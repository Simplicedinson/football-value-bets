"""
Simplified OddsPortal football scraper.

Output goal:
- Keep matches grouped by league.
- Do not save URLs in the final JSON.
- Keep one selected bookmaker for standard markets.
- Keep a compact structure without repeated market/period metadata.
- Keep Over/Under lines in a clean format:
    {"label": "Over/Under 2.5", "line": "2.5", "values": {"over": 1.88, "under": 1.98}}

Important:
- This is a lightweight local scraper.
- It does not use proxies.
- It does not bypass protections.
- It only reads public visible pages.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError


TARGET_URL = "https://www.oddsportal.com/matches/football/"

OUTPUT_DIR = Path("data/raw/oddsportal")
HTML_OUTPUT = OUTPUT_DIR / "football_matches_page.html"
JSON_OUTPUT = OUTPUT_DIR / "football_matches_by_league_simple.json"

# We keep only one bookmaker for markets like 1X2 and BTTS.
# For Over/Under, the visible summary line is used because it directly exposes
# the different lines: 0.5, 1.5, 2.5, etc.
PREFERRED_BOOKMAKER = "Bet365.de"

# For quick tests, keep 3.
# Set to None when you want to scrape all matches.
MAX_MATCHES_TO_SCRAPE_ODDS: Optional[int] = 3

REQUEST_DELAY_SECONDS = 1.5


ODDS_MARKET_SUFFIXES = [
    {"market_key": "1x2", "period_key": "full_time", "suffix": ":1X2;2"},
    {"market_key": "1x2", "period_key": "halftime", "suffix": ":1X2;3"},
    {"market_key": "1x2", "period_key": "second_half", "suffix": ":1X2;4"},
    {"market_key": "over_under", "period_key": "full_time", "suffix": ":over-under;2"},
    {"market_key": "over_under", "period_key": "halftime", "suffix": ":over-under;3"},
    {"market_key": "over_under", "period_key": "second_half", "suffix": ":over-under;4"},
    {"market_key": "both_teams_to_score", "period_key": "full_time", "suffix": ":bts;2"},
    {"market_key": "both_teams_to_score", "period_key": "halftime", "suffix": ":bts;3"},
    {"market_key": "both_teams_to_score", "period_key": "second_half", "suffix": ":bts;4"},
]


@dataclass
class FootballMatch:
    """
    Internal match representation.

    match_url is kept only internally for scraping.
    It is not written in the simplified output JSON.
    """

    scraped_at: str
    kickoff_time: str
    home_team: str
    away_team: str
    match_url: str
    odds: dict[str, Any] = field(default_factory=dict)

    def to_output_dict(self) -> dict[str, Any]:
        """
        Convert match to the simplified JSON structure.
        """
        return {
            "scraped_at": self.scraped_at,
            "kickoff_time": self.kickoff_time,
            "home_team": self.home_team,
            "away_team": self.away_team,
            "odds": self.odds,
        }


@dataclass
class LeagueSection:
    """
    Internal league representation.

    league_url is kept only internally for grouping.
    It is not written in the simplified output JSON.
    """

    league_name: str
    league_url: str
    matches: list[FootballMatch] = field(default_factory=list)

    def to_output_dict(self) -> dict[str, Any]:
        """
        Convert league to the simplified JSON structure.
        """
        return {
            "league_name": self.league_name,
            "matches": [match.to_output_dict() for match in self.matches],
        }


def clean_text(value: Any) -> str:
    """
    Normalize whitespace safely.
    """
    return re.sub(r"\s+", " ", str(value or "")).strip()


def clean_league_name(value: str) -> str:
    """
    Remove match count from league name.

    Example:
        Premier League (10) -> Premier League
    """
    value = clean_text(value)
    return re.sub(r"\s+\(\d+\)$", "", value).strip()


def is_real_match_text(text: str) -> bool:
    """
    Check if text looks like a match row.

    Example:
        17:00 Kairat Almaty (Kaz) – Sutjeska (Mne)
    """
    text = clean_text(text)
    return bool(re.match(r"^\d{1,2}:\d{2}\s+.+\s+[–-]\s+.+$", text))


def is_real_match_url(url: str) -> bool:
    """
    Check if the URL looks like an OddsPortal match URL.
    """
    if not url:
        return False

    return "/football/h2h/" in url and "#" in url


def is_league_url(url: str) -> bool:
    """
    Check if the URL looks like a league page.

    Accepted:
        /football/england/premier-league/

    Rejected:
        /football/
        /football/h2h/team-a/team-b/#abc
        /matches/football/
    """
    if not url:
        return False

    parsed_url = urlparse(url)
    path = parsed_url.path.strip("/")
    parts = path.split("/")

    if len(parts) != 3:
        return False

    if parts[0] != "football":
        return False

    if parts[1] == "h2h":
        return False

    return True


def parse_match_text(text: str) -> Optional[tuple[str, str, str]]:
    """
    Parse match text into kickoff time, home team and away team.
    """
    text = clean_text(text)
    match = re.match(r"^(\d{1,2}:\d{2})\s+(.+?)\s+[–-]\s+(.+)$", text)

    if not match:
        return None

    return (
        match.group(1).strip(),
        match.group(2).strip(),
        match.group(3).strip(),
    )


def remove_existing_market_suffix(match_url: str) -> str:
    """
    Remove an existing OddsPortal market suffix from a match URL.

    Example:
        #abc123:1X2;2 -> #abc123
    """
    return re.sub(
        r":(?:1X2|over-under|bts);\d+$",
        "",
        match_url.strip(),
    )


def build_market_url(match_url: str, suffix: str) -> str:
    """
    Build a market URL from a match URL and an OddsPortal suffix.
    """
    return remove_existing_market_suffix(match_url) + suffix


def extract_decimal_odds_values(text: str) -> list[str]:
    """
    Extract decimal odds values from text.

    Examples:
        1.55
        3.90
        12.00
    """
    text = clean_text(text)

    values = re.findall(r"(?<![\d:])\d{1,2}\.\d{2}(?!\d)", text)

    valid_values: list[str] = []

    for value in values:
        try:
            numeric_value = float(value)
        except ValueError:
            continue

        if 1.00 <= numeric_value <= 100.00:
            valid_values.append(value)

    return valid_values


def as_float(value: Any) -> Optional[float]:
    """
    Convert value to float safely.
    """
    if value in {None, "", "-"}:
        return None

    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def normalize_line_value(value: Any) -> Optional[str]:
    """
    Normalize Over/Under line values.

    Examples:
        +2.5 -> 2.5
        2.50 -> 2.5
    """
    if value is None:
        return None

    value_as_text = clean_text(value).replace("+", "")

    if not value_as_text:
        return None

    try:
        numeric_value = float(value_as_text)
    except ValueError:
        return value_as_text

    if numeric_value.is_integer():
        return str(int(numeric_value))

    return str(numeric_value)


def extract_bookmaker_name(text: str) -> str:
    """
    Extract bookmaker name from a bookmaker row.
    """
    text = clean_text(text)

    if " CLAIM BONUS" in text:
        return text.split(" CLAIM BONUS")[0].strip()

    return text.split(" ")[0].strip()


def is_previous_matches_section(text: str) -> bool:
    """
    Detect the preview/history block that must not be parsed as odds.
    """
    text = clean_text(text).lower()

    blocked_patterns = [
        "previous matches",
        "last matches",
        "h2h matches",
        "head-to-head",
    ]

    return any(pattern in text for pattern in blocked_patterns)


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


async def prepare_market_page(page, market_url: str) -> None:
    """
    Open a market page and force a fresh render.

    OddsPortal uses hash suffixes, so moving through about:blank avoids
    keeping an old market visible after a hash-only URL change.
    """
    await page.goto("about:blank", wait_until="domcontentloaded", timeout=30000)

    await page.goto(
        market_url,
        wait_until="domcontentloaded",
        timeout=60000,
    )

    await accept_cookies_if_visible(page)

    # Give the JavaScript app time to render.
    await page.wait_for_timeout(3500)

    # Trigger lazy-loaded rows if needed.
    await page.mouse.wheel(0, 1200)
    await page.wait_for_timeout(700)
    await page.mouse.wheel(0, -1200)
    await page.wait_for_timeout(700)


async def extract_visible_text_blocks(page) -> list[str]:
    """
    Extract visible text blocks from the current page.

    The function stops when it reaches preview/history blocks such as
    Previous Matches. We only want odds.
    """
    raw_texts = await page.locator("body *").evaluate_all(
        """
        elements => {
            function normalizeText(value) {
                return (value || "").replace(/\\s+/g, " ").trim();
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

                if (/^(Previous Matches|Last Matches|H2H Matches)/i.test(text)) {
                    break;
                }

                if (text.length < 4 || text.length > 900) {
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

        if is_previous_matches_section(text):
            break

        if text in seen_texts:
            continue

        seen_texts.add(text)
        clean_texts.append(text)

    return clean_texts


def build_values_from_bookmaker_row(
    market_key: str,
    raw_values: list[str],
) -> Optional[dict[str, float]]:
    """
    Convert raw bookmaker odds into simple named values.
    """
    values = [as_float(value) for value in raw_values]
    values = [value for value in values if value is not None]

    if market_key == "1x2" and len(values) >= 3:
        return {
            "home_win": values[0],
            "draw": values[1],
            "away_win": values[2],
        }

    if market_key == "both_teams_to_score" and len(values) >= 2:
        return {
            "yes": values[0],
            "no": values[1],
        }

    return None


def parse_single_bookmaker_market(
    text_blocks: list[str],
    market_key: str,
    preferred_bookmaker: str,
) -> Optional[dict[str, Any]]:
    """
    Parse one selected bookmaker row for markets without line values.

    Used for:
        1X2
        Both Teams To Score
    """
    candidate_rows = [
        text
        for text in text_blocks
        if "CLAIM BONUS" in text and not is_previous_matches_section(text)
    ]

    preferred_rows = [
        text
        for text in candidate_rows
        if preferred_bookmaker.lower() in text.lower()
    ]

    rows_to_try = preferred_rows or candidate_rows

    for row_text in rows_to_try:
        raw_values = extract_decimal_odds_values(row_text)
        values = build_values_from_bookmaker_row(market_key, raw_values)

        if not values:
            continue

        return {
            "bookmaker": extract_bookmaker_name(row_text),
            "values": values,
        }

    return None


def parse_over_under_summary_lines(text_blocks: list[str]) -> list[dict[str, Any]]:
    """
    Parse visible Over/Under summary lines.

    Expected visible format:
        Over/Under +2.5 7 1.88 1.98 96.4%

    Simplified output:
        {
            "label": "Over/Under 2.5",
            "line": "2.5",
            "values": {"over": 1.88, "under": 1.98}
        }
    """
    lines_by_value: dict[str, dict[str, Any]] = {}

    patterns = [
        # Normal visible row:
        # Over/Under +2.5 7 1.88 1.98 96.4%
        re.compile(
            r"^Over/Under\s+\+?(\d+(?:\.\d+)?)\s+\d+\s+"
            r"(\d{1,2}\.\d{2}|-)\s+(\d{1,2}\.\d{2}|-)",
            re.IGNORECASE,
        ),
        # Fallback for text blocks that contain the row inside a larger block.
        re.compile(
            r"Over/Under\s+\+?(\d+(?:\.\d+)?)\s+\d+\s+"
            r"(\d{1,2}\.\d{2}|-)\s+(\d{1,2}\.\d{2}|-)",
            re.IGNORECASE,
        ),
    ]

    for text in text_blocks:
        if is_previous_matches_section(text):
            break

        for pattern in patterns:
            for match in pattern.finditer(text):
                line_value = normalize_line_value(match.group(1))
                over_value = as_float(match.group(2))
                under_value = as_float(match.group(3))

                if line_value is None:
                    continue

                if over_value is None or under_value is None:
                    continue

                row = {
                    "label": f"Over/Under {line_value}",
                    "line": line_value,
                    "values": {
                        "over": over_value,
                        "under": under_value,
                    },
                }

                # Keep one clean row per line.
                # If a duplicate appears, keep the row with the better payout proxy:
                # over + under is not a real payout formula, but it avoids replacing
                # good visible rows by incomplete duplicated blocks.
                existing_row = lines_by_value.get(line_value)

                if existing_row is None:
                    lines_by_value[line_value] = row
                    continue

                existing_score = (
                    existing_row["values"]["over"]
                    + existing_row["values"]["under"]
                )
                new_score = over_value + under_value

                if new_score > existing_score:
                    lines_by_value[line_value] = row

    return sorted(
        lines_by_value.values(),
        key=lambda row: float(row["line"]),
    )


def parse_market_period(
    text_blocks: list[str],
    market_key: str,
) -> Optional[dict[str, Any]]:
    """
    Parse one market period into the simplified structure.
    """
    if market_key == "over_under":
        lines = parse_over_under_summary_lines(text_blocks)

        if not lines:
            return None

        return {
            "lines": lines,
        }

    return parse_single_bookmaker_market(
        text_blocks=text_blocks,
        market_key=market_key,
        preferred_bookmaker=PREFERRED_BOOKMAKER,
    )


async def scrape_all_odds_for_match(page, match_url: str) -> dict[str, Any]:
    """
    Scrape all configured markets for one match.

    Final structure:
        odds["1x2"]["full_time"] = {"bookmaker": "...", "values": {...}}
        odds["over_under"]["full_time"] = {"lines": [...]}
    """
    odds: dict[str, Any] = {}

    for market_config in ODDS_MARKET_SUFFIXES:
        market_key = market_config["market_key"]
        period_key = market_config["period_key"]
        suffix = market_config["suffix"]

        market_url = build_market_url(match_url, suffix)

        try:
            print(f"Opening {market_key} | {period_key}")

            await prepare_market_page(page, market_url)
            text_blocks = await extract_visible_text_blocks(page)

            period_data = parse_market_period(
                text_blocks=text_blocks,
                market_key=market_key,
            )

            if period_data is None:
                continue

            if market_key not in odds:
                odds[market_key] = {}

            odds[market_key][period_key] = period_data

            await page.wait_for_timeout(int(REQUEST_DELAY_SECONDS * 1000))

        except PlaywrightTimeoutError:
            print(f"Timeout while loading market: {market_key} | {period_key}")

        except Exception as error:
            print(f"Could not scrape market: {market_key} | {period_key}")
            print(f"Error: {error}")

    return odds


async def scrape_matches_by_league() -> list[LeagueSection]:
    """
    Render OddsPortal football page, group matches by league,
    and add simplified odds for each match.
    """
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    scraped_at = datetime.now(timezone.utc).isoformat()

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

        page = await context.new_page()

        try:
            await page.goto(
                TARGET_URL,
                wait_until="domcontentloaded",
                timeout=60000,
            )

            await accept_cookies_if_visible(page)
            await page.wait_for_timeout(5000)

            html = await page.content()
            HTML_OUTPUT.write_text(html, encoding="utf-8")

            raw_links = await page.locator("a[href*='/football/']").evaluate_all(
                """
                elements => elements.map(element => ({
                    text: (element.innerText || "").replace(/\\s+/g, " ").trim(),
                    url: element.href || ""
                }))
                """
            )

            league_sections: list[LeagueSection] = []
            current_league: Optional[LeagueSection] = None

            seen_league_urls: set[str] = set()
            seen_match_urls: set[str] = set()

            for raw_link in raw_links:
                text = clean_text(raw_link.get("text", ""))
                url = raw_link.get("url", "")

                if not text or not url:
                    continue

                if is_league_url(url):
                    league_name = clean_league_name(text)

                    if url in seen_league_urls:
                        current_league = next(
                            (
                                section
                                for section in league_sections
                                if section.league_url == url
                            ),
                            None,
                        )
                        continue

                    current_league = LeagueSection(
                        league_name=league_name,
                        league_url=url,
                        matches=[],
                    )

                    league_sections.append(current_league)
                    seen_league_urls.add(url)
                    continue

                if not is_real_match_text(text):
                    continue

                if not is_real_match_url(url):
                    continue

                if url in seen_match_urls:
                    continue

                parsed_match = parse_match_text(text)

                if not parsed_match:
                    continue

                if current_league is None:
                    current_league = LeagueSection(
                        league_name="Unknown League",
                        league_url="",
                        matches=[],
                    )
                    league_sections.append(current_league)

                kickoff_time, home_team, away_team = parsed_match

                current_league.matches.append(
                    FootballMatch(
                        scraped_at=scraped_at,
                        kickoff_time=kickoff_time,
                        home_team=home_team,
                        away_team=away_team,
                        match_url=url,
                    )
                )

                seen_match_urls.add(url)

            league_sections = [
                section for section in league_sections if section.matches
            ]

            detail_page = await context.new_page()
            scraped_detailed_matches = 0

            for section in league_sections:
                for match in section.matches:
                    if (
                        MAX_MATCHES_TO_SCRAPE_ODDS is not None
                        and scraped_detailed_matches >= MAX_MATCHES_TO_SCRAPE_ODDS
                    ):
                        break

                    print(
                        "Scraping odds:",
                        match.kickoff_time,
                        match.home_team,
                        "vs",
                        match.away_team,
                    )

                    match.odds = await scrape_all_odds_for_match(
                        detail_page,
                        match.match_url,
                    )

                    scraped_detailed_matches += 1

                    await detail_page.wait_for_timeout(
                        int(REQUEST_DELAY_SECONDS * 1000)
                    )

                if (
                    MAX_MATCHES_TO_SCRAPE_ODDS is not None
                    and scraped_detailed_matches >= MAX_MATCHES_TO_SCRAPE_ODDS
                ):
                    break

            await detail_page.close()

            return league_sections

        except PlaywrightTimeoutError:
            print("Timeout while loading OddsPortal football matches page.")
            return []

        finally:
            await browser.close()


def write_output_json(league_sections: list[LeagueSection]) -> None:
    """
    Write simplified output JSON.
    """
    output_data = [
        section.to_output_dict()
        for section in league_sections
        if section.matches
    ]

    JSON_OUTPUT.write_text(
        json.dumps(output_data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def print_summary(league_sections: list[LeagueSection]) -> None:
    """
    Print a compact scraping summary.
    """
    total_matches = sum(len(section.matches) for section in league_sections)
    matches_with_odds = sum(
        1
        for section in league_sections
        for match in section.matches
        if match.odds
    )

    print("=" * 80)
    print("OddsPortal simplified football odds")
    print("=" * 80)
    print(f"Preferred bookmaker: {PREFERRED_BOOKMAKER}")
    print(f"Total leagues found: {len(league_sections)}")
    print(f"Total matches found: {total_matches}")
    print(f"Matches with scraped odds: {matches_with_odds}")
    print(f"JSON saved to: {JSON_OUTPUT}")
    print("=" * 80)

    for section in league_sections:
        print()
        print(section.league_name)
        print("-" * 80)

        for match in section.matches:
            market_keys = ", ".join(match.odds.keys()) if match.odds else "no odds"

            print(
                f"{match.kickoff_time} | "
                f"{match.home_team} vs {match.away_team} | "
                f"{market_keys}"
            )


async def main() -> None:
    """
    Main entry point.
    """
    league_sections = await scrape_matches_by_league()
    write_output_json(league_sections)
    print_summary(league_sections)


if __name__ == "__main__":
    asyncio.run(main())
