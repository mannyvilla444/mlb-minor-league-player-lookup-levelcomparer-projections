"""Turn raw MLB Stats API JSON into season x level rows and career rollups.

Rules we hold ourselves to:

* Never invent a stat. A field the API omits comes through as ``None`` and the
  template renders an em dash.
* Rows with zero (or missing) plate appearances are dropped — rates would be
  meaningless and the brief says to skip them.
* BB% = ``baseOnBalls / plateAppearances``; K% = ``strikeOuts / plateAppearances``.
  Stored as fractions here, formatted as percentages in the templates.
"""

from __future__ import annotations

from typing import Any, Optional

import pandas as pd

from app.config import LEVEL_BY_SPORT_ID, MLB_PA_MILESTONE
from app.services.models import LevelSummary, PlayerMatch, PlayerReport, SeasonLevelRow

MLB_SPORT_ID = 1

_EXACT_METHOD = "exact — from AB/TB/H/BB/HBP/SF totals"
_WEIGHTED_METHOD = "PA-weighted average of seasons"
_SINGLE_METHOD = "as reported (single season)"


# ----------------------------------------------------------------------
# scalar helpers
# ----------------------------------------------------------------------
def to_float(value: Any) -> Optional[float]:
    """Parse ``.789`` / ``"0.789"`` / ``789`` into a float, or ``None``."""
    if value is None or value == "" or value == "-" or value == ".---":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def to_int(value: Any) -> Optional[int]:
    f = to_float(value)
    return int(f) if f is not None else None


def safe_rate(numerator: Optional[int], denominator: Optional[int]) -> Optional[float]:
    """``numerator / denominator`` or ``None`` when either side is unusable."""
    if numerator is None or not denominator:
        return None
    return numerator / denominator


def _sum(values: list[Optional[int]]) -> Optional[int]:
    present = [v for v in values if v is not None]
    if not present:
        return None
    return sum(present)


# ----------------------------------------------------------------------
# payload -> rows
# ----------------------------------------------------------------------
def extract_splits(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Pull the yearByYear hitting splits out of a ``people`` payload."""
    if not isinstance(payload, dict):
        return []
    people = payload.get("people") or []
    if not people:
        return []
    splits: list[dict[str, Any]] = []
    for group in people[0].get("stats") or []:
        group_name = ((group.get("group") or {}).get("displayName") or "").lower()
        if group_name and group_name != "hitting":
            continue
        for split in group.get("splits") or []:
            if isinstance(split, dict):
                splits.append(split)
    return splits


def aggregate_splits(splits: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """Fold any set of splits into one hitting line, or ``None`` if there are no PA.

    The single source of the "sum the counting stats, recompute the rates from
    the totals" rule. Used for a season at a level, and again for a career in a
    league — so both agree by construction rather than by coincidence.
    """
    stats = [split.get("stat") or {} for split in splits]
    pa = _sum([to_int(s.get("plateAppearances")) for s in stats])
    if not pa:
        return None  # zero or unknown PA -> nothing meaningful to report

    bb = _sum([to_int(s.get("baseOnBalls")) for s in stats])
    so = _sum([to_int(s.get("strikeOuts")) for s in stats])
    ab = _sum([to_int(s.get("atBats")) for s in stats])
    hits = _sum([to_int(s.get("hits")) for s in stats])
    tb = _sum([to_int(s.get("totalBases")) for s in stats])
    hbp = _sum([to_int(s.get("hitByPitch")) for s in stats])
    sf = _sum([to_int(s.get("sacFlies")) for s in stats])

    if len(stats) == 1:
        ops = to_float(stats[0].get("ops"))
        slg = to_float(stats[0].get("slg"))
    else:
        slg, ops = _recompute_slash(ab, hits, tb, bb, hbp, sf)
        if slg is None or ops is None:
            slg, ops = _pa_weighted_slash(stats)

    return {
        "plate_appearances": pa,
        "ops": ops,
        "slg": slg,
        "walk_rate": safe_rate(bb, pa),
        "strikeout_rate": safe_rate(so, pa),
        "walks": bb,
        "strikeouts": so,
        "at_bats": ab,
        "hits": hits,
        "total_bases": tb,
        "hit_by_pitch": hbp,
        "sac_flies": sf,
    }


def _combine_season_splits(
    season: str, sport_id: int, splits: list[dict[str, Any]]
) -> Optional[SeasonLevelRow]:
    """Fold one or more splits (a mid-season trade makes two) into one row."""
    level = LEVEL_BY_SPORT_ID.get(sport_id)
    if level is None:
        return None

    line = aggregate_splits(splits)
    if line is None:
        return None

    teams = [
        (split.get("team") or {}).get("name")
        for split in splits
        if (split.get("team") or {}).get("name")
    ]
    if not teams:
        team = None
    elif len(set(teams)) == 1:
        team = teams[0]
    else:
        team = f"{len(set(teams))} teams"

    return SeasonLevelRow(
        season=str(season),
        sport_id=sport_id,
        level_code=level.code,
        level_name=level.name,
        level_order=level.order,
        team=team,
        **line,
    )


def _recompute_slash(
    ab: Optional[int],
    hits: Optional[int],
    tb: Optional[int],
    bb: Optional[int],
    hbp: Optional[int],
    sf: Optional[int],
) -> tuple[Optional[float], Optional[float]]:
    """Return ``(SLG, OPS)`` computed from component totals, or ``(None, None)``."""
    if not ab or tb is None or hits is None or bb is None:
        return None, None
    slg = tb / ab
    obp_denom = ab + bb + (hbp or 0) + (sf or 0)
    if not obp_denom:
        return slg, None
    obp = (hits + bb + (hbp or 0)) / obp_denom
    return slg, obp + slg


def _pa_weighted_slash(stats: list[dict[str, Any]]) -> tuple[Optional[float], Optional[float]]:
    def weighted(key: str) -> Optional[float]:
        num = 0.0
        den = 0.0
        for s in stats:
            value = to_float(s.get(key))
            pa = to_int(s.get("plateAppearances")) or 0
            if value is not None and pa:
                num += value * pa
                den += pa
        return num / den if den else None

    return weighted("slg"), weighted("ops")


def rows_from_payloads(payloads: dict[int, dict[str, Any]]) -> list[SeasonLevelRow]:
    """Build the combined season x level table from one payload per sportId."""
    buckets: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for requested_sport_id, payload in payloads.items():
        for split in extract_splits(payload):
            season = split.get("season")
            if not season:
                continue
            split_sport_id = (split.get("sport") or {}).get("id")
            sport_id = (
                int(split_sport_id)
                if split_sport_id in LEVEL_BY_SPORT_ID
                else int(requested_sport_id)
            )
            buckets.setdefault((str(season), sport_id), []).append(split)

    rows = [
        row
        for (season, sport_id), splits in buckets.items()
        if (row := _combine_season_splits(season, sport_id, splits)) is not None
    ]
    # Most recent season first; within a season MLB sits above the minors.
    rows.sort(key=lambda r: (-int(r.season) if r.season.isdigit() else 0, r.level_order))
    return rows


# ----------------------------------------------------------------------
# career-by-level rollup (pandas)
# ----------------------------------------------------------------------
def career_by_level(rows: list[SeasonLevelRow]) -> list[LevelSummary]:
    """Aggregate every season row into one line per level."""
    if not rows:
        return []

    frame = pd.DataFrame(
        [
            {
                "level_code": r.level_code,
                "level_name": r.level_name,
                "level_order": r.level_order,
                "season": r.season,
                "pa": r.plate_appearances or 0,
                "bb": r.walks,
                "so": r.strikeouts,
                "ab": r.at_bats,
                "h": r.hits,
                "tb": r.total_bases,
                "hbp": r.hit_by_pitch,
                "sf": r.sac_flies,
                "ops": r.ops,
                "slg": r.slg,
            }
            for r in rows
        ]
    )

    summaries: list[LevelSummary] = []
    for (code, name, order), group in frame.groupby(
        ["level_code", "level_name", "level_order"], sort=False
    ):
        pa = int(group["pa"].sum())
        bb = _nullable_sum(group["bb"])
        so = _nullable_sum(group["so"])
        ab = _nullable_sum(group["ab"])
        hits = _nullable_sum(group["h"])
        tb = _nullable_sum(group["tb"])
        hbp = _nullable_sum(group["hbp"])
        sf = _nullable_sum(group["sf"])

        slg, ops = _recompute_slash(ab, hits, tb, bb, hbp, sf)
        if slg is not None and ops is not None:
            method = _EXACT_METHOD if len(group) > 1 else _SINGLE_METHOD
        else:
            slg = _weighted_column(group, "slg")
            ops = _weighted_column(group, "ops")
            method = _WEIGHTED_METHOD

        summaries.append(
            LevelSummary(
                level_code=str(code),
                level_name=str(name),
                level_order=int(order),
                seasons=int(group["season"].nunique()),
                plate_appearances=pa,
                ops=ops,
                slg=slg,
                walk_rate=safe_rate(bb, pa),
                strikeout_rate=safe_rate(so, pa),
                method=method,
            )
        )

    summaries.sort(key=lambda s: s.level_order)
    return summaries


def _nullable_sum(series: "pd.Series") -> Optional[int]:
    clean = series.dropna()
    if clean.empty:
        return None
    return int(clean.sum())


def _weighted_column(group: "pd.DataFrame", column: str) -> Optional[float]:
    clean = group[[column, "pa"]].dropna()
    clean = clean[clean["pa"] > 0]
    if clean.empty:
        return None
    return float((clean[column] * clean["pa"]).sum() / clean["pa"].sum())


# ----------------------------------------------------------------------
# "first N MLB PA" marker
# ----------------------------------------------------------------------
def annotate_mlb_cumulative_pa(
    rows: list[SeasonLevelRow], milestone: int = MLB_PA_MILESTONE
) -> tuple[list[SeasonLevelRow], Optional[str], Optional[int]]:
    """Attach a running MLB PA total and report where the milestone is crossed.

    Season granularity only — the Stats API's yearByYear hydration has no
    game-level detail, so we flag the season in which the running total first
    reaches the milestone rather than pretending to split a season.
    """
    mlb_rows = sorted(
        (r for r in rows if r.sport_id == MLB_SPORT_ID and r.plate_appearances),
        key=lambda r: r.season,
    )
    running = 0
    totals: dict[str, int] = {}
    milestone_season: Optional[str] = None
    milestone_total: Optional[int] = None
    for row in mlb_rows:
        running += row.plate_appearances or 0
        totals[row.season] = running
        if milestone_season is None and running >= milestone:
            milestone_season = row.season
            milestone_total = running

    annotated = [
        (
            row
            if row.sport_id != MLB_SPORT_ID or row.season not in totals
            else _replace_cumulative(row, totals[row.season])
        )
        for row in rows
    ]
    return annotated, milestone_season, milestone_total


def _replace_cumulative(row: SeasonLevelRow, value: int) -> SeasonLevelRow:
    from dataclasses import replace

    return replace(row, cumulative_mlb_pa=value)


# ----------------------------------------------------------------------
# top-level assembly
# ----------------------------------------------------------------------
def build_report(
    player: PlayerMatch,
    payloads: dict[int, dict[str, Any]],
    failed_sport_ids: Optional[list[int]] = None,
    milestone: int = MLB_PA_MILESTONE,
) -> PlayerReport:
    rows = rows_from_payloads(payloads)
    rows, milestone_season, milestone_total = annotate_mlb_cumulative_pa(rows, milestone)
    return PlayerReport(
        player=player,
        rows=rows,
        by_level=career_by_level(rows),
        milestone_pa=milestone,
        milestone_season=milestone_season,
        milestone_total_pa=milestone_total,
        failed_sport_ids=list(failed_sport_ids or []),
    )
