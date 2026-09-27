"""FastAPI app: search a player, see MLB + MiLB hitting stats by level."""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import FastAPI, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import __version__
from app.config import (
    DEFAULT_MIN_PA,
    DEFAULT_PAIR_KEY,
    LEVEL_PAIRS,
    LEVELS,
    MAX_BULK_PLAYERS,
    MLB_PA_MILESTONE,
    PAIR_BY_KEY,
    PROJECT_ROOT,
    TRANSLATIONS_PATH,
    TRANSLATIONS_URL,
)
from app.services.translations import TRANSLATIONS
from app.services import (
    boxplot,
    compare as compare_service,
    formatting,
    projection as projection_service,
)
from app.services.mlb_client import MLBClient
from app.services.models import MLBAPIError
from app.services.transforms import build_report

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="MLB Level Stats", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=PROJECT_ROOT / "app" / "static"), name="static")

templates = Jinja2Templates(directory=str(PROJECT_ROOT / "app" / "templates"))

client = MLBClient()


# ----------------------------------------------------------------------
# Jinja filters — the formatting itself lives in services/formatting.py so the
# SVG box plots render numbers identically to the tables.
# ----------------------------------------------------------------------
def sign_class(value: Optional[float], good_direction: int = 1) -> str:
    """Colour a delta by whether it is *better* at the higher level, not by sign.

    K% passes good_direction=-1, so a smaller strikeout rate up top reads green
    the same way a bigger OPS does.
    """
    if value is None or value == 0:
        return ""
    return "pos" if value * good_direction > 0 else "neg"


templates.env.filters["rate"] = formatting.fmt_rate
templates.env.filters["slash"] = formatting.fmt_slash
templates.env.filters["int_"] = formatting.fmt_int
templates.env.filters["delta"] = formatting.fmt_delta
templates.env.filters["spread"] = formatting.fmt_spread
templates.env.filters["sign_class"] = sign_class
templates.env.filters["boxplot"] = boxplot.svg_boxplot
templates.env.filters["unit_label"] = formatting.unit_label


def _base_context() -> dict:
    return {"levels": LEVELS, "milestone": MLB_PA_MILESTONE, "version": __version__}


def _render(request: Request, template: str, context: dict, status: int = 200):
    return templates.TemplateResponse(
        request, template, {**_base_context(), **context}, status_code=status
    )


def _error_page(request: Request, message: str, retry_url: str, status: int = 502):
    return _render(
        request, "error.html", {"message": message, "retry_url": retry_url}, status
    )


# ----------------------------------------------------------------------
# Routes
# ----------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return _render(request, "index.html", {})


@app.get("/search", response_class=HTMLResponse)
def search(request: Request, q: str = Query("", alias="q")):
    query = q.strip()
    if not query:
        return RedirectResponse("/", status_code=303)

    try:
        matches = client.find_players(query)
    except MLBAPIError as exc:
        logger.exception("player lookup failed")
        return _error_page(
            request,
            f"The MLB Stats API did not answer while searching for “{query}”. {exc}",
            f"/search?q={query}",
        )

    if not matches:
        return _render(request, "matches.html", {"query": query, "matches": []}, 404)
    if len(matches) == 1:
        return RedirectResponse(f"/player/{matches[0].person_id}", status_code=303)

    return _render(request, "matches.html", {"query": query, "matches": matches})


@app.get("/player/{person_id}", response_class=HTMLResponse)
def player(request: Request, person_id: int):
    try:
        person = client.get_person(person_id)
        if person is None:
            return _render(
                request, "matches.html", {"query": str(person_id), "matches": []}, 404
            )
        payloads, failed = client.all_levels_hitting(person_id)
    except MLBAPIError as exc:
        logger.exception("stat fetch failed for %s", person_id)
        return _error_page(
            request,
            f"Could not load stats for player {person_id}. {exc}",
            f"/player/{person_id}",
        )

    report = build_report(person, payloads, failed)
    return _render(request, "results.html", {"report": report})


# ----------------------------------------------------------------------
# Compare tab
# ----------------------------------------------------------------------
def _compare_context(
    players: str = "",
    pair_key: str = DEFAULT_PAIR_KEY,
    min_pa: int = DEFAULT_MIN_PA,
    apply_filter: bool = True,
    result=None,
    warning: Optional[str] = None,
) -> dict:
    return {
        "pairs": LEVEL_PAIRS,
        "metrics": compare_service.METRICS,
        "players_text": players,
        "pair_key": pair_key,
        "min_pa": min_pa,
        "apply_filter": apply_filter,
        "result": result,
        "warning": warning,
        "max_players": MAX_BULK_PLAYERS,
    }


@app.get("/compare", response_class=HTMLResponse)
def compare_form(request: Request):
    return _render(request, "compare.html", _compare_context())


@app.post("/compare", response_class=HTMLResponse)
def compare_run(
    request: Request,
    players: str = Form(""),
    pair: str = Form(DEFAULT_PAIR_KEY),
    min_pa: int = Form(DEFAULT_MIN_PA),
    exclude: Optional[str] = Form(None),
):
    level_pair = PAIR_BY_KEY.get(pair) or PAIR_BY_KEY[DEFAULT_PAIR_KEY]
    apply_filter = exclude is not None
    min_pa = max(0, min_pa)

    queries = compare_service.parse_player_list(players)
    if not queries:
        return _render(
            request,
            "compare.html",
            _compare_context(
                players, level_pair.key, min_pa, apply_filter,
                warning="Add at least one player — one name per line.",
            ),
        )

    warning = None
    if len(queries) > MAX_BULK_PLAYERS:
        warning = (
            f"{len(queries)} names submitted; only the first {MAX_BULK_PLAYERS} were "
            "used. Raise MLB_MAX_BULK_PLAYERS if you need more."
        )
        queries = queries[:MAX_BULK_PLAYERS]

    try:
        result = compare_service.build_comparison(
            client, "\n".join(queries), level_pair, min_pa, apply_filter
        )
    except MLBAPIError as exc:
        logger.exception("comparison failed")
        return _error_page(request, f"Could not build the comparison. {exc}", "/compare")

    return _render(
        request,
        "compare.html",
        _compare_context(players, level_pair.key, min_pa, apply_filter, result, warning),
    )


# ----------------------------------------------------------------------
# Translations tab — a static document, framed so the app nav survives
# ----------------------------------------------------------------------
@app.get("/translations", response_class=HTMLResponse)
def translations(request: Request):
    if not TRANSLATIONS_PATH.is_file():
        return _error_page(
            request,
            "The translations page is missing. Expected to find "
            f"{TRANSLATIONS_PATH.name} in app/static/.",
            "/translations",
            status=404,
        )
    return _render(request, "translations.html", {"doc_url": TRANSLATIONS_URL})


# ----------------------------------------------------------------------
# Projection tab
# ----------------------------------------------------------------------
def _projection_context(
    players: str = "",
    min_pa: int = DEFAULT_MIN_PA,
    apply_filter: bool = True,
    result=None,
    warning: Optional[str] = None,
) -> dict:
    return {
        "metrics": projection_service.METRICS,
        "translations": TRANSLATIONS,
        "players_text": players,
        "min_pa": min_pa,
        "apply_filter": apply_filter,
        "result": result,
        "warning": warning,
        "max_players": MAX_BULK_PLAYERS,
    }


@app.get("/projection", response_class=HTMLResponse)
def projection_form(request: Request):
    return _render(request, "projection.html", _projection_context())


@app.post("/projection", response_class=HTMLResponse)
def projection_run(
    request: Request,
    players: str = Form(""),
    min_pa: int = Form(DEFAULT_MIN_PA),
    exclude: Optional[str] = Form(None),
):
    apply_filter = exclude is not None
    min_pa = max(0, min_pa)

    queries = compare_service.parse_player_list(players)
    if not queries:
        return _render(
            request,
            "projection.html",
            _projection_context(
                players, min_pa, apply_filter,
                warning="Add at least one player — one name per line.",
            ),
        )

    warning = None
    if len(queries) > MAX_BULK_PLAYERS:
        warning = (
            f"{len(queries)} names submitted; only the first {MAX_BULK_PLAYERS} were "
            "used. Raise MLB_MAX_BULK_PLAYERS if you need more."
        )
        queries = queries[:MAX_BULK_PLAYERS]

    try:
        result = projection_service.build_projections(
            client, "\n".join(queries), min_pa, apply_filter
        )
    except MLBAPIError as exc:
        logger.exception("projection failed")
        return _error_page(request, f"Could not build the projections. {exc}", "/projection")

    return _render(
        request,
        "projection.html",
        _projection_context(players, min_pa, apply_filter, result, warning),
    )


@app.get("/healthz")
def healthz() -> dict[str, str]:
    """Also reports which build and which folder is actually serving."""
    return {
        "status": "ok",
        "version": __version__,
        "running_from": str(PROJECT_ROOT),
        "has_compare_tab": "yes",
        "has_translations_tab": "yes" if TRANSLATIONS_PATH.is_file() else "missing",
    }
