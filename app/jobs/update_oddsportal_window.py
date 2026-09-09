"""
Update the OddsPortal data window.

Window managed by this job:
    - History: today - 1 day to yesterday
    - Today: today
    - Next games: tomorrow to today + 1 day

Normal daily behavior:
    1. Delete fixtures outside the active window.
    2. Refresh today's odds.
    3. Scrape today + 1 day to extend the future window.
    4. Scrape missing future dates if the database has gaps.
    5. Load every scraped JSON file into PostgreSQL.

Run:
    python -m app.jobs.update_oddsportal_window

Useful environment variables:
    ODDSPORTAL_MAX_PARALLEL_MATCHES=32
    ODDSPORTAL_FORCE_FULL_WINDOW=1
    ODDSPORTAL_BACKFILL_PAST_DAYS=1
    ODDSPORTAL_REQUEST_DELAY_SECONDS=0.3
    ODDSPORTAL_MARKET_RENDER_WAIT_MS=3000
    ODDSPORTAL_SCROLL_RENDER_WAIT_MS=400
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from zoneinfo import ZoneInfo

import psycopg
from dotenv import load_dotenv


PAST_DAYS_TO_KEEP = 1
FUTURE_DAYS_TO_KEEP = 1
DEFAULT_PARALLEL_WORKERS = "32"

LOCK_DIR = Path("data/runtime")
LOCK_FILE = LOCK_DIR / "oddsportal_update.lock"
LOCK_MAX_AGE_MINUTES = 180


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


def get_today() -> date:
    """
    Return today's date in Europe/Berlin.
    """
    return datetime.now(ZoneInfo("Europe/Berlin")).date()


def iter_dates(start_date: date, end_date: date) -> list[date]:
    """
    Return all dates between start_date and end_date included.
    """
    dates: list[date] = []
    current_date = start_date

    while current_date <= end_date:
        dates.append(current_date)
        current_date += timedelta(days=1)

    return dates


def is_enabled_env(name: str) -> bool:
    """
    Read a boolean-like environment variable.
    """
    return os.getenv(name, "0").strip().lower() in {"1", "true", "yes", "y"}


def acquire_lock() -> None:
    """
    Prevent two update jobs from running at the same time.
    """
    LOCK_DIR.mkdir(parents=True, exist_ok=True)

    if LOCK_FILE.exists():
        lock_age = datetime.now() - datetime.fromtimestamp(LOCK_FILE.stat().st_mtime)
        lock_age_minutes = lock_age.total_seconds() / 60

        if lock_age_minutes <= LOCK_MAX_AGE_MINUTES:
            raise RuntimeError(
                "Another OddsPortal update seems to be running. "
                f"Lock file: {LOCK_FILE}"
            )

        print(f"Removing stale lock file: {LOCK_FILE}")
        LOCK_FILE.unlink(missing_ok=True)

    LOCK_FILE.write_text(
        datetime.now(ZoneInfo("Europe/Berlin")).isoformat(),
        encoding="utf-8",
    )


def release_lock() -> None:
    """
    Remove the update lock file.
    """
    LOCK_FILE.unlink(missing_ok=True)


def fetch_existing_fixture_dates(start_date: date, end_date: date) -> set[date]:
    """
    Fetch dates already present in oddsportal_fixtures.
    """
    query = """
    SELECT DISTINCT match_date
    FROM oddsportal_fixtures
    WHERE match_date BETWEEN %s AND %s;
    """

    database_url = get_database_url()

    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, (start_date, end_date))
            rows = cursor.fetchall()

    return {row[0] for row in rows}


def print_existing_dates(start_date: date, end_date: date) -> None:
    """
    Print fixture counts by date for the active window.
    """
    query = """
    SELECT match_date, COUNT(*)
    FROM oddsportal_fixtures
    WHERE match_date BETWEEN %s AND %s
    GROUP BY match_date
    ORDER BY match_date;
    """

    database_url = get_database_url()

    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, (start_date, end_date))
            rows = cursor.fetchall()

    print("-" * 80)
    print("Fixtures currently in active window:")

    if not rows:
        print("No fixtures found in active window.")
    else:
        for fixture_date, fixture_count in rows:
            print(f"{fixture_date}: {fixture_count}")

    print("-" * 80)


def cleanup_old_oddsportal_data(start_date: date, end_date: date) -> tuple[int, int]:
    """
    Delete OddsPortal fixtures and prices outside the active window.
    """
    delete_prices_query = """
    DELETE FROM oddsportal_prices p
    USING oddsportal_fixtures f
    WHERE p.fixture_id = f.id
    AND (
        f.match_date < %s
        OR f.match_date > %s
    );
    """

    delete_fixtures_query = """
    DELETE FROM oddsportal_fixtures
    WHERE match_date < %s
    OR match_date > %s;
    """

    database_url = get_database_url()

    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(delete_prices_query, (start_date, end_date))
            deleted_prices = cursor.rowcount

            cursor.execute(delete_fixtures_query, (start_date, end_date))
            deleted_fixtures = cursor.rowcount

        connection.commit()

    return deleted_fixtures, deleted_prices


def build_dates_to_scrape(today: date, start_date: date, end_date: date) -> list[date]:
    """
    Build the dates that should be scraped.

    Normal daily behavior:
        - Refresh today.
        - Scrape end_date to extend the future window.
        - Scrape missing future dates.

    Optional behavior:
        - ODDSPORTAL_FORCE_FULL_WINDOW=1 scrapes all dates in the active window.
        - ODDSPORTAL_BACKFILL_PAST_DAYS=1 also fills missing past dates.
    """
    all_window_dates = iter_dates(start_date, end_date)
    future_dates = iter_dates(today, end_date)
    existing_dates = fetch_existing_fixture_dates(start_date, end_date)

    if is_enabled_env("ODDSPORTAL_FORCE_FULL_WINDOW"):
        return all_window_dates

    dates_to_scrape: set[date] = set()

    # Today's odds are refreshed because odds move during the day.
    dates_to_scrape.add(today)

    # The farthest future day is scraped every day to extend the rolling window.
    dates_to_scrape.add(end_date)

    # Fill gaps in the future window.
    for fixture_date in future_dates:
        if fixture_date not in existing_dates:
            dates_to_scrape.add(fixture_date)

    # Optional past backfill for the first setup or manual repair.
    if is_enabled_env("ODDSPORTAL_BACKFILL_PAST_DAYS"):
        for fixture_date in all_window_dates:
            if fixture_date < today and fixture_date not in existing_dates:
                dates_to_scrape.add(fixture_date)

    return sorted(dates_to_scrape)


def build_child_environment(target_date: date) -> dict[str, str]:
    """
    Build environment variables passed to scraper and loader.
    """
    return {
        "ENVIRONMENT": os.getenv("ENVIRONMENT", "development"),
        "ODDSPORTAL_TARGET_DATE": target_date.strftime("%Y%m%d"),
        "ODDSPORTAL_MAX_PARALLEL_MATCHES": os.getenv(
            "ODDSPORTAL_MAX_PARALLEL_MATCHES",
            DEFAULT_PARALLEL_WORKERS,
        ),
        "ODDSPORTAL_REQUEST_DELAY_SECONDS": os.getenv(
            "ODDSPORTAL_REQUEST_DELAY_SECONDS",
            "0.3",
        ),
        "ODDSPORTAL_MARKET_RENDER_WAIT_MS": os.getenv(
            "ODDSPORTAL_MARKET_RENDER_WAIT_MS",
            "3000",
        ),
        "ODDSPORTAL_SCROLL_RENDER_WAIT_MS": os.getenv(
            "ODDSPORTAL_SCROLL_RENDER_WAIT_MS",
            "400",
        ),
    }


def run_command(command: list[str], extra_env: dict[str, str]) -> None:
    """
    Run a command with extra environment variables.
    """
    command_env = os.environ.copy()
    command_env.update(extra_env)

    print("=" * 80)
    print("Running command:", " ".join(command))
    print("Environment:")
    for key, value in extra_env.items():
        print(f"  {key}={value}")
    print("=" * 80)

    result = subprocess.run(command, env=command_env)

    if result.returncode != 0:
        raise RuntimeError(
            f"Command failed with exit code {result.returncode}: {' '.join(command)}"
        )


def scrape_and_load_date(fixture_date: date) -> None:
    """
    Scrape and load one fixture date.
    """
    extra_env = build_child_environment(fixture_date)

    print("#" * 80)
    print(f"Processing OddsPortal date: {fixture_date}")
    print("#" * 80)

    run_command(
        [sys.executable, "-m", "app.scrapers.oddsportal_scraper_parallel"],
        extra_env=extra_env,
    )

    run_command(
        [sys.executable, "-m", "app.etl.load_oddsportal_simple_to_db"],
        extra_env=extra_env,
    )


def print_final_summary(start_date: date, end_date: date) -> None:
    """
    Print global counts after the update.
    """
    query = """
    SELECT
        COUNT(DISTINCT f.id) AS fixture_count,
        COUNT(p.id) AS price_count
    FROM oddsportal_fixtures f
    LEFT JOIN oddsportal_prices p
        ON p.fixture_id = f.id
    WHERE f.match_date BETWEEN %s AND %s;
    """

    database_url = get_database_url()

    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, (start_date, end_date))
            fixture_count, price_count = cursor.fetchone()

    print("=" * 80)
    print("Final active window summary")
    print("=" * 80)
    print(f"Window: {start_date} -> {end_date}")
    print(f"Fixtures in window: {fixture_count}")
    print(f"Prices in window: {price_count}")
    print("=" * 80)


def main() -> None:
    """
    Run the complete OddsPortal rolling window update.
    """
    started_at = datetime.now(ZoneInfo("Europe/Berlin"))
    today = get_today()
    start_date = today - timedelta(days=PAST_DAYS_TO_KEEP)
    end_date = today + timedelta(days=FUTURE_DAYS_TO_KEEP)

    print("=" * 80)
    print("Football Value Bets - OddsPortal Window Update")
    print("=" * 80)
    print(f"Started at: {started_at}")
    print(f"Today: {today}")
    print(f"Active window: {start_date} -> {end_date}")
    print(f"Past days kept: {PAST_DAYS_TO_KEEP}")
    print(f"Future days kept: {FUTURE_DAYS_TO_KEEP}")
    print(f"Default parallel workers: {DEFAULT_PARALLEL_WORKERS}")
    print(f"Force full window: {is_enabled_env('ODDSPORTAL_FORCE_FULL_WINDOW')}")
    print(f"Backfill past days: {is_enabled_env('ODDSPORTAL_BACKFILL_PAST_DAYS')}")
    print("=" * 80)

    acquire_lock()

    try:
        deleted_fixtures, deleted_prices = cleanup_old_oddsportal_data(
            start_date=start_date,
            end_date=end_date,
        )

        print("=" * 80)
        print("Cleanup before scraping completed")
        print("=" * 80)
        print(f"Deleted fixtures: {deleted_fixtures}")
        print(f"Deleted prices: {deleted_prices}")

        print_existing_dates(start_date, end_date)

        dates_to_scrape = build_dates_to_scrape(
            today=today,
            start_date=start_date,
            end_date=end_date,
        )

        print("=" * 80)
        print("Dates selected for scraping")
        print("=" * 80)

        if not dates_to_scrape:
            print("No dates selected.")
        else:
            for fixture_date in dates_to_scrape:
                print(f"- {fixture_date}")

        for fixture_date in dates_to_scrape:
            scrape_and_load_date(fixture_date)

        deleted_fixtures, deleted_prices = cleanup_old_oddsportal_data(
            start_date=start_date,
            end_date=end_date,
        )

        print("=" * 80)
        print("Cleanup after scraping completed")
        print("=" * 80)
        print(f"Deleted fixtures: {deleted_fixtures}")
        print(f"Deleted prices: {deleted_prices}")

        print_existing_dates(start_date, end_date)
        print_final_summary(start_date, end_date)

        finished_at = datetime.now(ZoneInfo("Europe/Berlin"))

        print("=" * 80)
        print("OddsPortal window update completed successfully")
        print("=" * 80)
        print(f"Finished at: {finished_at}")
        print(f"Duration: {finished_at - started_at}")
        print("=" * 80)

    finally:
        release_lock()


if __name__ == "__main__":
    main()