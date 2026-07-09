"""
Check OddsPortal data saved in PostgreSQL.

Run:
    python -m app.db.check_oddsportal_data
"""

from __future__ import annotations

import os
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import psycopg
from dotenv import load_dotenv


def ensure_sslmode(database_url: str) -> str:
    """Add sslmode=require if missing."""
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
        raise RuntimeError("DATABASE_URL is missing.")

    return ensure_sslmode(database_url)


def main() -> None:
    """Print a small OddsPortal database check."""
    database_url = get_database_url()

    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM oddsportal_fixtures;")
            fixtures_count = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM oddsportal_prices;")
            prices_count = cursor.fetchone()[0]

            cursor.execute(
                """
                SELECT
                    f.league_name,
                    f.kickoff_time,
                    f.home_team,
                    f.away_team,
                    p.market_key,
                    p.period_key,
                    p.line_value,
                    p.bookmaker,
                    p.outcome_name,
                    p.odd_value
                FROM oddsportal_prices p
                JOIN oddsportal_fixtures f ON f.id = p.fixture_id
                ORDER BY
                    f.match_date DESC,
                    f.kickoff_time,
                    f.home_team,
                    p.market_key,
                    p.line_value NULLS FIRST,
                    p.outcome_name
                LIMIT 30;
                """
            )
            rows = cursor.fetchall()

    print("=" * 80)
    print("OddsPortal DB Check")
    print("=" * 80)
    print(f"Fixtures: {fixtures_count}")
    print(f"Prices: {prices_count}")
    print("-" * 80)

    for row in rows:
        (
            league_name,
            kickoff_time,
            home_team,
            away_team,
            market_key,
            period_key,
            line_value,
            bookmaker,
            outcome_name,
            odd_value,
        ) = row

        print(
            f"{league_name} | {kickoff_time} | {home_team} vs {away_team} | "
            f"{market_key} | {period_key} | line={line_value} | "
            f"{bookmaker} | {outcome_name}={odd_value}"
        )

    print("=" * 80)


if __name__ == "__main__":
    main()
