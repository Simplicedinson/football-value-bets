"""
Main entry point for the Football Value Bets project.
"""

from app.db.connection import test_database_connection


def main() -> None:
    """
    Run a basic database connection test.
    """
    test_database_connection()


if __name__ == "__main__":
    main()