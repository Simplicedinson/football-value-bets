"""
Database connection utilities for the Football Value Bets project.
"""

import os

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine


load_dotenv()


def get_database_url() -> str:
    """
    Return the database URL from environment variables.
    """
    database_url = os.getenv("DATABASE_URL")

    if not database_url:
        raise RuntimeError("DATABASE_URL is missing in the .env file.")

    return database_url.strip()


def create_database_engine() -> Engine:
    """
    Create and return a SQLAlchemy database engine.
    """
    return create_engine(
        get_database_url(),
        pool_pre_ping=True,
    )


def test_database_connection() -> None:
    """
    Test the database connection with a simple SQL query.
    """
    engine = create_database_engine()

    with engine.connect() as connection:
        result = connection.execute(text("SELECT current_database(), current_user;"))
        row = result.fetchone()

        print("Database connection successful.")
        print(f"Database: {row[0]}")
        print(f"User: {row[1]}")