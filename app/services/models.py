"""Typed containers passed between the client, the transforms and the templates."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:  # avoids a circular import at runtime
    from app.services.distribution import Distribution


@dataclass(frozen=True)
class PlayerMatch:
    """One candidate returned by a name lookup."""

    person_id: int
    full_name: str
    position: Optional[str] = None
    current_team: Optional[str] = None
    mlb_debut_date: Optional[str] = None

    @property
    def subtitle(self) -> str:
        bits = [b for b in (self.position, self.current_team) if b]
        return " · ".join(bits)


@dataclass(frozen=True)
class SeasonLevelRow:
    """One season × level line of hitting stats."""

    season: str
    sport_id: int
    level_code: str
    level_name: str
    level_order: int
    team: Optional[str]
    plate_appearances: Optional[int]
    ops: Optional[float]
    slg: Optional[float]
    walk_rate: Optional[float]  # 0-1 fraction, not a percent
    strikeout_rate: Optional[float]  # 0-1 fraction, not a percent
    walks: Optional[int] = None
    strikeouts: Optional[int] = None
    # Component totals, kept so career rollups can be recomputed exactly.
    at_bats: Optional[int] = None
    hits: Optional[int] = None
    total_bases: Optional[int] = None
    hit_by_pitch: Optional[int] = None
    sac_flies: Optional[int] = None
    # Running MLB PA total, filled in only for MLB rows.
    cumulative_mlb_pa: Optional[int] = None


@dataclass(frozen=True)
class LevelSummary:
    """Career totals for a single level."""

    level_code: str
    level_name: str
    level_order: int
    seasons: int
    plate_appearances: int
    ops: Optional[float]
    slg: Optional[float]
    walk_rate: Optional[float]
    strikeout_rate: Optional[float]
    method: str  # human-readable description of how OPS/SLG were derived


@dataclass
class PlayerReport:
    """Everything the results page needs."""

    player: PlayerMatch
    rows: list[SeasonLevelRow] = field(default_factory=list)
    by_level: list[LevelSummary] = field(default_factory=list)
    milestone_pa: int = 900
    milestone_season: Optional[str] = None
    milestone_total_pa: Optional[int] = None
    # sportIds that failed to load, so the page can say so instead of silently
    # showing an incomplete picture.
    failed_sport_ids: list[int] = field(default_factory=list)

    @property
    def has_rows(self) -> bool:
        return bool(self.rows)


class MLBAPIError(RuntimeError):
    """Raised when the MLB Stats API cannot be reached or returns garbage."""


# ----------------------------------------------------------------------
# Compare tab
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class StatLine:
    """One level's career line for one player, as used in a comparison."""

    level_code: str
    plate_appearances: int
    ops: Optional[float]
    slg: Optional[float]
    walk_rate: Optional[float]
    strikeout_rate: Optional[float]


@dataclass(frozen=True)
class DiffRow:
    """One player's high-level-minus-low-level differential."""

    player: PlayerMatch
    high: Optional[StatLine]
    low: Optional[StatLine]
    deltas: dict[str, Optional[float]] = field(default_factory=dict)
    included: bool = False
    note: Optional[str] = None  # why this row sits out of the mean/median

    @property
    def has_both_levels(self) -> bool:
        return self.high is not None and self.low is not None


@dataclass(frozen=True)
class UnresolvedEntry:
    """A line from the submitted list that did not become exactly one player."""

    query: str
    reason: str  # "not found" | "ambiguous" | "lookup failed"
    candidates: list[PlayerMatch] = field(default_factory=list)


@dataclass
class ComparisonResult:
    """Everything the Compare results table needs."""

    pair_key: str
    high_code: str
    low_code: str
    min_pa: int
    apply_filter: bool
    rows: list[DiffRow] = field(default_factory=list)
    unresolved: list[UnresolvedEntry] = field(default_factory=list)
    # One Distribution per metric key: centre, spread, and the box-plot summary.
    stats: dict[str, "Distribution"] = field(default_factory=dict)
    failed_sport_ids: list[int] = field(default_factory=list)

    # Convenience views, so templates can say result.mean["ops"] as before.
    @property
    def mean(self) -> dict[str, Optional[float]]:
        return {k: d.mean for k, d in self.stats.items()}

    @property
    def median(self) -> dict[str, Optional[float]]:
        return {k: d.median for k, d in self.stats.items()}

    @property
    def counts(self) -> dict[str, int]:
        return {k: d.n for k, d in self.stats.items()}

    @property
    def included_count(self) -> int:
        return sum(1 for r in self.rows if r.included)

    @property
    def excluded_count(self) -> int:
        return sum(1 for r in self.rows if not r.included)
