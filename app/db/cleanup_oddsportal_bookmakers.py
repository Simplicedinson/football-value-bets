"""
Clean existing OddsPortal bookmaker names in PostgreSQL.

Run:
    python -m app.db.cleanup_oddsportal_bookmakers
"""

from __future__ import annotations

import os
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import psycopg
from dotenv import load_dotenv


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


def cleanup_bookmakers(connection: psycopg.Connection) -> int:
    """
    Normalize bookmaker names already stored in oddsportal_prices.
    """
    query = """
    UPDATE oddsportal_prices
    SET bookmaker = CASE
        WHEN bookmaker ILIKE '%Bet365.de%' THEN 'Bet365.de'
        WHEN bookmaker ILIKE '%bet-at-home.de%' THEN 'bet-at-home.de'
        WHEN bookmaker ILIKE '%Betano.de%' THEN 'Betano.de'
        WHEN bookmaker ILIKE '%Interwetten.de%' THEN 'Interwetten.de'
        WHEN bookmaker ILIKE '%Neobet%' THEN 'Neobet'
        WHEN bookmaker ILIKE '%Oddset.de%' THEN 'Oddset.de'
        WHEN bookmaker ILIKE '%Winamax.de%' THEN 'Winamax.de'
        ELSE bookmaker
    END
    WHERE bookmaker ILIKE 'Bookmakers%Payout%';
    """

    with connection.cursor() as cursor:
        cursor.execute(query)
        return cursor.rowcount


def print_bookmaker_summary(connection: psycopg.Connection) -> None:
    """
    Print bookmaker counts after cleanup.
    """
    query = """
    SELECT bookmaker, COUNT(*)
    FROM oddsportal_prices
    GROUP BY bookmaker
    ORDER BY bookmaker;
    """

    with connection.cursor() as cursor:
        cursor.execute(query)
        rows = cursor.fetchall()

    print("=" * 80)
    print("Bookmakers after cleanup")
    print("=" * 80)

    for bookmaker, count in rows:
        print(f"{bookmaker}: {count}")

    print("=" * 80)


def main() -> None:
    """
    Main entry point.
    """
    database_url = get_database_url()

    with psycopg.connect(database_url) as connection:
        updated_rows = cleanup_bookmakers(connection)
        connection.commit()

        print("=" * 80)
        print("OddsPortal bookmaker cleanup completed")
        print("=" * 80)
        print(f"Updated rows: {updated_rows}")

        print_bookmaker_summary(connection)


if __name__ == "__main__":
    main()