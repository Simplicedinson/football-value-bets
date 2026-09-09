"""
Today matches web routes.

This page displays today's fixtures with 1X2 odds and sorting options.
"""

from __future__ import annotations

import json
import os
from decimal import Decimal
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import psycopg
from dotenv import load_dotenv
from fastapi.encoders import jsonable_encoder
from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from datetime import datetime


router = APIRouter()

templates = Jinja2Templates(directory="app/web/templates")


SORT_COLUMNS = {
    "league": "league_name",
    "time": "kickoff_time",
    "home": "home_team",
    "away": "away_team",
    "home_win": "home_win",
    "draw": "draw",
    "away_win": "away_win",
    "highest_odd": "highest_odd",
    "prices": "price_count",
}


def ensure_sslmode(database_url: str) -> str:
    """
    Add sslmode=require if missing.
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
    Read DATABASE_URL from environment.
    """
    load_dotenv()

    database_url = os.getenv("DATABASE_URL")

    if not database_url:
        raise RuntimeError("DATABASE_URL is missing.")

    return ensure_sslmode(database_url)


def decimal_to_float(value: Any) -> Any:
    """
    Convert Decimal values to float for template rendering.
    """
    if isinstance(value, Decimal):
        return float(value)

    return value


# def fetch_today_matches(
#     sort: str,
#     direction: str,
# ) -> list[dict[str, Any]]:
#     """
#     Fetch today's matches with 1X2 full-time odds.

#     If there is no current-date data, the query uses the latest match_date
#     available in the staging table.
#     """
#     sort_column = SORT_COLUMNS.get(sort, "kickoff_time")
#     sort_direction = "DESC" if direction == "desc" else "ASC"

#     query = f"""
#     WITH selected_date AS (
#         SELECT COALESCE(
#             (
#                 SELECT match_date
#                 FROM oddsportal_fixtures
#                 WHERE match_date = CURRENT_DATE
#                 LIMIT 1
#             ),
#             (
#                 SELECT MAX(match_date)
#                 FROM oddsportal_fixtures
#             )
#         ) AS match_date
#     ),
#     match_odds AS (
#         SELECT
#             f.id AS fixture_id,
#             f.league_name,
#             f.match_date,
#             f.kickoff_time,
#             f.home_team,
#             f.away_team,

#             MAX(
#                 CASE
#                     WHEN p.market_key = '1x2'
#                     AND p.period_key = 'full_time'
#                     AND p.outcome_name = 'home_win'
#                     THEN p.odd_value
#                 END
#             ) AS home_win,

#             MAX(
#                 CASE
#                     WHEN p.market_key = '1x2'
#                     AND p.period_key = 'full_time'
#                     AND p.outcome_name = 'draw'
#                     THEN p.odd_value
#                 END
#             ) AS draw,

#             MAX(
#                 CASE
#                     WHEN p.market_key = '1x2'
#                     AND p.period_key = 'full_time'
#                     AND p.outcome_name = 'away_win'
#                     THEN p.odd_value
#                 END
#             ) AS away_win,

#             COUNT(p.id) AS price_count

#         FROM oddsportal_fixtures f
#         LEFT JOIN oddsportal_prices p
#             ON p.fixture_id = f.id
#         WHERE f.match_date = (SELECT match_date FROM selected_date)
#         GROUP BY
#             f.id,
#             f.league_name,
#             f.match_date,
#             f.kickoff_time,
#             f.home_team,
#             f.away_team
#     )
#     SELECT
#         fixture_id,
#         league_name,
#         match_date,
#         kickoff_time,
#         home_team,
#         away_team,
#         home_win,
#         draw,
#         away_win,
#         GREATEST(
#             COALESCE(home_win, 0),
#             COALESCE(draw, 0),
#             COALESCE(away_win, 0)
#         ) AS highest_odd,
#         price_count
#     FROM match_odds
#     ORDER BY {sort_column} {sort_direction} NULLS LAST;
#     """

#     database_url = get_database_url()

#     with psycopg.connect(database_url) as connection:
#         with connection.cursor() as cursor:
#             cursor.execute(query)
#             rows = cursor.fetchall()
#             columns = [description.name for description in cursor.description]

#     matches: list[dict[str, Any]] = []

#     for row in rows:
#         match_data = {
#             column: decimal_to_float(value)
#             for column, value in zip(columns, row)
#         }
#         matches.append(match_data)

#     return matches


def fetch_today_matches(
    sort: str,
    direction: str,
) -> list[dict[str, Any]]:
    """
    Fetch only today's matches with 1X2 full-time odds.

    Important:
    This function must not fallback to yesterday or latest available date.
    If today's data has not been scraped yet, the page should be empty.
    """
    sort_column = SORT_COLUMNS.get(sort, "kickoff_time")
    sort_direction = "DESC" if direction == "desc" else "ASC"

    query = f"""
    WITH match_odds AS (
        SELECT
            f.id AS fixture_id,
            f.league_name,
            f.match_date,
            f.kickoff_time,
            f.home_team,
            f.away_team,

            MAX(
                CASE
                    WHEN p.market_key = '1x2'
                    AND p.period_key = 'full_time'
                    AND p.outcome_name = 'home_win'
                    THEN p.odd_value
                END
            ) AS home_win,

            MAX(
                CASE
                    WHEN p.market_key = '1x2'
                    AND p.period_key = 'full_time'
                    AND p.outcome_name = 'draw'
                    THEN p.odd_value
                END
            ) AS draw,

            MAX(
                CASE
                    WHEN p.market_key = '1x2'
                    AND p.period_key = 'full_time'
                    AND p.outcome_name = 'away_win'
                    THEN p.odd_value
                END
            ) AS away_win,

            COUNT(p.id) AS price_count

        FROM oddsportal_fixtures f
        LEFT JOIN oddsportal_prices p
            ON p.fixture_id = f.id
        WHERE f.match_date = CURRENT_DATE
        GROUP BY
            f.id,
            f.league_name,
            f.match_date,
            f.kickoff_time,
            f.home_team,
            f.away_team
    )
    SELECT
        fixture_id,
        league_name,
        match_date,
        kickoff_time,
        home_team,
        away_team,
        home_win,
        draw,
        away_win,
        GREATEST(
            COALESCE(home_win, 0),
            COALESCE(draw, 0),
            COALESCE(away_win, 0)
        ) AS highest_odd,
        price_count
    FROM match_odds
    ORDER BY {sort_column} {sort_direction} NULLS LAST;
    """

    database_url = get_database_url()

    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query)
            rows = cursor.fetchall()
            columns = [description.name for description in cursor.description]

    matches: list[dict[str, Any]] = []

    for row in rows:
        match_data = {
            column: decimal_to_float(value)
            for column, value in zip(columns, row)
        }
        matches.append(match_data)

    return matches

@router.get("/matches/today", response_class=HTMLResponse)
def today_matches_page(
    request: Request,
    sort: str = Query(default="time"),
    direction: str = Query(default="asc"),
):
    """
    Display today's matches with sortable 1X2 odds.
    """
    if sort not in SORT_COLUMNS:
        sort = "time"

    if direction not in {"asc", "desc"}:
        direction = "asc"

    matches = fetch_today_matches(
        sort=sort,
        direction=direction,
    )

    next_direction = "desc" if direction == "asc" else "asc"

    return templates.TemplateResponse(
        request=request,
        name="today_matches.html",
        context={
            "matches": matches,
            "sort": sort,
            "direction": direction,
            "next_direction": next_direction,
            "page_title": "Today's Matches",
        },
    )





def fetch_match_detail(fixture_id: int) -> dict[str, Any] | None:
    """
    Fetch one fixture and all its saved odds.
    """
    database_url = get_database_url()

    fixture_query = """
    SELECT
        id,
        league_name,
        match_date,
        kickoff_time,
        home_team,
        away_team,
        home_score,
        away_score,
        match_status,
        live_minute,
        result,
        scraped_at
    FROM oddsportal_fixtures
    WHERE id = %s;
    """

    prices_query = """
    SELECT
        market_key,
        period_key,
        line_value,
        bookmaker,
        outcome_name,
        odd_value
    FROM oddsportal_prices
    WHERE fixture_id = %s
    ORDER BY
        market_key,
        period_key,
        line_value NULLS FIRST,
        outcome_name;
    """

    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(fixture_query, (fixture_id,))
            fixture_row = cursor.fetchone()

            if not fixture_row:
                return None

            fixture_columns = [description.name for description in cursor.description]
            fixture = {
                column: decimal_to_float(value)
                for column, value in zip(fixture_columns, fixture_row)
            }

            cursor.execute(prices_query, (fixture_id,))
            price_rows = cursor.fetchall()
            price_columns = [description.name for description in cursor.description]

    prices = [
        {
            column: decimal_to_float(value)
            for column, value in zip(price_columns, row)
        }
        for row in price_rows
    ]

    return {
        "fixture": fixture,
        "markets": build_match_markets(prices),
    }


def build_match_markets(prices: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Group flat odds prices into display-friendly market blocks.
    """
    markets: dict[str, Any] = {
        "1x2": {},
        "both_teams_to_score": {},
        "over_under": {},
    }

    for price in prices:
        market_key = price["market_key"]
        period_key = price["period_key"]
        line_value = price["line_value"]
        outcome_name = price["outcome_name"]
        odd_value = price["odd_value"]
        bookmaker = price["bookmaker"]

        if market_key in {"1x2", "both_teams_to_score"}:
            if period_key not in markets[market_key]:
                markets[market_key][period_key] = {
                    "bookmaker": bookmaker,
                    "values": {},
                }

            markets[market_key][period_key]["values"][outcome_name] = odd_value

        elif market_key == "over_under":
            if period_key not in markets["over_under"]:
                markets["over_under"][period_key] = {}

            normalized_line = str(line_value)

            if normalized_line not in markets["over_under"][period_key]:
                markets["over_under"][period_key][normalized_line] = {
                    "line": line_value,
                    "values": {},
                }

            markets["over_under"][period_key][normalized_line]["values"][outcome_name] = odd_value

    for period_key, lines in markets["over_under"].items():
        markets["over_under"][period_key] = sorted(
            lines.values(),
            key=lambda row: float(row["line"]),
        )

    return markets


def fetch_live_matches() -> list[dict[str, Any]]:
    """
    Fetch currently live football matches from the database.
    """
    query = """
    SELECT
        f.id AS fixture_id,
        f.league_name,
        f.match_date,
        f.kickoff_time,
        f.home_team,
        f.away_team,
        f.home_score,
        f.away_score,
        f.match_status,
        f.live_minute,

        MAX(
            CASE
                WHEN p.market_key = '1x2'
                AND p.period_key = 'live'
                AND p.outcome_name = 'home_win'
                THEN p.odd_value
            END
        ) AS home_win,

        MAX(
            CASE
                WHEN p.market_key = '1x2'
                AND p.period_key = 'live'
                AND p.outcome_name = 'draw'
                THEN p.odd_value
            END
        ) AS draw,

        MAX(
            CASE
                WHEN p.market_key = '1x2'
                AND p.period_key = 'live'
                AND p.outcome_name = 'away_win'
                THEN p.odd_value
            END
        ) AS away_win

    FROM oddsportal_fixtures f
    LEFT JOIN oddsportal_prices p
        ON p.fixture_id = f.id
    WHERE f.match_date = CURRENT_DATE
    AND f.match_status IN ('live', 'halftime')
    GROUP BY
        f.id,
        f.league_name,
        f.match_date,
        f.kickoff_time,
        f.home_team,
        f.away_team,
        f.home_score,
        f.away_score,
        f.match_status,
        f.live_minute
    ORDER BY
        f.league_name ASC,
        f.kickoff_time ASC;
    """

    database_url = get_database_url()

    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query)
            rows = cursor.fetchall()
            columns = [description.name for description in cursor.description]

    return [
        {
            column: decimal_to_float(value)
            for column, value in zip(columns, row)
        }
        for row in rows
    ]


def get_live_refresh_status(request: Request) -> dict[str, Any]:
    """
    Read the current background live refresh status from app state.
    """
    live_refresh_service = getattr(request.app.state, "live_refresh_service", None)

    if live_refresh_service is None:
        return {
            "interval_seconds": 60,
            "last_started_at": None,
            "last_completed_at": None,
            "last_error": None,
            "last_match_count": 0,
            "is_refreshing": False,
        }

    return live_refresh_service.snapshot()


def build_live_matches_payload(request: Request) -> dict[str, Any]:
    """
    Build the JSON payload used by the live page and API.
    """
    matches = fetch_live_matches()
    refresh_status = get_live_refresh_status(request)

    return {
        "matches": matches,
        "refresh": refresh_status,
    }


@router.get("/matches/live", response_class=HTMLResponse)
def live_matches_page(request: Request):
    """
    Display currently live football matches.
    """
    payload = build_live_matches_payload(request)
    initial_live_matches_json = json.dumps(jsonable_encoder(payload["matches"]))

    return templates.TemplateResponse(
        request=request,
        name="live_matches.html",
        context={
            "page_title": "Live Matches",
            "matches": payload["matches"],
            "live_refresh": payload["refresh"],
            "initial_live_matches_json": initial_live_matches_json,
        },
    )


@router.get("/api/live-matches", response_class=JSONResponse)
def live_matches_api(request: Request):
    """
    Return current live matches for in-browser silent refreshes.
    """
    return build_live_matches_payload(request)

@router.get("/matches/{fixture_id:int}", response_class=HTMLResponse)
def match_detail_page(
    request: Request,
    fixture_id: int,
):
    """
    Display the full odds detail for one fixture.
    """
    match_detail = fetch_match_detail(fixture_id)

    if match_detail is None:
        return templates.TemplateResponse(
            request=request,
            name="match_detail.html",
            context={
                "page_title": "Match not found",
                "fixture": None,
                "markets": {},
            },
            status_code=404,
        )

    return templates.TemplateResponse(
        request=request,
        name="match_detail.html",
        context={
            "page_title": "Match Detail",
            "fixture": match_detail["fixture"],
            "markets": match_detail["markets"],
        },
    )



def fetch_available_history_dates() -> list[dict[str, Any]]:
    """
    Fetch available historical match dates.

    History means dates before CURRENT_DATE.
    """
    query = """
    SELECT
        match_date,
        COUNT(*) AS match_count
    FROM oddsportal_fixtures
    WHERE match_date < CURRENT_DATE
    GROUP BY match_date
    ORDER BY match_date DESC;
    """

    database_url = get_database_url()

    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query)
            rows = cursor.fetchall()
            columns = [description.name for description in cursor.description]

    return [
        {
            column: decimal_to_float(value)
            for column, value in zip(columns, row)
        }
        for row in rows
    ]


def get_default_history_date() -> str | None:
    """
    Return the latest available historical date.
    """
    dates = fetch_available_history_dates()

    if not dates:
        return None

    return str(dates[0]["match_date"])


def validate_history_date(value: str | None) -> str | None:
    """
    Validate YYYY-MM-DD date string.
    """
    if not value:
        return get_default_history_date()

    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return get_default_history_date()

    return value


def fetch_history_matches(
    history_date: str,
    sort: str,
    direction: str,
) -> list[dict[str, Any]]:
    """
    Fetch historical matches for a selected date.
    """
    sort_column = SORT_COLUMNS.get(sort, "kickoff_time")
    sort_direction = "DESC" if direction == "desc" else "ASC"

    query = f"""
    WITH match_odds AS (
        SELECT
            f.id AS fixture_id,
            f.league_name,
            f.match_date,
            f.kickoff_time,
            f.home_team,
            f.away_team,

            MAX(
                CASE
                    WHEN p.market_key = '1x2'
                    AND p.period_key = 'full_time'
                    AND p.outcome_name = 'home_win'
                    THEN p.odd_value
                END
            ) AS home_win,

            MAX(
                CASE
                    WHEN p.market_key = '1x2'
                    AND p.period_key = 'full_time'
                    AND p.outcome_name = 'draw'
                    THEN p.odd_value
                END
            ) AS draw,

            MAX(
                CASE
                    WHEN p.market_key = '1x2'
                    AND p.period_key = 'full_time'
                    AND p.outcome_name = 'away_win'
                    THEN p.odd_value
                END
            ) AS away_win,

            COUNT(p.id) AS price_count

        FROM oddsportal_fixtures f
        LEFT JOIN oddsportal_prices p
            ON p.fixture_id = f.id
        WHERE f.match_date = %s
        GROUP BY
            f.id,
            f.league_name,
            f.match_date,
            f.kickoff_time,
            f.home_team,
            f.away_team
    )
    SELECT
        fixture_id,
        league_name,
        match_date,
        kickoff_time,
        home_team,
        away_team,
        home_win,
        draw,
        away_win,
        GREATEST(
            COALESCE(home_win, 0),
            COALESCE(draw, 0),
            COALESCE(away_win, 0)
        ) AS highest_odd,
        price_count
    FROM match_odds
    ORDER BY {sort_column} {sort_direction} NULLS LAST;
    """

    database_url = get_database_url()

    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, (history_date,))
            rows = cursor.fetchall()
            columns = [description.name for description in cursor.description]

    return [
        {
            column: decimal_to_float(value)
            for column, value in zip(columns, row)
        }
        for row in rows
    ]





@router.get("/matches/history", response_class=HTMLResponse)
def match_history_page(
    request: Request,
    date: str | None = Query(default=None),
    sort: str = Query(default="time"),
    direction: str = Query(default="asc"),
):
    """
    Display historical matches by selected date.
    """
    if sort not in SORT_COLUMNS:
        sort = "time"

    if direction not in {"asc", "desc"}:
        direction = "asc"

    available_dates = fetch_available_history_dates()
    selected_date = validate_history_date(date)

    matches: list[dict[str, Any]] = []

    if selected_date:
        matches = fetch_history_matches(
            history_date=selected_date,
            sort=sort,
            direction=direction,
        )

    next_direction = "desc" if direction == "asc" else "asc"

    return templates.TemplateResponse(
        request=request,
        name="matches_history.html",
        context={
            "page_title": "Match History",
            "matches": matches,
            "available_dates": available_dates,
            "selected_date": selected_date,
            "sort": sort,
            "direction": direction,
            "next_direction": next_direction,
        },
    )
