"""
Load simplified OddsPortal JSON into PostgreSQL.

Supported inputs:
    1. ODDSPORTAL_INPUT_JSON
       Example:
           data/raw/oddsportal/football_matches_by_league_20260711.json

    2. ODDSPORTAL_TARGET_DATE
       Example:
           20260711
           2026-07-11

       This resolves to:
           data/raw/oddsportal/football_matches_by_league_YYYYMMDD.json

    3. Fallback:
       data/raw/oddsportal/football_matches_by_league_simple.json

Run:
    python -m app.etl.load_oddsportal_simple_to_db

Notes:
    - Fixtures are inserted even when odds are empty.
    - Existing prices for the same fixture are deleted before inserting fresh prices.
    - match_date is read from the JSON when available.
    - If match_date is missing, ODDSPORTAL_TARGET_DATE is used as fallback.
"""

from __future__ import annotations

import json
import os
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import psycopg
from dotenv import load_dotenv

load_dotenv()   

ODDSPORTAL_RAW_DIR = Path("data/raw/oddsportal")


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
    """
    Add sslmode=require if it is missing.
    """
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
    """
    Read DATABASE_URL from .env.
    """
    load_dotenv()
    database_url = os.getenv("DATABASE_URL")

    if not database_url:
        raise RuntimeError("DATABASE_URL is missing in your .env file.")

    return ensure_sslmode(database_url)


def normalize_target_date(value: str) -> str:
    """
    Normalize a target date to YYYYMMDD.

    Accepted formats:
        20260711
        2026-07-11
    """
    return value.strip().replace("-", "")


def parse_date_value(value: Any) -> Optional[date]:
    """
    Parse a date value.

    Accepted formats:
        2026-07-11
        20260711
    """
    if value is None:
        return None

    value_as_text = str(value).strip()

    if not value_as_text:
        return None

    for date_format in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(value_as_text, date_format).date()
        except ValueError:
            continue

    return None


def resolve_input_json() -> Path:
    """
    Resolve the OddsPortal JSON file to load.

    Priority:
        1. ODDSPORTAL_INPUT_JSON
        2. ODDSPORTAL_TARGET_DATE -> football_matches_by_league_YYYYMMDD.json
        3. football_matches_by_league_simple.json
        4. latest football_matches_by_league_*.json
    """
    explicit_input = os.getenv("ODDSPORTAL_INPUT_JSON", "").strip()

    if explicit_input:
        return Path(explicit_input)

    target_date = os.getenv("ODDSPORTAL_TARGET_DATE", "").strip()

    if target_date:
        normalized_date = normalize_target_date(target_date)
        return ODDSPORTAL_RAW_DIR / f"football_matches_by_league_{normalized_date}.json"

    simple_json = ODDSPORTAL_RAW_DIR / "football_matches_by_league_simple.json"

    if simple_json.exists():
        return simple_json

    dated_files = sorted(
        ODDSPORTAL_RAW_DIR.glob("football_matches_by_league_*.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )

    dated_files = [
        path
        for path in dated_files
        if "parallel" not in path.name
    ]

    if dated_files:
        return dated_files[0]

    return simple_json


def parse_scraped_at(value: Any) -> datetime:
    """
    Parse ISO timestamp from JSON.
    """
    if not value:
        return datetime.now().astimezone()

    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def resolve_match_date(match_data: dict[str, Any], scraped_at: datetime) -> date:
    """
    Resolve match_date for one match.

    Priority:
        1. match_data["match_date"]
        2. ODDSPORTAL_TARGET_DATE
        3. scraped_at.date()
    """
    match_date = parse_date_value(match_data.get("match_date"))

    if match_date is not None:
        return match_date

    target_date = parse_date_value(os.getenv("ODDSPORTAL_TARGET_DATE", ""))

    if target_date is not None:
        return target_date

    return scraped_at.date()


def normalize_line_value(value: Any) -> Optional[float]:
    """
    Convert line values like '+2.5' to float.
    """
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
    """
    Convert odds to float and reject invalid values.
    """
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
        if re.search(re.escape(bookmaker), text, flags=re.IGNORECASE):
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
    """
    Create staging tables.
    """
    with connection.cursor() as cursor:
        cursor.execute(CREATE_TABLES_SQL)


def upsert_fixture(
    connection: psycopg.Connection,
    league_name: str,
    match_date: date,
    kickoff_time: str,
    home_team: str,
    away_team: str,
    scraped_at: datetime,
) -> int:
    """
    Insert or update one fixture and return its ID.
    """
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
                match_date,
                kickoff_time,
                home_team,
                away_team,
                scraped_at,
            ),
        )
        return int(cursor.fetchone()[0])


def delete_existing_prices(connection: psycopg.Connection, fixture_id: int) -> None:
    """
    Delete old odds for this fixture before inserting fresh odds.
    """
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
    """
    Insert one odds price.
    """
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
    """
    Insert all outcome values from one odds block.
    """
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
    """
    Insert all odds for one match.
    """
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
    """
    Load simplified OddsPortal JSON into PostgreSQL.
    """
    if not input_json.exists():
        raise FileNotFoundError(f"Input JSON not found: {input_json}")

    data = json.loads(input_json.read_text(encoding="utf-8"))

    fixture_count = 0
    price_count = 0

    for league in data:
        league_name = str(league.get("league_name", "")).strip()

        if not league_name:
            league_name = "Unknown League"

        for match_data in league.get("matches", []):
            if not isinstance(match_data, dict):
                continue

            scraped_at = parse_scraped_at(match_data.get("scraped_at", ""))
            match_date = resolve_match_date(match_data, scraped_at)

            fixture_id = upsert_fixture(
                connection=connection,
                league_name=league_name,
                match_date=match_date,
                kickoff_time=str(match_data.get("kickoff_time", "")).strip(),
                home_team=str(match_data.get("home_team", "")).strip(),
                away_team=str(match_data.get("away_team", "")).strip(),
                scraped_at=scraped_at,
            )

            delete_existing_prices(connection, fixture_id)

            odds = match_data.get("odds", {})

            if isinstance(odds, dict) and odds:
                price_count += insert_match_odds(
                    connection=connection,
                    fixture_id=fixture_id,
                    odds=odds,
                    scraped_at=scraped_at,
                )

            fixture_count += 1

    return fixture_count, price_count


def print_summary(
    connection: psycopg.Connection,
    input_json: Path,
    fixture_count: int,
    price_count: int,
) -> None:
    """
    Print import summary and global DB counts.
    """
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
            SELECT match_date, COUNT(*)
            FROM oddsportal_fixtures
            GROUP BY match_date
            ORDER BY match_date DESC;
            """
        )
        fixture_counts_by_date = cursor.fetchall()

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
    print(f"Resolved input JSON: {input_json}")
    print(f"Fixtures loaded from this file: {fixture_count}")
    print(f"Prices inserted from this file: {price_count}")
    print(f"Total fixtures in DB: {total_fixtures}")
    print(f"Total prices in DB: {total_prices}")
    print("-" * 80)
    print("Fixtures by date:")

    for match_date, count in fixture_counts_by_date:
        print(f"{match_date}: {count}")

    print("-" * 80)
    print("Markets:")

    for market_key, count in market_counts:
        print(f"{market_key}: {count}")

    print("-" * 80)
    print("Bookmakers:")

    for bookmaker, count in bookmaker_counts:
        print(f"{bookmaker}: {count}")

    print("=" * 80)


def main() -> None:
    """
    Main entry point.
    """
    input_json = resolve_input_json()
    database_url = get_database_url()

    print("=" * 80)
    print("Football Value Bets - OddsPortal Loader")
    print("=" * 80)
    print(f"Resolved input JSON: {input_json}")
    print(f"ODDSPORTAL_TARGET_DATE: {os.getenv('ODDSPORTAL_TARGET_DATE', '')}")
    print(f"ODDSPORTAL_INPUT_JSON: {os.getenv('ODDSPORTAL_INPUT_JSON', '')}")
    print("=" * 80)

    with psycopg.connect(database_url) as connection:
        create_tables(connection)

        fixture_count, price_count = load_json_to_database(
            connection=connection,
            input_json=input_json,
        )

        connection.commit()

        print_summary(
            connection=connection,
            input_json=input_json,
            fixture_count=fixture_count,
            price_count=price_count,
        )


if __name__ == "__main__":
    main()