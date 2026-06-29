"""
Schema validation script for the Football Value Bets database.

This script checks whether all required PostgreSQL tables exist
in the public schema.
"""

import sys
from sqlalchemy import text

from app.db.connection import create_database_engine


REQUIRED_TABLES = {
    "leagues",
    "seasons",
    "teams",
    "team_aliases",
    "players",
    "player_aliases",
    "player_team_history",
    "fixtures",
    "matches",
    "player_match_stats",
    "bookmakers",
    "market_odds",
    "model_predictions",
    "value_bets",
    "import_logs",
}


def fetch_existing_tables() -> set[str]:
    """
    Fetch all existing table names from the public PostgreSQL schema.
    """
    engine = create_database_engine()

    query = text(
        """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'public'
          AND table_type = 'BASE TABLE'
        ORDER BY table_name;
        """
    )

    with engine.connect() as connection:
        result = connection.execute(query)
        return {row[0] for row in result.fetchall()}


def check_required_tables() -> bool:
    """
    Check whether all required tables exist in the database.
    """
    existing_tables = fetch_existing_tables()

    missing_tables = REQUIRED_TABLES - existing_tables
    extra_tables = existing_tables - REQUIRED_TABLES

    print("=" * 60)
    print("Football Value Bets - Database Schema Check")
    print("=" * 60)

    print(f"Required tables: {len(REQUIRED_TABLES)}")
    print(f"Existing tables: {len(existing_tables)}")
    print()

    if missing_tables:
        print("Missing tables:")
        for table_name in sorted(missing_tables):
            print(f"  - {table_name}")
    else:
        print("All required tables exist.")

    print()

    if extra_tables:
        print("Extra tables found:")
        for table_name in sorted(extra_tables):
            print(f"  - {table_name}")
    else:
        print("No extra tables found.")

    print("=" * 60)

    return not missing_tables


def main() -> None:
    """
    Run the schema validation script.
    """
    schema_is_valid = check_required_tables()

    if not schema_is_valid:
        print("Schema check failed.")
        sys.exit(1)

    print("Schema check successful.")
    sys.exit(0)


if __name__ == "__main__":
    main()