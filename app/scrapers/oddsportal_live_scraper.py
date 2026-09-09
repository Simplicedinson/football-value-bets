"""
Scrape live football matches from OddsPortal in-play page.

Target page:
    https://www.oddsportal.com/inplay-odds/

Output:
    data/raw/oddsportal/live_matches.json
    data/raw/oddsportal/live_matches_YYYYMMDD_HHMMSS.json

Run:
    python -m app.scrapers.oddsportal_live_scraper

Notes:
    - This scraper is intentionally lightweight.
    - It does not open match detail pages.
    - It only reads the visible in-play football table.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright

load_dotenv()

TARGET_URL = os.getenv(
    "ODDSPORTAL_LIVE_URL",
    "https://www.oddsportal.com/inplay-odds/",
)

OUTPUT_DIR = Path("data/raw/oddsportal")
LIVE_JSON_OUTPUT = OUTPUT_DIR / "live_matches.json"

PAGE_RENDER_WAIT_MS = int(os.getenv("ODDSPORTAL_LIVE_RENDER_WAIT_MS", "4000"))
SCROLL_RENDER_WAIT_MS = int(os.getenv("ODDSPORTAL_LIVE_SCROLL_WAIT_MS", "700"))


@dataclass
class LiveMatch:
    """
    Simplified live match representation.
    """

    scraped_at: str
    match_date: str
    league_name: str
    live_minute: Optional[str]
    match_status: str
    home_team: str
    away_team: str
    home_score: Optional[int]
    away_score: Optional[int]
    odds: dict[str, Any]



def clean_text(value: Any) -> str:
    """
    Normalize whitespace safely.
    """
    return re.sub(r"\s+", " ", str(value or "")).strip()



def get_berlin_today() -> str:
    """
    Return today's date in Europe/Berlin.
    """
    return datetime.now(ZoneInfo("Europe/Berlin")).date().isoformat()



def get_status_from_minute(raw_minute: str) -> str:
    """
    Convert visible live minute text to internal status.
    """
    value = clean_text(raw_minute).lower()

    if value in {"ht", "half-time", "halftime"}:
        return "halftime"

    if value in {"ft", "finished"}:
        return "finished"

    if re.match(r"^\d{1,3}'$", value):
        return "live"

    return "live"



def parse_int(value: Any) -> Optional[int]:
    """
    Convert a value to integer safely.
    """
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None



def parse_float(value: Any) -> Optional[float]:
    """
    Convert a value to float safely.
    """
    try:
        numeric_value = float(str(value).strip())
    except (TypeError, ValueError):
        return None

    if numeric_value <= 1.0:
        return None

    return numeric_value



def is_league_header(text: str) -> bool:
    """
    Detect OddsPortal in-play football league headers.

    Examples:
        Football / World / Club Friendly Women
        Football / Norway / Division 2 - Group 1
    """
    text = clean_text(text)
    return text.lower().startswith("football /")



def normalize_league_name(text: str) -> str:
    """
    Normalize a league header.
    """
    text = clean_text(text)

    # Remove duplicated football icon text if it appears twice.
    text = re.sub(r"^football\s*/\s*", "Football / ", text, flags=re.IGNORECASE)

    return text



def extract_decimal_odds_values(text: str) -> list[float]:
    """
    Extract decimal odds values from visible row text.
    """
    raw_values = re.findall(r"(?<![\d:])\d{1,2}\.\d{2}(?!\d)", text)

    values: list[float] = []

    for raw_value in raw_values:
        value = parse_float(raw_value)

        if value is not None:
            values.append(value)

    return values



def strip_trailing_odds(text: str) -> tuple[str, dict[str, Any]]:
    """
    Remove trailing 1X2 odds from a match row and return odds separately.

    Example:
        "Team A 1 : 0 Team B 2.04 3.20 3.90"
    """
    text = clean_text(text)

    pattern = re.compile(
        r"^(?P<body>.+?)\s+"
        r"(?P<home_win>\d{1,2}\.\d{2})\s+"
        r"(?P<draw>\d{1,2}\.\d{2})\s+"
        r"(?P<away_win>\d{1,2}\.\d{2})$"
    )

    match = pattern.match(text)

    if not match:
        return text, {}

    odds = {
        "1x2": {
            "live": {
                "bookmaker": "summary",
                "values": {
                    "home_win": parse_float(match.group("home_win")),
                    "draw": parse_float(match.group("draw")),
                    "away_win": parse_float(match.group("away_win")),
                },
            }
        }
    }

    # Remove odds values that could not be parsed.
    values = odds["1x2"]["live"]["values"]
    odds["1x2"]["live"]["values"] = {
        key: value for key, value in values.items() if value is not None
    }

    return clean_text(match.group("body")), odds



def parse_live_match_text(
    text: str,
    league_name: str,
    scraped_at: str,
    match_date: str,
) -> Optional[LiveMatch]:
    """
    Parse one visible live match row.

    Expected examples:
        37' SGS Essen W (Ger) 0 : 0 Twente W (Ned) 3.90 3.10 1.92
        33' Kvik Halden 0 : 0 Traeff 2.04 3.20 3.90
        HT Team A 1 : 1 Team B 2.20 3.10 2.90
    """
    text = clean_text(text)

    # Ignore table headers.
    if text in {"1 X 2", "1", "X", "2"}:
        return None

    body_without_odds, odds = strip_trailing_odds(text)

    pattern = re.compile(
        r"^(?P<minute>\d{1,3}'|HT|Half[- ]?Time)\s+"
        r"(?P<home_team>.+?)\s+"
        r"(?P<home_score>\d+)\s*:\s*(?P<away_score>\d+)\s+"
        r"(?P<away_team>.+)$",
        re.IGNORECASE,
    )

    match = pattern.match(body_without_odds)

    if not match:
        return None

    raw_minute = clean_text(match.group("minute"))

    return LiveMatch(
        scraped_at=scraped_at,
        match_date=match_date,
        league_name=league_name,
        live_minute=raw_minute,
        match_status=get_status_from_minute(raw_minute),
        home_team=clean_text(match.group("home_team")),
        away_team=clean_text(match.group("away_team")),
        home_score=parse_int(match.group("home_score")),
        away_score=parse_int(match.group("away_score")),
        odds=odds,
    )


async def accept_cookies_if_visible(page) -> None:
    """
    Accept the cookie popup if it appears.
    """
    possible_button_names = [
        "Accept",
        "Accept all",
        "I accept",
        "Agree",
        "OK",
    ]

    for button_name in possible_button_names:
        try:
            button = page.get_by_role(
                "button",
                name=re.compile(button_name, re.IGNORECASE),
            )

            if await button.count() > 0:
                await button.first.click(timeout=3000)
                await page.wait_for_timeout(1000)
                return

        except Exception:
            continue


async def block_unnecessary_resources(route) -> None:
    """
    Block heavy resources that are not needed for text extraction.
    """
    blocked_resource_types = {"image", "media", "font"}

    if route.request.resource_type in blocked_resource_types:
        await route.abort()
        return

    await route.continue_()


async def extract_visible_text_blocks(page) -> list[str]:
    """
    Extract visible text blocks from the in-play page.
    """
    raw_texts = await page.locator("body *").evaluate_all(
        """
        elements => {
            function normalizeText(value) {
                return (value || "").replace(/\s+/g, " ").trim();
            }

            function isVisible(element) {
                const style = window.getComputedStyle(element);
                const rect = element.getBoundingClientRect();

                return (
                    style.display !== "none" &&
                    style.visibility !== "hidden" &&
                    rect.width > 0 &&
                    rect.height > 0
                );
            }

            const output = [];

            for (const element of elements) {
                if (!isVisible(element)) {
                    continue;
                }

                const text = normalizeText(element.innerText);

                if (!text) {
                    continue;
                }

                if (text.length < 3 || text.length > 350) {
                    continue;
                }

                output.push(text);
            }

            return output;
        }
        """
    )

    clean_texts: list[str] = []
    seen_texts: set[str] = set()

    for raw_text in raw_texts:
        text = clean_text(raw_text)

        if not text:
            continue

        if text in seen_texts:
            continue

        seen_texts.add(text)
        clean_texts.append(text)

    return clean_texts


async def scrape_live_matches() -> list[LiveMatch]:
    """
    Scrape live football matches from OddsPortal in-play page.
    """
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    scraped_at = datetime.now(timezone.utc).isoformat()
    match_date = get_berlin_today()

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)

        context = await browser.new_context(
            locale="en-US",
            timezone_id="Europe/Berlin",
            viewport={"width": 1440, "height": 1200},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        )

        await context.route("**/*", block_unnecessary_resources)

        page = await context.new_page()

        try:
            await page.goto(
                TARGET_URL,
                wait_until="domcontentloaded",
                timeout=60000,
            )

            await accept_cookies_if_visible(page)
            await page.wait_for_timeout(PAGE_RENDER_WAIT_MS)

            # Trigger lazy loading.
            await page.mouse.wheel(0, 1400)
            await page.wait_for_timeout(SCROLL_RENDER_WAIT_MS)
            await page.mouse.wheel(0, -1400)
            await page.wait_for_timeout(SCROLL_RENDER_WAIT_MS)

            text_blocks = await extract_visible_text_blocks(page)

            live_matches: list[LiveMatch] = []
            current_league = "Unknown Live League"
            seen_match_keys: set[tuple[str, str, str, str]] = set()

            for text in text_blocks:
                if is_league_header(text):
                    current_league = normalize_league_name(text)
                    continue

                live_match = parse_live_match_text(
                    text=text,
                    league_name=current_league,
                    scraped_at=scraped_at,
                    match_date=match_date,
                )

                if live_match is None:
                    continue

                match_key = (
                    live_match.league_name,
                    live_match.home_team,
                    live_match.away_team,
                    live_match.live_minute or "",
                )

                if match_key in seen_match_keys:
                    continue

                seen_match_keys.add(match_key)
                live_matches.append(live_match)

            return live_matches

        except PlaywrightTimeoutError:
            print("Timeout while loading OddsPortal in-play page.")
            return []

        finally:
            await browser.close()


def write_output_json(live_matches: list[LiveMatch]) -> None:
    """
    Write live matches to JSON files.
    """
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    output_data = [asdict(match) for match in live_matches]

    timestamp_slug = datetime.now(ZoneInfo("Europe/Berlin")).strftime("%Y%m%d_%H%M%S")
    timestamped_output = OUTPUT_DIR / f"live_matches_{timestamp_slug}.json"

    LIVE_JSON_OUTPUT.write_text(
        json.dumps(output_data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    timestamped_output.write_text(
        json.dumps(output_data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print(f"Live JSON saved to: {LIVE_JSON_OUTPUT}")
    print(f"Timestamped live JSON saved to: {timestamped_output}")


def print_summary(live_matches: list[LiveMatch]) -> None:
    """
    Print scraping summary.
    """
    print("=" * 80)
    print("OddsPortal live football matches")
    print("=" * 80)
    print(f"Target URL: {TARGET_URL}")
    print(f"Live matches found: {len(live_matches)}")
    print("=" * 80)

    for match in live_matches:
        score = f"{match.home_score}:{match.away_score}"
        minute = match.live_minute or "-"
        print(
            f"{minute} | {match.league_name} | "
            f"{match.home_team} {score} {match.away_team}"
        )

    print("=" * 80)


async def main() -> None:
    """
    Main entry point.
    """
    live_matches = await scrape_live_matches()
    write_output_json(live_matches)
    print_summary(live_matches)


if __name__ == "__main__":
    print("=" * 80)
    print("Football Value Bets - OddsPortal Live Scraper started")
    print(f"Started at: {datetime.now(timezone.utc)}")
    asyncio.run(main())
    print("=" * 80)
    print("Football Value Bets - OddsPortal Live Scraper finished")
    print(f"Finished at: {datetime.now(timezone.utc)}")