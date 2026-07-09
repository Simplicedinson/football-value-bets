"""
Load simplified OddsPortal JSON into PostgreSQL.

Input:
    data/raw/oddsportal/football_matches_by_league_simple.json

Run:
    python -m app.etl.load_oddsportal_simple_to_db

Notes:
    - Existing prices for the same fixture are deleted before inserting fresh prices.
    - Bookmaker names are normalized before insertion.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import psycopg
from dotenv import load_dotenv


INPUT_JSON = Path("data/raw/oddsportal/football_matches_by_league_simple.json")


CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS oddsportal_fixtures (
    id BIGSERIAL PRIMARY KEY,
    league_name TEXT NOT NULL,
    match_date DATE NOT NULL,
    kickoff_time TEXT NOT NULL,
    home_team TEXT NOT NULL,
    away_team TEXT NOT NULL,
    scraped_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_oddsportal_fixtures_match UNIQUE (
        league_name,
        match_date,
        kickoff_time,
        home_team,
        away_team
    )
);

CREATE TABLE IF NOT EXISTS oddsportal_prices (
    id BIGSERIAL PRIMARY KEY,
    fixture_id BIGINT NOT NULL REFERENCES oddsportal_fixtures(id) ON DELETE CASCADE,
    market_key TEXT NOT NULL,
    period_key TEXT NOT NULL,
    line_value NUMERIC(6, 2),
    bookmaker TEXT NOT NULL,
    outcome_name TEXT NOT NULL,
    odd_value NUMERIC(10, 2) NOT NULL,
    scraped_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_oddsportal_fixtures_match_date
ON oddsportal_fixtures (match_date);

CREATE INDEX IF NOT EXISTS idx_oddsportal_prices_fixture
ON oddsportal_prices (fixture_id);

CREATE INDEX IF NOT EXISTS idx_oddsportal_prices_market
ON oddsportal_prices (market_key, period_key, line_value);

CREATE INDEX IF NOT EXISTS idx_oddsportal_prices_outcome
ON oddsportal_prices (outcome_name, odd_value);
"""


KNOWN_BOOKMAKERS = [
    "Bet365.de",
    "bet-at-home.de",
    "Betano.de",
    "Interwetten.de",
    "Neobet",
    "Oddset.de",
    "Winamax.de",
]


def ensure_sslmode(database_url: str) -> str:
    """Add sslmode=require if it is missing."""
    parsed_url = urlparse(database_url)
    query_params = dict(parse_qsl(parsed_url.query))

    if "sslmode" not in query_params:
        query_params["sslmode"] = "require"

    return urlunparse(
        (
            parsed_url.scheme,
            parsed_url.netloc,
            parsed_url.path,
            parsed_url.params,
            urlencode(query_params),
            parsed_url.fragment,
        )
    )


def get_database_url() -> str:
    """Read DATABASE_URL from .env."""
    load_dotenv()
    database_url = os.getenv("DATABASE_URL")

    if not database_url:
        raise RuntimeError("DATABASE_URL is missing in your .env file.")

    return ensure_sslmode(database_url)


def parse_scraped_at(value: str) -> datetime:
    """Parse ISO timestamp from JSON."""
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def normalize_line_value(value: Any) -> Optional[float]:
    """Convert line values like '+2.5' to float."""
    if value is None:
        return None

    value_as_text = str(value).strip().replace("+", "")

    if not value_as_text:
        return None

    try:
        return float(value_as_text)
    except ValueError:
        return None


def normalize_odd_value(value: Any) -> Optional[float]:
    """Convert odds to float and reject invalid values."""
    try:
        odd_value = float(value)
    except (TypeError, ValueError):
        return None

    if odd_value <= 1.0:
        return None

    return odd_value


def clean_bookmaker_name(value: Any) -> str:
    """
    Normalize bookmaker names before insertion.

    Examples:
        "Bookmakers 1 X 2 Payout bet-at-home.de" -> "bet-at-home.de"
        "Bookmakers Yes No Payout Bet365.de" -> "Bet365.de"
        "Bet365.de" -> "Bet365.de"
        "summary" -> "summary"
    """
    text = str(value or "").strip()

    if not text:
        return "unknown"

    if text.lower() in {"summary", "best", "unknown"}:
        return text.lower()

    for bookmaker in KNOWN_BOOKMAKERS:
        pattern = re.escape(bookmaker)
        if re.search(pattern, text, flags=re.IGNORECASE):
            return bookmaker

    if "Payout " in text:
        text = text.split("Payout ", 1)[1].strip()

    if " CLAIM BONUS" in text:
        text = text.split(" CLAIM BONUS", 1)[0].strip()

    tokens = text.split()

    if not tokens:
        return "unknown"

    return tokens[-1].strip()


def create_tables(connection: psycopg.Connection) -> None:
    """Create staging tables."""
    with connection.cursor() as cursor:
        cursor.execute(CREATE_TABLES_SQL)


def upsert_fixture(
    connection: psycopg.Connection,
    league_name: str,
    kickoff_time: str,
    home_team: str,
    away_team: str,
    scraped_at: datetime,
) -> int:
    """Insert or update one fixture and return its ID."""
    query = """
    INSERT INTO oddsportal_fixtures (
        league_name,
        match_date,
        kickoff_time,
        home_team,
        away_team,
        scraped_at
    )
    VALUES (%s, %s, %s, %s, %s, %s)
    ON CONFLICT ON CONSTRAINT uq_oddsportal_fixtures_match
    DO UPDATE SET
        scraped_at = EXCLUDED.scraped_at,
        updated_at = NOW()
    RETURNING id;
    """

    with connection.cursor() as cursor:
        cursor.execute(
            query,
            (
                league_name,
                scraped_at.date(),
                kickoff_time,
                home_team,
                away_team,
                scraped_at,
            ),
        )
        return int(cursor.fetchone()[0])


def delete_existing_prices(connection: psycopg.Connection, fixture_id: int) -> None:
    """Delete old odds for this fixture before inserting fresh odds."""
    with connection.cursor() as cursor:
        cursor.execute(
            "DELETE FROM oddsportal_prices WHERE fixture_id = %s;",
            (fixture_id,),
        )


def insert_price(
    connection: psycopg.Connection,
    fixture_id: int,
    market_key: str,
    period_key: str,
    line_value: Optional[float],
    bookmaker: str,
    outcome_name: str,
    odd_value: float,
    scraped_at: datetime,
) -> None:
    """Insert one odds price."""
    query = """
    INSERT INTO oddsportal_prices (
        fixture_id,
        market_key,
        period_key,
        line_value,
        bookmaker,
        outcome_name,
        odd_value,
        scraped_at
    )
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s);
    """

    with connection.cursor() as cursor:
        cursor.execute(
            query,
            (
                fixture_id,
                market_key,
                period_key,
                line_value,
                clean_bookmaker_name(bookmaker),
                outcome_name,
                odd_value,
                scraped_at,
            ),
        )


def insert_values(
    connection: psycopg.Connection,
    fixture_id: int,
    market_key: str,
    period_key: str,
    line_value: Optional[float],
    bookmaker: str,
    values: dict[str, Any],
    scraped_at: datetime,
) -> int:
    """Insert all outcome values from one odds block."""
    inserted_count = 0

    for outcome_name, raw_odd_value in values.items():
        odd_value = normalize_odd_value(raw_odd_value)

        if odd_value is None:
            continue

        insert_price(
            connection=connection,
            fixture_id=fixture_id,
            market_key=market_key,
            period_key=period_key,
            line_value=line_value,
            bookmaker=bookmaker,
            outcome_name=outcome_name,
            odd_value=odd_value,
            scraped_at=scraped_at,
        )

        inserted_count += 1

    return inserted_count


def insert_match_odds(
    connection: psycopg.Connection,
    fixture_id: int,
    odds: dict[str, Any],
    scraped_at: datetime,
) -> int:
    """Insert all odds for one match."""
    inserted_count = 0

    for market_key, periods in odds.items():
        if not isinstance(periods, dict):
            continue

        for period_key, period_data in periods.items():
            if not isinstance(period_data, dict):
                continue

            values = period_data.get("values")

            if isinstance(values, dict):
                inserted_count += insert_values(
                    connection=connection,
                    fixture_id=fixture_id,
                    market_key=market_key,
                    period_key=period_key,
                    line_value=None,
                    bookmaker=period_data.get("bookmaker") or "unknown",
                    values=values,
                    scraped_at=scraped_at,
                )

            lines = period_data.get("lines")

            if isinstance(lines, list):
                for line_row in lines:
                    if not isinstance(line_row, dict):
                        continue

                    line_values = line_row.get("values")

                    if not isinstance(line_values, dict):
                        continue

                    inserted_count += insert_values(
                        connection=connection,
                        fixture_id=fixture_id,
                        market_key=market_key,
                        period_key=period_key,
                        line_value=normalize_line_value(line_row.get("line")),
                        bookmaker=line_row.get("bookmaker")
                        or period_data.get("bookmaker")
                        or "summary",
                        values=line_values,
                        scraped_at=scraped_at,
                    )

    return inserted_count


def load_json_to_database(connection: psycopg.Connection, input_json: Path) -> tuple[int, int]:
    """Load simplified OddsPortal JSON into PostgreSQL."""
    if not input_json.exists():
        raise FileNotFoundError(f"Input JSON not found: {input_json}")

    data = json.loads(input_json.read_text(encoding="utf-8"))

    fixture_count = 0
    price_count = 0

    for league in data:
        league_name = league.get("league_name", "")

        for match in league.get("matches", []):
            odds = match.get("odds", {})

            if not odds:
                continue

            scraped_at = parse_scraped_at(match.get("scraped_at", ""))

            fixture_id = upsert_fixture(
                connection=connection,
                league_name=league_name,
                kickoff_time=match.get("kickoff_time", ""),
                home_team=match.get("home_team", ""),
                away_team=match.get("away_team", ""),
                scraped_at=scraped_at,
            )

            delete_existing_prices(connection, fixture_id)

            price_count += insert_match_odds(
                connection=connection,
                fixture_id=fixture_id,
                odds=odds,
                scraped_at=scraped_at,
            )

            fixture_count += 1

    return fixture_count, price_count


def print_summary(connection: psycopg.Connection, fixture_count: int, price_count: int) -> None:
    """Print import summary and global DB counts."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) FROM oddsportal_fixtures;")
        total_fixtures = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM oddsportal_prices;")
        total_prices = cursor.fetchone()[0]

        cursor.execute(
            """
            SELECT market_key, COUNT(*)
            FROM oddsportal_prices
            GROUP BY market_key
            ORDER BY market_key;
            """
        )
        market_counts = cursor.fetchall()

        cursor.execute(
            """
            SELECT bookmaker, COUNT(*)
            FROM oddsportal_prices
            GROUP BY bookmaker
            ORDER BY bookmaker;
            """
        )
        bookmaker_counts = cursor.fetchall()

    print("=" * 80)
    print("OddsPortal JSON loaded into PostgreSQL")
    print("=" * 80)
    print(f"Input JSON: {INPUT_JSON}")
    print(f"Fixtures loaded from this file: {fixture_count}")
    print(f"Prices inserted from this file: {price_count}")
    print(f"Total fixtures in DB: {total_fixtures}")
    print(f"Total prices in DB: {total_prices}")
    print("-" * 80)

    for market_key, count in market_counts:
        print(f"{market_key}: {count}")

    print("-" * 80)
    print("Bookmakers:")

    for bookmaker, count in bookmaker_counts:
        print(f"{bookmaker}: {count}")

    print("=" * 80)


def main() -> None:
    """Main entry point."""
    database_url = get_database_url()

    with psycopg.connect(database_url) as connection:
        create_tables(connection)

        fixture_count, price_count = load_json_to_database(
            connection=connection,
            input_json=INPUT_JSON,
        )

        connection.commit()

        print_summary(
            connection=connection,
            fixture_count=fixture_count,
            price_count=price_count,
        )


if __name__ == "__main__":
    main()
