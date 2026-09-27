"""Level-to-level differentials across a list of players.

For each player we take the two career-by-level lines produced by
``transforms.career_by_level`` and subtract: **high level minus low level**.
So on the default MLB − AAA pair a negative ΔOPS means the bat played worse in
the majors, which is the normal direction.

Nothing here talks to the network. It takes a client only to fan out the
fetches, which keeps it trivial to test with a stub.
"""

from __future__ import annotations

import logging
from typing import Any, Iterable, Optional, Protocol

import pandas as pd

from app.config import LevelPair
from app.services.distribution import EMPTY, Distribution, describe
from app.services.models import (
    ComparisonResult,
    DiffRow,
    PlayerMatch,
    StatLine,
    UnresolvedEntry,
)
from app.services.transforms import career_by_level, rows_from_payloads

logger = logging.getLogger(__name__)

# (key, column label, how to format it, which sign means "better up here")
# K% is the one metric where a negative difference is the good outcome, so it
# carries -1. Without this the table would paint "struck out more in the
# majors" green alongside "slugged more in the majors".
METRICS: tuple[tuple[str, str, str, int], ...] = (
    ("ops", "Δ OPS", "slash", 1),
    ("slg", "Δ SLG", "slash", 1),
    ("bb", "Δ BB%", "rate", 1),
    ("k", "Δ K%", "rate", -1),
)
METRIC_KEYS: tuple[str, ...] = tuple(m[0] for m in METRICS)


class SupportsBulkFetch(Protocol):
    """The slice of MLBClient this module needs."""

    def bulk_find_players(
        self, queries: Iterable[str]
    ) -> tuple[dict[str, list[PlayerMatch]], list[str]]: ...

    def bulk_levels_hitting(
        self, person_ids: Iterable[int], sport_ids: Iterable[int]
    ) -> tuple[dict[int, dict[int, dict[str, Any]]], list[tuple[int, int]]]: ...


# ----------------------------------------------------------------------
# input parsing
# ----------------------------------------------------------------------
def parse_player_list(text: str) -> list[str]:
    """One player per line.

    Deliberately does **not** split on commas — "Smith, Will" is a legitimate
    way to type a name, and splitting it would silently create two bogus
    lookups. Blank lines, list bullets and "1." numbering are stripped;
    duplicates collapse, keeping first-seen order.
    """
    out: list[str] = []
    for raw in (text or "").splitlines():
        line = raw.strip().lstrip("-•*\t ").strip()
        # strip a leading "12." or "12)" enumerator
        head, sep, tail = line.partition(" ")
        if sep and head[:-1].isdigit() and head[-1:] in (".", ")"):
            line = tail.strip()
        if line and line not in out:
            out.append(line)
    return out


# ----------------------------------------------------------------------
# resolution
# ----------------------------------------------------------------------
def resolve(
    client: SupportsBulkFetch, queries: list[str]
) -> tuple[list[PlayerMatch], list[UnresolvedEntry]]:
    """Turn typed lines into players, flagging anything that is not a clean hit.

    An ambiguous name is never guessed at — it goes to the unresolved list with
    its candidates so the user can paste back the MLBAM id they meant.
    """
    matches_by_query, failed = client.bulk_find_players(queries)

    players: list[PlayerMatch] = []
    unresolved: list[UnresolvedEntry] = []
    seen: set[int] = set()

    for query in queries:
        if query in failed:
            unresolved.append(UnresolvedEntry(query=query, reason="lookup failed"))
            continue
        matches = matches_by_query.get(query, [])
        if not matches:
            unresolved.append(UnresolvedEntry(query=query, reason="not found"))
        elif len(matches) > 1:
            unresolved.append(
                UnresolvedEntry(query=query, reason="ambiguous", candidates=matches[:8])
            )
        else:
            player = matches[0]
            if player.person_id not in seen:
                seen.add(player.person_id)
                players.append(player)

    return players, unresolved


# ----------------------------------------------------------------------
# per-player differential
# ----------------------------------------------------------------------
def _stat_line(summaries: list, level_code: str) -> Optional[StatLine]:
    for summary in summaries:
        if summary.level_code == level_code:
            return StatLine(
                level_code=summary.level_code,
                plate_appearances=summary.plate_appearances,
                ops=summary.ops,
                slg=summary.slg,
                walk_rate=summary.walk_rate,
                strikeout_rate=summary.strikeout_rate,
            )
    return None


def _subtract(high: Optional[float], low: Optional[float]) -> Optional[float]:
    if high is None or low is None:
        return None
    return high - low


def diff_row(
    player: PlayerMatch,
    payloads: dict[int, dict[str, Any]],
    pair: LevelPair,
    min_pa: int,
    apply_filter: bool,
) -> DiffRow:
    """Build one player's row. `payloads` is keyed by sportId."""
    summaries = career_by_level(rows_from_payloads(payloads))
    high = _stat_line(summaries, pair.high.code)
    low = _stat_line(summaries, pair.low.code)

    deltas: dict[str, Optional[float]] = dict.fromkeys(METRIC_KEYS)
    if high and low:
        deltas["ops"] = _subtract(high.ops, low.ops)
        deltas["slg"] = _subtract(high.slg, low.slg)
        deltas["bb"] = _subtract(high.walk_rate, low.walk_rate)
        deltas["k"] = _subtract(high.strikeout_rate, low.strikeout_rate)

    included = True
    note: Optional[str] = None
    if high is None and low is None:
        included, note = False, f"no {pair.high.code} or {pair.low.code} record"
    elif high is None:
        included, note = False, f"no {pair.high.code} record"
    elif low is None:
        included, note = False, f"no {pair.low.code} record"
    elif apply_filter and low.plate_appearances < min_pa:
        included = False
        note = f"{low.plate_appearances} {pair.low.code} PA < {min_pa}"

    return DiffRow(player=player, high=high, low=low, deltas=deltas, included=included, note=note)


# ----------------------------------------------------------------------
# aggregate
# ----------------------------------------------------------------------
def summarise(rows: list[DiffRow]) -> dict[str, Distribution]:
    """One Distribution per metric, over included rows only.

    Sample size is tracked per metric, not once for the table: a player can have
    a usable ΔOPS but a missing ΔBB% if the API omitted walks at one level, so
    the columns legitimately have different n.
    """
    included = [r for r in rows if r.included]
    if not included:
        return {key: EMPTY for key in METRIC_KEYS}

    frame = pd.DataFrame([{k: r.deltas.get(k) for k in METRIC_KEYS} for r in included])
    return {
        key: describe(frame[key].dropna().tolist()) for key in METRIC_KEYS
    }


# ----------------------------------------------------------------------
# top-level orchestration
# ----------------------------------------------------------------------
def build_comparison(
    client: SupportsBulkFetch,
    raw_text: str,
    pair: LevelPair,
    min_pa: int,
    apply_filter: bool,
) -> ComparisonResult:
    queries = parse_player_list(raw_text)
    players, unresolved = resolve(client, queries)

    result = ComparisonResult(
        pair_key=pair.key,
        high_code=pair.high.code,
        low_code=pair.low.code,
        min_pa=min_pa,
        apply_filter=apply_filter,
        unresolved=unresolved,
    )
    if not players:
        result.stats = summarise([])
        return result

    payloads, failed = client.bulk_levels_hitting(
        [p.person_id for p in players], pair.sport_ids
    )

    result.rows = [
        diff_row(player, payloads.get(player.person_id, {}), pair, min_pa, apply_filter)
        for player in players
    ]
    result.stats = summarise(result.rows)
    result.failed_sport_ids = sorted({sid for _, sid in failed})
    return result
