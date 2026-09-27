"""The author-supplied league → MLB translation deltas, as data.

These are the same eight rows shown on the Translations tab. They live here in
Python because the app has to *compute* with them; the static HTML page is the
human-readable copy. ``tests/test_translations_data.py`` parses the numbers back
out of that HTML and asserts they match this table, so the two cannot drift.

Two unit conventions, and getting them wrong silently ruins every projection:

* ``d_ops`` and ``d_slg`` are in **rate-stat points** and apply directly to an
  OPS or SLG (.750 + -.154 = .596).
* ``d_bb`` and ``d_k`` are in **percentage points** and must be divided by 100
  before touching a rate held as a fraction (0.10 + -3.0/100 = 0.07).

Coverage note: these eight are *every* affiliated league at Triple-A, Double-A
and High-A, so a current-era hitter at one of those levels always lands in a
league this table knows — unless the API reports a league name we do not
recognise, which shows up as an unmatched row rather than a silent default.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class LeagueTranslation:
    """One league's mean change from its own numbers to MLB numbers."""

    key: str
    name: str  # display name
    short: str  # as written on the Translations page
    level_code: str  # MLB / AAA / AA / A+ vocabulary used elsewhere in the app
    sport_id: int
    d_ops: float  # OPS points
    d_slg: float  # SLG points
    d_bb: float  # percentage points
    d_k: float  # percentage points
    aliases: tuple[str, ...] = ()

    @property
    def d_bb_fraction(self) -> float:
        return self.d_bb / 100.0

    @property
    def d_k_fraction(self) -> float:
        return self.d_k / 100.0

    def delta(self, metric: str) -> float:
        """The delta for a metric key, already in the app's internal units."""
        return {
            "ops": self.d_ops,
            "slg": self.d_slg,
            "bb": self.d_bb_fraction,
            "k": self.d_k_fraction,
        }[metric]


# The 2021 season is the trap here: MiLB ran that year under placeholder names
# ("Triple-A East", "High-A West" …) before the historic names came back in
# 2022. Without these aliases a 2021 stint silently fails to match its league.
TRANSLATIONS: tuple[LeagueTranslation, ...] = (
    LeagueTranslation(
        key="international", name="International League", short="International",
        level_code="AAA", sport_id=11,
        d_ops=-0.154, d_slg=-0.094, d_bb=-3.0, d_k=3.2,
        aliases=("international", "triple a east", "aaa east"),
    ),
    LeagueTranslation(
        key="pacific-coast", name="Pacific Coast League", short="Pacific",
        level_code="AAA", sport_id=11,
        d_ops=-0.202, d_slg=-0.127, d_bb=-3.3, d_k=4.2,
        aliases=("pacific coast", "pacific", "triple a west", "aaa west", "pcl"),
    ),
    LeagueTranslation(
        key="eastern", name="Eastern League", short="Eastern",
        level_code="AA", sport_id=12,
        d_ops=-0.117, d_slg=-0.073, d_bb=-2.0, d_k=1.6,
        aliases=("eastern", "double a northeast", "aa northeast"),
    ),
    LeagueTranslation(
        key="southern", name="Southern League", short="Southern",
        level_code="AA", sport_id=12,
        d_ops=-0.073, d_slg=-0.034, d_bb=-1.9, d_k=2.9,
        aliases=("southern", "double a south", "aa south"),
    ),
    LeagueTranslation(
        key="texas", name="Texas League", short="Texas",
        level_code="AA", sport_id=12,
        d_ops=-0.126, d_slg=-0.074, d_bb=-2.3, d_k=1.8,
        aliases=("texas", "double a central", "aa central"),
    ),
    LeagueTranslation(
        key="midwest", name="Midwest League", short="Midwest",
        level_code="A+", sport_id=13,
        d_ops=-0.104, d_slg=-0.058, d_bb=-2.2, d_k=3.2,
        aliases=("midwest", "high a central", "a central"),
    ),
    LeagueTranslation(
        key="northwest", name="Northwest League", short="Northwest",
        level_code="A+", sport_id=13,
        d_ops=-0.101, d_slg=-0.058, d_bb=-1.6, d_k=3.5,
        aliases=("northwest", "high a west"),
    ),
    LeagueTranslation(
        key="south-atlantic", name="South Atlantic League", short="South Atlantic",
        level_code="A+", sport_id=13,
        d_ops=-0.145, d_slg=-0.086, d_bb=-2.7, d_k=2.7,
        aliases=("south atlantic", "sally", "high a east"),
    ),
)

TRANSLATION_BY_KEY: dict[str, LeagueTranslation] = {t.key: t for t in TRANSLATIONS}

# sportIds a projection draws on, and the level order it reports them in:
# closest to the majors first, because that is the order a bat climbs in reverse
# and the order a reader scans for "how ready is he".
PROJECTION_SPORT_IDS: tuple[int, ...] = (11, 12, 13)
PROJECTION_LEVEL_CODES: tuple[str, ...] = ("AAA", "AA", "A+")
LEVEL_ORDER: dict[str, int] = {code: i for i, code in enumerate(PROJECTION_LEVEL_CODES)}
MLB_SPORT_ID = 1


def normalise_league_name(name: Optional[str]) -> str:
    """Fold an API league name to a comparable key.

    ``"Pacific Coast League"`` -> ``"pacific coast"``; ``"Triple-A East"`` ->
    ``"triple a east"``. Punctuation and the word "league" carry no meaning.
    """
    if not name:
        return ""
    text = re.sub(r"[^a-z0-9]+", " ", str(name).lower())
    text = re.sub(r"\bleagues?\b", " ", text)
    return re.sub(r"\s+", " ", text).strip()


_BY_ALIAS: dict[str, LeagueTranslation] = {}
for _t in TRANSLATIONS:
    for _alias in (_t.name, _t.short, *_t.aliases):
        _BY_ALIAS[normalise_league_name(_alias)] = _t


def find_translation(league_name: Optional[str]) -> Optional[LeagueTranslation]:
    """The translation for an API league name, or ``None`` if unrecognised.

    Unrecognised is returned honestly rather than guessed at: applying the wrong
    league's delta would be worse than declining to project.
    """
    return _BY_ALIAS.get(normalise_league_name(league_name))
