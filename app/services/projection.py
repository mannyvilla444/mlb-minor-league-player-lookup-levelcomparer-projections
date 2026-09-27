"""Project MLB numbers from a hitter's minor-league line, one level at a time.

For every player the app produces **up to three projections** — one for Triple-A,
one for Double-A, one for High-A — because a bat leaves a different record at
each rung and each rung has its own translation. Marcelo Mayer with time in the
International, Eastern and a High-A league gets three lines, not one.

Within a level, the league is chosen by plate appearances: a player who split
Triple-A between the International and Pacific Coast leagues is projected from
whichever of the two he has more PA in, using *that* league's own line and
*that* league's own delta. The other stint is reported as an alternate rather
than blended in, because blending would apply one league's delta to the other
league's plate appearances.

    projected MLB stat = career stat in that league + that league's mean Δ

Everything else here is honesty about the limits: unrecognised leagues are named
rather than substituted, impossible rates are clamped and flagged, and when the
player already has MLB plate appearances each level's projection is scored
against them — so you can see which level translates best rather than assuming.

This is a league-average adjustment, not a forecast: no age, park, competition
or playing-time model is involved.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional, Protocol

from app.services.distribution import Distribution, describe
from app.services.models import PlayerMatch, StatLine, UnresolvedEntry
from app.services.transforms import aggregate_splits, extract_splits
from app.services.translations import (
    LEVEL_ORDER,
    MLB_SPORT_ID,
    PROJECTION_LEVEL_CODES,
    PROJECTION_SPORT_IDS,
    TRANSLATION_BY_KEY,
    find_translation,
)

logger = logging.getLogger(__name__)

# (key, label, formatting kind, which sign is "better")
METRICS: tuple[tuple[str, str, str, int], ...] = (
    ("ops", "OPS", "slash", 1),
    ("slg", "SLG", "slash", 1),
    ("bb", "BB%", "rate", 1),
    ("k", "K%", "rate", -1),
)
METRIC_KEYS: tuple[str, ...] = tuple(m[0] for m in METRICS)

# A projected rate outside these bounds is arithmetic, not baseball.
BOUNDS: dict[str, tuple[float, float]] = {
    "ops": (0.0, 5.0),
    "slg": (0.0, 4.0),
    "bb": (0.0, 1.0),
    "k": (0.0, 1.0),
}

# A runner-up league this close to the winner is worth a second look.
CLOSE_CALL_RATIO = 0.85


class SupportsBulkFetch(Protocol):
    def bulk_find_players(
        self, queries: Iterable[str]
    ) -> tuple[dict[str, list[PlayerMatch]], list[str]]: ...

    def bulk_levels_hitting(
        self, person_ids: Iterable[int], sport_ids: Iterable[int]
    ) -> tuple[dict[int, dict[int, dict[str, Any]]], list[tuple[int, int]]]: ...


# ----------------------------------------------------------------------
# containers
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class LeagueLine:
    """A player's whole career in one league."""

    league_key: str
    plate_appearances: int
    ops: Optional[float]
    slg: Optional[float]
    walk_rate: Optional[float]
    strikeout_rate: Optional[float]
    seasons: tuple[str, ...] = ()

    @property
    def translation(self):
        return TRANSLATION_BY_KEY[self.league_key]

    @property
    def league_name(self) -> str:
        return self.translation.name

    @property
    def short_name(self) -> str:
        return self.translation.short

    @property
    def level_code(self) -> str:
        return self.translation.level_code

    @property
    def span(self) -> str:
        if not self.seasons:
            return ""
        first, last = self.seasons[0], self.seasons[-1]
        return first if first == last else f"{first}–{last}"

    def value(self, metric: str) -> Optional[float]:
        return {
            "ops": self.ops,
            "slg": self.slg,
            "bb": self.walk_rate,
            "k": self.strikeout_rate,
        }[metric]


@dataclass(frozen=True)
class LevelProjection:
    """One level's worth of projection for one player."""

    level_code: str
    source: LeagueLine
    projected: dict[str, Optional[float]] = field(default_factory=dict)
    residual: dict[str, Optional[float]] = field(default_factory=dict)
    alternates: tuple[LeagueLine, ...] = ()  # other leagues at this same level
    clamped: tuple[str, ...] = ()
    included: bool = False
    note: Optional[str] = None

    @property
    def level_order(self) -> int:
        return LEVEL_ORDER[self.level_code]

    @property
    def is_close_call(self) -> bool:
        """True when a league at the same level nearly took the slot."""
        if not self.alternates:
            return False
        return (
            self.alternates[0].plate_appearances
            >= self.source.plate_appearances * CLOSE_CALL_RATIO
        )


@dataclass(frozen=True)
class PlayerProjection:
    """One player: a projection per level they have a record at."""

    player: PlayerMatch
    levels: tuple[LevelProjection, ...] = ()
    actual: Optional[StatLine] = None
    unmatched_leagues: tuple[str, ...] = ()
    note: Optional[str] = None

    @property
    def has_levels(self) -> bool:
        return bool(self.levels)

    @property
    def row_count(self) -> int:
        """Rows this player occupies in the table (never fewer than one)."""
        return max(1, len(self.levels))

    @property
    def has_actual(self) -> bool:
        return self.actual is not None

    @property
    def included_levels(self) -> tuple[LevelProjection, ...]:
        return tuple(level for level in self.levels if level.included)


@dataclass
class ProjectionResult:
    min_pa: int
    apply_filter: bool
    players: list[PlayerProjection] = field(default_factory=list)
    unresolved: list[UnresolvedEntry] = field(default_factory=list)
    # Residuals are kept per level: the whole point is seeing which rung
    # translates best, which a pooled number would hide.
    residuals: dict[str, dict[str, Distribution]] = field(default_factory=dict)
    mean_abs_residual: dict[str, dict[str, Optional[float]]] = field(default_factory=dict)
    failed_sport_ids: list[int] = field(default_factory=list)

    @property
    def projected_line_count(self) -> int:
        return sum(len(p.included_levels) for p in self.players)

    @property
    def projected_player_count(self) -> int:
        return sum(1 for p in self.players if p.included_levels)

    @property
    def skipped_line_count(self) -> int:
        return sum(
            1 for p in self.players for level in p.levels if not level.included
        )

    @property
    def scored_player_count(self) -> int:
        return sum(1 for p in self.players if p.has_actual and p.included_levels)

    @property
    def levels_with_scores(self) -> list[str]:
        return [
            code
            for code in PROJECTION_LEVEL_CODES
            if self.residuals.get(code, {}).get("ops") is not None
            and self.residuals[code]["ops"].n > 0
        ]


# ----------------------------------------------------------------------
# league aggregation
# ----------------------------------------------------------------------
def league_lines(payloads: dict[int, dict[str, Any]]) -> tuple[list[LeagueLine], list[str]]:
    """Every recognised minor league this player appeared in, biggest PA first.

    Splits are bucketed by league rather than by level because the deltas are
    per league. Returns ``(lines, unrecognised league names)``.
    """
    buckets: dict[str, list[dict[str, Any]]] = {}
    seasons: dict[str, set[str]] = {}
    unmatched: dict[str, None] = {}

    for sport_id, payload in payloads.items():
        if sport_id not in PROJECTION_SPORT_IDS:
            continue
        for split in extract_splits(payload):
            name = (split.get("league") or {}).get("name")
            translation = find_translation(name)
            if translation is None:
                if name:
                    unmatched.setdefault(str(name), None)
                continue
            buckets.setdefault(translation.key, []).append(split)
            if split.get("season"):
                seasons.setdefault(translation.key, set()).add(str(split["season"]))

    lines: list[LeagueLine] = []
    for key, splits in buckets.items():
        aggregated = aggregate_splits(splits)
        if aggregated is None:
            continue
        lines.append(
            LeagueLine(
                league_key=key,
                plate_appearances=aggregated["plate_appearances"],
                ops=aggregated["ops"],
                slg=aggregated["slg"],
                walk_rate=aggregated["walk_rate"],
                strikeout_rate=aggregated["strikeout_rate"],
                seasons=tuple(sorted(seasons.get(key, set()))),
            )
        )

    lines.sort(key=lambda line: (-line.plate_appearances, line.league_key))
    return lines, list(unmatched)


def lines_by_level(lines: list[LeagueLine]) -> dict[str, list[LeagueLine]]:
    """Group league lines under AAA / AA / A+, each still sorted by PA."""
    grouped: dict[str, list[LeagueLine]] = {}
    for line in lines:
        grouped.setdefault(line.level_code, []).append(line)
    for level_lines in grouped.values():
        level_lines.sort(key=lambda line: (-line.plate_appearances, line.league_key))
    return grouped


def mlb_line(payloads: dict[int, dict[str, Any]]) -> Optional[StatLine]:
    """The player's actual MLB career line, if they have one."""
    aggregated = aggregate_splits(extract_splits(payloads.get(MLB_SPORT_ID, {})))
    if aggregated is None:
        return None
    return StatLine(
        level_code="MLB",
        plate_appearances=aggregated["plate_appearances"],
        ops=aggregated["ops"],
        slg=aggregated["slg"],
        walk_rate=aggregated["walk_rate"],
        strikeout_rate=aggregated["strikeout_rate"],
    )


# ----------------------------------------------------------------------
# the projection itself
# ----------------------------------------------------------------------
def project(line: LeagueLine) -> tuple[dict[str, Optional[float]], tuple[str, ...]]:
    """Apply a league's deltas. Returns ``(projected, clamped metric keys)``."""
    projected: dict[str, Optional[float]] = {}
    clamped: list[str] = []
    for metric in METRIC_KEYS:
        observed = line.value(metric)
        if observed is None:
            projected[metric] = None
            continue
        raw = observed + line.translation.delta(metric)
        low, high = BOUNDS[metric]
        bounded = min(max(raw, low), high)
        if abs(bounded - raw) > 1e-12:
            clamped.append(metric)
        projected[metric] = bounded
    return projected, tuple(clamped)


def _actual_value(actual: Optional[StatLine], metric: str) -> Optional[float]:
    if actual is None:
        return None
    return {
        "ops": actual.ops,
        "slg": actual.slg,
        "bb": actual.walk_rate,
        "k": actual.strikeout_rate,
    }[metric]


def level_projection(
    level_code: str,
    level_lines: list[LeagueLine],
    actual: Optional[StatLine],
    min_pa: int,
    apply_filter: bool,
) -> LevelProjection:
    """Project one level from its dominant league."""
    source, alternates = level_lines[0], tuple(level_lines[1:])

    if apply_filter and source.plate_appearances < min_pa:
        return LevelProjection(
            level_code=level_code,
            source=source,
            alternates=alternates,
            note=f"{source.plate_appearances} PA < {min_pa}",
        )

    projected, clamped = project(source)
    residual = {
        metric: (
            _actual_value(actual, metric) - projected[metric]
            if _actual_value(actual, metric) is not None and projected.get(metric) is not None
            else None
        )
        for metric in METRIC_KEYS
    }
    return LevelProjection(
        level_code=level_code,
        source=source,
        projected=projected,
        residual=residual,
        alternates=alternates,
        clamped=clamped,
        included=True,
    )


def player_projection(
    player: PlayerMatch,
    payloads: dict[int, dict[str, Any]],
    min_pa: int,
    apply_filter: bool,
) -> PlayerProjection:
    """One projection per level the player has a recognised record at."""
    lines, unmatched = league_lines(payloads)
    actual = mlb_line(payloads)
    grouped = lines_by_level(lines)

    if not grouped:
        note = "no AAA / AA / A+ record in a recognised league" + (
            f" (saw: {', '.join(unmatched)})" if unmatched else ""
        )
        return PlayerProjection(
            player=player,
            actual=actual,
            unmatched_leagues=tuple(unmatched),
            note=note,
        )

    levels = tuple(
        level_projection(code, grouped[code], actual, min_pa, apply_filter)
        for code in PROJECTION_LEVEL_CODES
        if code in grouped
    )
    return PlayerProjection(
        player=player,
        levels=levels,
        actual=actual,
        unmatched_leagues=tuple(unmatched),
    )


def summarise_residuals(
    players: list[PlayerProjection],
) -> tuple[dict[str, dict[str, Distribution]], dict[str, dict[str, Optional[float]]]]:
    """Residuals per level, so the rungs can be compared against each other.

    Two figures per metric because they answer different questions: the mean
    residual is **bias** (is this level's translation systematically high or
    low?) and the mean absolute residual is **typical miss**. A level can have
    near-zero bias and still be wrong about every individual player.
    """
    residuals: dict[str, dict[str, Distribution]] = {}
    mean_abs: dict[str, dict[str, Optional[float]]] = {}

    for code in PROJECTION_LEVEL_CODES:
        per_metric: dict[str, Distribution] = {}
        per_metric_abs: dict[str, Optional[float]] = {}
        for metric in METRIC_KEYS:
            values = [
                level.residual.get(metric)
                for player in players
                if player.has_actual
                for level in player.levels
                if level.included and level.level_code == code
                and level.residual.get(metric) is not None
            ]
            per_metric[metric] = describe(values)
            per_metric_abs[metric] = (
                sum(abs(v) for v in values) / len(values) if values else None
            )
        residuals[code] = per_metric
        mean_abs[code] = per_metric_abs

    return residuals, mean_abs


# ----------------------------------------------------------------------
# orchestration
# ----------------------------------------------------------------------
def build_projections(
    client: SupportsBulkFetch,
    raw_text: str,
    min_pa: int,
    apply_filter: bool,
) -> ProjectionResult:
    from app.services.compare import parse_player_list, resolve

    queries = parse_player_list(raw_text)
    players, unresolved = resolve(client, queries)

    result = ProjectionResult(min_pa=min_pa, apply_filter=apply_filter, unresolved=unresolved)
    if not players:
        result.residuals, result.mean_abs_residual = summarise_residuals([])
        return result

    sport_ids = (MLB_SPORT_ID, *PROJECTION_SPORT_IDS)
    payloads, failed = client.bulk_levels_hitting(
        [p.person_id for p in players], sport_ids
    )

    result.players = [
        player_projection(player, payloads.get(player.person_id, {}), min_pa, apply_filter)
        for player in players
    ]
    result.residuals, result.mean_abs_residual = summarise_residuals(result.players)
    result.failed_sport_ids = sorted({sid for _, sid in failed})
    return result
