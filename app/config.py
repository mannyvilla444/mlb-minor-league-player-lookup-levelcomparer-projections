"""Configuration constants for the MLB level-stats app.

Everything here is public information — the MLB Stats API needs no key and no
secrets are stored anywhere in this project.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Level:
    """A baseball level we pull stats for."""

    sport_id: int
    code: str  # short label shown in the table
    name: str  # long label
    order: int  # display order; MLB first, then descending minor-league rungs


# ---------------------------------------------------------------------------
# sportId map
# ---------------------------------------------------------------------------
# These are the MLB Stats API `sportId` values (see https://statsapi.mlb.com/api/v1/sports).
# We deliberately exclude 17 (winter leagues), 21/22/23 (independent, college,
# high school) and 51 (international) — they are not part of the affiliated
# ladder this app is about.
LEVELS: tuple[Level, ...] = (
    Level(sport_id=1, code="MLB", name="Major League Baseball", order=0),
    Level(sport_id=11, code="AAA", name="Triple-A", order=1),
    Level(sport_id=12, code="AA", name="Double-A", order=2),
    Level(sport_id=13, code="A+", name="High-A", order=3),
    Level(sport_id=14, code="A", name="Single-A (Low-A)", order=4),
    Level(sport_id=15, code="A-", name="Class A Short Season (pre-2021)", order=5),
    Level(sport_id=16, code="R", name="Rookie / complex leagues", order=6),
)

LEVEL_BY_SPORT_ID: dict[int, Level] = {lvl.sport_id: lvl for lvl in LEVELS}
LEVEL_BY_CODE: dict[str, Level] = {lvl.code: lvl for lvl in LEVELS}
SPORT_IDS: tuple[int, ...] = tuple(lvl.sport_id for lvl in LEVELS)


# ---------------------------------------------------------------------------
# Level pairs offered on the Compare tab
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class LevelPair:
    """Two levels to difference. Always ``high`` minus ``low``."""

    key: str
    high_sport_id: int
    low_sport_id: int

    @property
    def high(self) -> Level:
        return LEVEL_BY_SPORT_ID[self.high_sport_id]

    @property
    def low(self) -> Level:
        return LEVEL_BY_SPORT_ID[self.low_sport_id]

    @property
    def label(self) -> str:
        return f"{self.high.code} − {self.low.code}"

    @property
    def sport_ids(self) -> tuple[int, int]:
        return (self.high_sport_id, self.low_sport_id)


LEVEL_PAIRS: tuple[LevelPair, ...] = (
    LevelPair(key="mlb-aaa", high_sport_id=1, low_sport_id=11),
    LevelPair(key="mlb-aa", high_sport_id=1, low_sport_id=12),
    LevelPair(key="mlb-aplus", high_sport_id=1, low_sport_id=13),
    LevelPair(key="aaa-aa", high_sport_id=11, low_sport_id=12),
    LevelPair(key="aa-aplus", high_sport_id=12, low_sport_id=13),
)

PAIR_BY_KEY: dict[str, LevelPair] = {p.key: p for p in LEVEL_PAIRS}
DEFAULT_PAIR_KEY: str = "mlb-aaa"

# Minimum plate appearances at the LOWER level for a player to count toward the
# mean and median. Checked on the lower level only: a differential built on a
# 40-PA Triple-A stint is noise no matter how many MLB PA sit on the other side.
DEFAULT_MIN_PA: int = int(os.environ.get("MLB_MIN_PA", "120"))

# Guard rail on the bulk form so one paste cannot fire thousands of requests.
MAX_BULK_PLAYERS: int = int(os.environ.get("MLB_MAX_BULK_PLAYERS", "60"))


# ---------------------------------------------------------------------------
# Translations tab
# ---------------------------------------------------------------------------
# A static, author-supplied reference page served verbatim from app/static.
# Nothing in the app reads these numbers — it is a document, not a data source.
TRANSLATIONS_FILENAME: str = "minor-league-to-mlb-stat-translations.html"
TRANSLATIONS_PATH: Path = PROJECT_ROOT / "app" / "static" / TRANSLATIONS_FILENAME
TRANSLATIONS_URL: str = f"/static/{TRANSLATIONS_FILENAME}"


# ---------------------------------------------------------------------------
# HTTP / caching behaviour
# ---------------------------------------------------------------------------
REQUEST_TIMEOUT_SECONDS: float = float(os.environ.get("MLB_TIMEOUT", "12"))
MAX_RETRIES: int = int(os.environ.get("MLB_MAX_RETRIES", "3"))
RETRY_BACKOFF_SECONDS: float = float(os.environ.get("MLB_RETRY_BACKOFF", "0.75"))

# Never fire more than this many concurrent requests at the MLB API.
MAX_CONCURRENT_REQUESTS: int = int(os.environ.get("MLB_MAX_CONCURRENCY", "3"))

# Cache API JSON for at least 12 hours, per the brief.
CACHE_TTL_SECONDS: int = int(os.environ.get("MLB_CACHE_TTL", str(12 * 60 * 60)))
CACHE_PATH: Path = Path(os.environ.get("MLB_CACHE_PATH", str(PROJECT_ROOT / "cache.sqlite3")))

# The "first N MLB PA" marker on the results page.
MLB_PA_MILESTONE: int = int(os.environ.get("MLB_PA_MILESTONE", "900"))
