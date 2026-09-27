"""Project MLB numbers from a hitter's minor-league line.

The method, in one line: find the league the player took the most plate
appearances in across Triple-A, Double-A and High-A; take their **career** line
in that league; add that league's supplied mean delta to each of the four
metrics.

    projected MLB stat = career stat in the dominant league + that league's mean Δ

Everything this module does is that plus honesty about its limits:

* The dominant league is chosen by PA, and the runners-up are reported so a
  near-tie is visible rather than hidden.
* A league the translation table does not recognise is never quietly swapped for
  a level average — the row says so and sits out.
* Rates are clamped to sane ranges (a 1.5% walk rate minus 3.3 points is not a
  negative walk rate), and any clamp is flagged on the row.
* When the player already has MLB plate appearances, their real line is shown
  next to the projection and the residual reported, so the translation can be
  judged rather than trusted.

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
    MLB_SPORT_ID,
    PROJECTION_SPORT_IDS,
    TRANSLATION_BY_KEY,
    LeagueTranslation,
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
    """A player's whole career in one minor league."""

    translation: LeagueTranslation
    plate_appearances: int
    ops: Optional[float]
    slg: Optional[float]
    walk_rate: Optional[float]
    strikeout_rate: Optional[float]
    seasons: tuple[str, ...] = ()

    @property
    def league_name(self) -> str:
        return self.translation.name

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
class ProjectionRow:
    """One player's projection, its inputs, and its scorecard against reality."""

    player: PlayerMatch
    source: Optional[LeagueLine] = None
    projected: dict[str, Optional[float]] = field(default_factory=dict)
    actual: Optional[StatLine] = None
    residual: dict[str, Optional[float]] = field(default_factory=dict)
    other_leagues: tuple[LeagueLine, ...] = ()
    unmatched_leagues: tuple[str, ...] = ()
    clamped: tuple[str, ...] = ()
    included: bool = False
    note: Optional[str] = None

    @property
    def has_actual(self) -> bool:
        return self.actual is not None

    @property
    def is_close_call(self) -> bool:
        """True when the runner-up league is within 15% of the winner's PA."""
        if not self.source or not self.other_leagues:
            return False
        runner_up = self.other_leagues[0].plate_appearances
        return runner_up >= self.source.plate_appearances * 0.85


@dataclass
class ProjectionResult:
    min_pa: int
    apply_filter: bool
    rows: list[ProjectionRow] = field(default_factory=list)
    unresolved: list[UnresolvedEntry] = field(default_factory=list)
    # Residual distributions over the players who already have MLB time.
    residuals: dict[str, Distribution] = field(default_factory=dict)
    mean_abs_residual: dict[str, Optional[float]] = field(default_factory=dict)
    failed_sport_ids: list[int] = field(default_factory=list)

    @property
    def projected_count(self) -> int:
        return sum(1 for r in self.rows if r.included)

    @property
    def skipped_count(self) -> int:
        return sum(1 for r in self.rows if not r.included)

    @property
    def scored_count(self) -> int:
        return sum(1 for r in self.rows if r.included and r.has_actual)


# ----------------------------------------------------------------------
# league aggregation
# ----------------------------------------------------------------------
def league_lines(payloads: dict[int, dict[str, Any]]) -> tuple[list[LeagueLine], list[str]]:
    """Every minor league this player appeared in, biggest PA first.

    Returns ``(recognised lines, unrecognised league names)``. Splits are
    bucketed by league rather than by level, because the deltas are per league:
    a player who split Triple-A between the International and Pacific Coast
    leagues gets two separate lines, not one blended "AAA" line.
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
        translation = TRANSLATION_BY_KEY[key]
        lines.append(
            LeagueLine(
                translation=translation,
                plate_appearances=aggregated["plate_appearances"],
                ops=aggregated["ops"],
                slg=aggregated["slg"],
                walk_rate=aggregated["walk_rate"],
                strikeout_rate=aggregated["strikeout_rate"],
                seasons=tuple(sorted(seasons.get(key, set()))),
            )
        )

    # Most plate appearances first; ties broken by the higher level, then name,
    # so the ordering is stable rather than dictionary-order.
    lines.sort(
        key=lambda line: (
            -line.plate_appearances,
            line.translation.sport_id,
            line.translation.key,
        )
    )
    return lines, list(unmatched)


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


def projection_row(
    player: PlayerMatch,
    payloads: dict[int, dict[str, Any]],
    min_pa: int,
    apply_filter: bool,
) -> ProjectionRow:
    lines, unmatched = league_lines(payloads)
    actual = mlb_line(payloads)

    if not lines:
        note = (
            f"no AAA / AA / A+ record in a recognised league"
            + (f" (saw: {', '.join(unmatched)})" if unmatched else "")
        )
        return ProjectionRow(
            player=player,
            actual=actual,
            unmatched_leagues=tuple(unmatched),
            note=note,
        )

    source, others = lines[0], tuple(lines[1:])

    if apply_filter and source.plate_appearances < min_pa:
        return ProjectionRow(
            player=player,
            source=source,
            actual=actual,
            other_leagues=others,
            unmatched_leagues=tuple(unmatched),
            note=f"{source.plate_appearances} PA in {source.translation.short} < {min_pa}",
        )

    projected, clamped = project(source)
    residual: dict[str, Optional[float]] = {}
    for metric in METRIC_KEYS:
        actual_value = _actual_value(actual, metric)
        estimate = projected.get(metric)
        residual[metric] = (
            actual_value - estimate
            if actual_value is not None and estimate is not None
            else None
        )

    return ProjectionRow(
        player=player,
        source=source,
        projected=projected,
        actual=actual,
        residual=residual,
        other_leagues=others,
        unmatched_leagues=tuple(unmatched),
        clamped=clamped,
        included=True,
    )


def _actual_value(actual: Optional[StatLine], metric: str) -> Optional[float]:
    if actual is None:
        return None
    return {
        "ops": actual.ops,
        "slg": actual.slg,
        "bb": actual.walk_rate,
        "k": actual.strikeout_rate,
    }[metric]


def summarise_residuals(
    rows: list[ProjectionRow],
) -> tuple[dict[str, Distribution], dict[str, Optional[float]]]:
    """How far off the projections were, for players with real MLB numbers.

    Two figures per metric, because they answer different questions: the mean
    residual is **bias** (is the translation systematically high or low?) and the
    mean absolute residual is **typical miss** (how far off is any one player?).
    A translation can have near-zero bias and still be wrong about everybody.
    """
    scored = [r for r in rows if r.included and r.has_actual]
    residuals: dict[str, Distribution] = {}
    mean_abs: dict[str, Optional[float]] = {}
    for metric in METRIC_KEYS:
        values = [
            r.residual.get(metric)
            for r in scored
            if r.residual.get(metric) is not None
        ]
        residuals[metric] = describe(values)
        mean_abs[metric] = (
            sum(abs(v) for v in values) / len(values) if values else None
        )
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

    result.rows = [
        projection_row(player, payloads.get(player.person_id, {}), min_pa, apply_filter)
        for player in players
    ]
    result.residuals, result.mean_abs_residual = summarise_residuals(result.rows)
    result.failed_sport_ids = sorted({sid for _, sid in failed})
    return result
