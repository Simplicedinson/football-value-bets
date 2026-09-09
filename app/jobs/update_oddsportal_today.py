"""
Run the full OddsPortal daily update pipeline.

Steps:
1. Scrape today's football matches from OddsPortal.
2. Load the simplified JSON output into PostgreSQL.
3. Check the loaded OddsPortal data.

This script is intentionally simple because each step already exists
as a separate Python module.
"""

import subprocess
import sys
from datetime import datetime


def run_command(command: list[str]) -> None:
    """
    Run a command and stop the pipeline if it fails.
    """
    print("=" * 70)
    print(f"Running command: {' '.join(command)}")
    print("=" * 70)

    result = subprocess.run(command)

    if result.returncode != 0:
        raise RuntimeError(
            f"Command failed with exit code {result.returncode}: {' '.join(command)}"
        )


def main() -> None:
    """
    Execute the complete OddsPortal update pipeline.
    """
    started_at = datetime.now()

    print("=" * 70)
    print("Football Value Bets - OddsPortal Daily Update")
    print(f"Started at: {started_at}")
    print("=" * 70)

    run_command([sys.executable, "-m", "app.scrapers.oddsportal_scraper"])
    run_command([sys.executable, "-m", "app.etl.load_oddsportal_simple_to_db"])
    run_command([sys.executable, "-m", "app.db.check_oddsportal_data"])

    finished_at = datetime.now()
    duration = finished_at - started_at

    print("=" * 70)
    print("OddsPortal daily update completed successfully.")
    print(f"Finished at: {finished_at}")
    print(f"Duration: {duration}")
    print("=" * 70)


if __name__ == "__main__":
    main()