"""
Load OddsPortal live football matches into PostgreSQL.

Input:
    data/raw/oddsportal/live_matches.json

Run:
    python -m app.etl.load_oddsportal_live_to_db

Purpose:
    - Update live scores and match status in oddsportal_fixtures.
    - Insert/update live 1X2 odds in oddsportal_prices with period_key='live'.
    - Keep this loader lightweight for frequent live refreshes.
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import psycopg
from dotenv import load_dotenv


DEFAULT_INPUT_JSON = Path("data/raw/oddsportal/live_matches.json")


CREATE_AND_MIGRATE_SQL = """
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

ALTER TABLE oddsportal_fixtures
ADD COLUMN IF NOT EXISTS home_score INTEGER;

ALTER TABLE oddsportal_fixtures
ADD COLUMN IF NOT EXISTS away_score INTEGER;

ALTER TABLE oddsportal_fixtures
ADD COLUMN IF NOT EXISTS match_status TEXT NOT NULL DEFAULT 'scheduled';

ALTER TABLE oddsportal_fixtures
ADD COLUMN IF NOT EXISTS live_minute TEXT;

ALTER TABLE oddsportal_fixtures
ADD COLUMN IF NOT EXISTS result TEXT;

CREATE INDEX IF NOT EXISTS idx_oddsportal_fixtures_match_date
ON oddsportal_fixtures (match_date);

CREATE INDEX IF NOT EXISTS idx_oddsportal_fixtures_live
ON oddsportal_fixtures (match_date, match_status);

CREATE INDEX IF NOT EXISTS idx_oddsportal_prices_fixture
ON oddsportal_prices (fixture_id);

CREATE INDEX IF NOT EXISTS idx_oddsportal_prices_market
ON oddsportal_prices (market_key, period_key, line_value);
"""


LIVE_PRICE_DELETE_SQL = """
DELETE FROM oddsportal_prices
WHERE fixture_id = %s
AND market_key = '1x2'
AND period_key = 'live';
"""


LIVE_PRICE_INSERT_SQL = """
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
VALUES (%s, '1x2', 'live', NULL, %s, %s, %s, %s);
"""


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
    Read DATABASE_URL from environment variables.
    """
    load_dotenv()
    database_url = os.getenv("DATABASE_URL")

    if not database_url:
        raise RuntimeError("DATABASE_URL is missing in your .env file.")

    return ensure_sslmode(database_url)


def resolve_input_json() -> Path:
    """
    Resolve the live JSON input file.
    """
    explicit_input = os.getenv("ODDSPORTAL_LIVE_INPUT_JSON", "").strip()

    if explicit_input:
        return Path(explicit_input)

    return DEFAULT_INPUT_JSON


def parse_scraped_at(value: Any) -> datetime:
    """
    Parse ISO timestamp from JSON.
    """
    if not value:
        return datetime.now().astimezone()

    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def parse_match_date(value: Any) -> date:
    """
    Parse match_date from JSON.
    """
    if not value:
        return datetime.now().astimezone().date()

    return datetime.strptime(str(value), "%Y-%m-%d").date()


def parse_int(value: Any) -> Optional[int]:
    """
    Convert a value to integer safely.
    """
    if value is None or value == "":
        return None

    try:
        return int(value)
    except (TypeError, ValueError):
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


def normalize_text(value: Any, fallback: str = "") -> str:
    """
    Convert a value to a clean text.
    """
    text = str(value or "").strip()
    return text or fallback


def normalize_status(value: Any) -> str:
    """
    Normalize live match status.
    """
    status = normalize_text(value, fallback="unknown").lower()

    allowed_statuses = {
        "scheduled",
        "live",
        "halftime",
        "finished",
        "postponed",
        "cancelled",
        "unknown",
    }

    if status in allowed_statuses:
        return status

    return "unknown"


def calculate_result(
    home_score: Optional[int],
    away_score: Optional[int],
    match_status: str,
) -> Optional[str]:
    """
    Calculate the final result only when the match is finished.
    """
    if match_status != "finished":
        return None

    if home_score is None or away_score is None:
        return None

    if home_score > away_score:
        return "home_win"

    if home_score < away_score:
        return "away_win"

    return "draw"


def create_or_migrate_tables(connection: psycopg.Connection) -> None:
    """
    Create required tables and live columns if they are missing.
    """
    with connection.cursor() as cursor:
        cursor.execute(CREATE_AND_MIGRATE_SQL)


def find_existing_fixture_id(
    connection: psycopg.Connection,
    match_date: date,
    home_team: str,
    away_team: str,
) -> Optional[int]:
    """
    Find an existing fixture by date and teams.

    We intentionally do not require league_name here because the normal scraper
    and the live page can use slightly different league labels.
    """
    query = """
    SELECT id
    FROM oddsportal_fixtures
    WHERE match_date = %s
    AND home_team = %s
    AND away_team = %s
    ORDER BY updated_at DESC, created_at DESC
    LIMIT 1;
    """

    with connection.cursor() as cursor:
        cursor.execute(query, (match_date, home_team, away_team))
        row = cursor.fetchone()

    if not row:
        return None

    return int(row[0])


def update_fixture_live_fields(
    connection: psycopg.Connection,
    fixture_id: int,
    scraped_at: datetime,
    home_score: Optional[int],
    away_score: Optional[int],
    match_status: str,
    live_minute: Optional[str],
    result: Optional[str],
) -> int:
    """
    Update live fields on an existing fixture.
    """
    query = """
    UPDATE oddsportal_fixtures
    SET
        scraped_at = %s,
        home_score = %s,
        away_score = %s,
        match_status = %s,
        live_minute = %s,
        result = %s,
        updated_at = NOW()
    WHERE id = %s;
    """

    with connection.cursor() as cursor:
        cursor.execute(
            query,
            (
                scraped_at,
                home_score,
                away_score,
                match_status,
                live_minute,
                result,
                fixture_id,
            ),
        )
        return cursor.rowcount


def insert_live_only_fixture(
    connection: psycopg.Connection,
    league_name: str,
    match_date: date,
    scraped_at: datetime,
    home_team: str,
    away_team: str,
    home_score: Optional[int],
    away_score: Optional[int],
    match_status: str,
    live_minute: Optional[str],
    result: Optional[str],
) -> int:
    """
    Insert a live-only fixture when it does not exist from the normal scraper.
    """
    query = """
    INSERT INTO oddsportal_fixtures (
        league_name,
        match_date,
        kickoff_time,
        home_team,
        away_team,
        scraped_at,
        home_score,
        away_score,
        match_status,
        live_minute,
        result
    )
    VALUES (%s, %s, 'LIVE', %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT ON CONSTRAINT uq_oddsportal_fixtures_match
    DO UPDATE SET
        scraped_at = EXCLUDED.scraped_at,
        home_score = EXCLUDED.home_score,
        away_score = EXCLUDED.away_score,
        match_status = EXCLUDED.match_status,
        live_minute = EXCLUDED.live_minute,
        result = EXCLUDED.result,
        updated_at = NOW()
    RETURNING id;
    """

    with connection.cursor() as cursor:
        cursor.execute(
            query,
            (
                league_name,
                match_date,
                home_team,
                away_team,
                scraped_at,
                home_score,
                away_score,
                match_status,
                live_minute,
                result,
            ),
        )
        return int(cursor.fetchone()[0])


def upsert_live_fixture(
    connection: psycopg.Connection,
    live_match: dict[str, Any],
) -> int:
    """
    Insert or update one live fixture and return its ID.
    """
    scraped_at = parse_scraped_at(live_match.get("scraped_at"))
    match_date = parse_match_date(live_match.get("match_date"))
    league_name = normalize_text(live_match.get("league_name"), "Unknown Live League")
    home_team = normalize_text(live_match.get("home_team"), "Unknown Home Team")
    away_team = normalize_text(live_match.get("away_team"), "Unknown Away Team")
    home_score = parse_int(live_match.get("home_score"))
    away_score = parse_int(live_match.get("away_score"))
    match_status = normalize_status(live_match.get("match_status"))
    live_minute = live_match.get("live_minute")
    result = calculate_result(home_score, away_score, match_status)

    existing_fixture_id = find_existing_fixture_id(
        connection=connection,
        match_date=match_date,
        home_team=home_team,
        away_team=away_team,
    )

    if existing_fixture_id is not None:
        update_fixture_live_fields(
            connection=connection,
            fixture_id=existing_fixture_id,
            scraped_at=scraped_at,
            home_score=home_score,
            away_score=away_score,
            match_status=match_status,
            live_minute=live_minute,
            result=result,
        )
        return existing_fixture_id

    return insert_live_only_fixture(
        connection=connection,
        league_name=league_name,
        match_date=match_date,
        scraped_at=scraped_at,
        home_team=home_team,
        away_team=away_team,
        home_score=home_score,
        away_score=away_score,
        match_status=match_status,
        live_minute=live_minute,
        result=result,
    )


def delete_existing_live_prices(
    connection: psycopg.Connection,
    fixture_id: int,
) -> None:
    """
    Delete old live 1X2 prices for a fixture before inserting fresh ones.
    """
    with connection.cursor() as cursor:
        cursor.execute(LIVE_PRICE_DELETE_SQL, (fixture_id,))


def insert_live_prices(
    connection: psycopg.Connection,
    fixture_id: int,
    odds: dict[str, Any],
    scraped_at: datetime,
) -> int:
    """
    Insert live 1X2 prices for one fixture.
    """
    inserted_count = 0

    live_1x2 = (
        odds.get("1x2", {})
        .get("live", {})
    )

    if not isinstance(live_1x2, dict):
        return inserted_count

    bookmaker = normalize_text(live_1x2.get("bookmaker"), "summary")
    values = live_1x2.get("values", {})

    if not isinstance(values, dict):
        return inserted_count

    delete_existing_live_prices(connection, fixture_id)

    with connection.cursor() as cursor:
        for outcome_name, raw_value in values.items():
            odd_value = normalize_odd_value(raw_value)

            if odd_value is None:
                continue

            cursor.execute(
                LIVE_PRICE_INSERT_SQL,
                (
                    fixture_id,
                    bookmaker,
                    outcome_name,
                    odd_value,
                    scraped_at,
                ),
            )
            inserted_count += 1

    return inserted_count


def load_live_json_to_database(
    connection: psycopg.Connection,
    input_json: Path,
) -> tuple[int, int]:
    """
    Load live matches from JSON into PostgreSQL.
    """
    if not input_json.exists():
        raise FileNotFoundError(f"Input JSON not found: {input_json}")

    live_matches = json.loads(input_json.read_text(encoding="utf-8"))

    return load_live_matches_to_database(
        connection=connection,
        live_matches=live_matches,
    )


def load_live_matches_to_database(
    connection: psycopg.Connection,
    live_matches: list[dict[str, Any]],
) -> tuple[int, int]:
    """
    Load live matches that are already available in memory.
    """

    if not isinstance(live_matches, list):
        raise ValueError("Live JSON must contain a list of matches.")

    fixture_count = 0
    price_count = 0

    for live_match in live_matches:
        if not isinstance(live_match, dict):
            continue

        fixture_id = upsert_live_fixture(connection, live_match)
        scraped_at = parse_scraped_at(live_match.get("scraped_at"))
        odds = live_match.get("odds", {})

        if isinstance(odds, dict) and odds:
            price_count += insert_live_prices(
                connection=connection,
                fixture_id=fixture_id,
                odds=odds,
                scraped_at=scraped_at,
            )

        fixture_count += 1

    return fixture_count, price_count


def print_summary(
    database_url: str,
    input_json: Path,
    fixture_count: int,
    price_count: int,
) -> None:
    """
    Print a compact import summary.
    """
    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT COUNT(*)
                FROM oddsportal_fixtures
                WHERE match_status IN ('live', 'halftime');
                """
            )
            live_fixtures_count = cursor.fetchone()[0]

            cursor.execute(
                """
                SELECT match_status, COUNT(*)
                FROM oddsportal_fixtures
                WHERE match_date = CURRENT_DATE
                GROUP BY match_status
                ORDER BY match_status;
                """
            )
            status_counts = cursor.fetchall()

            cursor.execute(
                """
                SELECT league_name, home_team, home_score, away_score, away_team, live_minute
                FROM oddsportal_fixtures
                WHERE match_status IN ('live', 'halftime')
                ORDER BY league_name, live_minute NULLS LAST
                LIMIT 20;
                """
            )
            live_rows = cursor.fetchall()

    print("=" * 80)
    print("OddsPortal live JSON loaded into PostgreSQL")
    print("=" * 80)
    print(f"Input JSON: {input_json}")
    print(f"Live fixtures loaded from this file: {fixture_count}")
    print(f"Live prices inserted from this file: {price_count}")
    print(f"Current live fixtures in DB: {live_fixtures_count}")
    print("-" * 80)
    print("Today's fixture statuses:")

    for match_status, count in status_counts:
        print(f"{match_status}: {count}")

    print("-" * 80)
    print("Live matches preview:")

    for league_name, home_team, home_score, away_score, away_team, live_minute in live_rows:
        print(
            f"{live_minute or '-'} | {league_name} | "
            f"{home_team} {home_score}:{away_score} {away_team}"
        )

    print("=" * 80)


def main() -> None:
    """
    Main entry point.
    """
    database_url = get_database_url()
    input_json = resolve_input_json()

    print("=" * 80)
    print("Football Value Bets - OddsPortal Live Loader")
    print("=" * 80)
    print(f"Resolved input JSON: {input_json}")
    print(f"ODDSPORTAL_LIVE_INPUT_JSON: {os.getenv('ODDSPORTAL_LIVE_INPUT_JSON', '')}")
    print("=" * 80)

    with psycopg.connect(database_url) as connection:
        create_or_migrate_tables(connection)

        fixture_count, price_count = load_live_json_to_database(
            connection=connection,
            input_json=input_json,
        )

        connection.commit()

    print_summary(
        database_url=database_url,
        input_json=input_json,
        fixture_count=fixture_count,
        price_count=price_count,
    )


if __name__ == "__main__":
    main()
