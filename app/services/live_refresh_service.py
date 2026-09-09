"""
Background live refresh service for the FastAPI app.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


logger = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[2]


class LiveRefreshService:
    """
    Refresh live matches every configured interval without blocking requests.
    """

    def __init__(self, interval_seconds: int = 60) -> None:
        self.interval_seconds = interval_seconds
        self._task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self.last_started_at: str | None = None
        self.last_completed_at: str | None = None
        self.last_error: str | None = None
        self.last_match_count: int = 0

    async def start(self) -> None:
        """
        Start the background loop once.
        """
        if self._task is not None:
            return

        self._task = asyncio.create_task(self._run_forever())

    async def stop(self) -> None:
        """
        Stop the background loop cleanly.
        """
        if self._task is None:
            return

        self._task.cancel()

        try:
            await self._task
        except asyncio.CancelledError:
            pass

        self._task = None

    async def refresh_once(self) -> None:
        """
        Scrape live matches and push them into PostgreSQL.
        """
        if self._lock.locked():
            return

        async with self._lock:
            self.last_started_at = datetime.now(timezone.utc).isoformat()

            try:
                self.last_match_count = await asyncio.to_thread(
                    self._run_refresh_pipeline
                )
                self.last_completed_at = datetime.now(timezone.utc).isoformat()
                self.last_error = None
            except Exception as error:
                self.last_error = str(error) or repr(error)
                logger.exception("Live refresh cycle failed: %s", error)

    def snapshot(self) -> dict[str, Any]:
        """
        Return the current service status for API responses.
        """
        return {
            "interval_seconds": self.interval_seconds,
            "last_started_at": self.last_started_at,
            "last_completed_at": self.last_completed_at,
            "last_error": self.last_error,
            "last_match_count": self.last_match_count,
            "is_refreshing": self._lock.locked(),
        }

    async def _run_forever(self) -> None:
        """
        Refresh immediately on startup, then keep a one-minute cadence.
        """
        while True:
            started_at = asyncio.get_running_loop().time()
            await self.refresh_once()
            elapsed_seconds = asyncio.get_running_loop().time() - started_at
            sleep_seconds = max(0.0, self.interval_seconds - elapsed_seconds)
            await asyncio.sleep(sleep_seconds)

    def _run_refresh_pipeline(self) -> int:
        """
        Run the existing live scraper and loader scripts.
        """
        self._run_command([sys.executable, "-m", "app.scrapers.oddsportal_live_scraper"])
        self._run_command([sys.executable, "-m", "app.etl.load_oddsportal_live_to_db"])

        return self._count_loaded_live_matches()

    def _run_command(self, command: list[str]) -> None:
        """
        Execute one live pipeline command and raise on failure.
        """
        command_env = os.environ.copy()
        command_env["PYTHONUNBUFFERED"] = "1"

        result = subprocess.run(
            command,
            cwd=PROJECT_ROOT,
            env=command_env,
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            stdout = (result.stdout or "").strip()
            error_message = stderr or stdout or f"Command failed: {' '.join(command)}"
            raise RuntimeError(error_message)

    def _count_loaded_live_matches(self) -> int:
        """
        Read the latest live JSON to expose a useful count in the status payload.
        """
        live_json_path = PROJECT_ROOT / "data" / "raw" / "oddsportal" / "live_matches.json"

        if not live_json_path.exists():
            return 0

        import json

        payload = json.loads(live_json_path.read_text(encoding="utf-8"))

        if not isinstance(payload, list):
            return 0

        return len(payload)
