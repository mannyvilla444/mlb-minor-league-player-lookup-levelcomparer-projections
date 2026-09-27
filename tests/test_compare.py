from __future__ import annotations

import pytest

from app.config import PAIR_BY_KEY
from app.services.compare import (
    build_comparison,
    diff_row,
    parse_player_list,
    resolve,
    summarise,
)
from app.services.models import PlayerMatch
from tests.fixtures import people_payload, split

MLB_AAA = PAIR_BY_KEY["mlb-aaa"]
MLB_AA = PAIR_BY_KEY["mlb-aa"]
AAA_AA = PAIR_BY_KEY["aaa-aa"]


class StubBulkClient:
    """Implements just the two bulk methods compare.py needs."""

    def __init__(self, matches_by_query=None, payloads_by_person=None, failed_jobs=None):
        self.matches_by_query = matches_by_query or {}
        self.payloads_by_person = payloads_by_person or {}
        self.failed_jobs = failed_jobs or []
        self.requested_sport_ids: list[int] = []

    def bulk_find_players(self, queries):
        queries = list(queries)
        return {q: self.matches_by_query.get(q, []) for q in queries}, []

    def bulk_levels_hitting(self, person_ids, sport_ids):
        self.requested_sport_ids = list(sport_ids)
        return (
            {pid: self.payloads_by_person.get(pid, {}) for pid in person_ids},
            self.failed_jobs,
        )


def player(pid: int, name: str) -> PlayerMatch:
    return PlayerMatch(person_id=pid, full_name=name)


def levels(mlb=None, aaa=None, aa=None):
    """Build a {sportId: payload} dict from (pa, bb, so, ab, h, tb) tuples."""
    out = {}
    for sport_id, spec in ((1, mlb), (11, aaa), (12, aa)):
        if spec is None:
            out[sport_id] = {"people": []}
            continue
        pa, bb, so, ab, h, tb = spec
        out[sport_id] = people_payload(
            [split("2024", sport_id, pa=pa, bb=bb, so=so, ab=ab, hits=h, tb=tb, hbp=0, sf=0)]
        )
    return out


# ----------------------------------------------------------------------
# input parsing
# ----------------------------------------------------------------------
def test_parse_splits_on_newlines_only_so_last_first_names_survive():
    assert parse_player_list("Smith, Will\nDe La Cruz, Elly") == [
        "Smith, Will",
        "De La Cruz, Elly",
    ]


def test_parse_strips_bullets_numbering_and_blanks():
    text = "1. Elly De La Cruz\n\n- Bobby Witt Jr.\n  * Corbin Carroll  \n2) Julio Rodriguez"
    assert parse_player_list(text) == [
        "Elly De La Cruz",
        "Bobby Witt Jr.",
        "Corbin Carroll",
        "Julio Rodriguez",
    ]


def test_parse_dedupes_keeping_first_seen_order():
    assert parse_player_list("A\nB\nA\nC") == ["A", "B", "C"]


def test_parse_empty_text_gives_empty_list():
    assert parse_player_list("") == []
    assert parse_player_list("   \n\n  ") == []


# ----------------------------------------------------------------------
# resolution
# ----------------------------------------------------------------------
def test_ambiguous_name_is_flagged_not_guessed():
    stub = StubBulkClient(
        {"Will Smith": [player(2, "Will Smith"), player(3, "Will Smith")]}
    )
    players, unresolved = resolve(stub, ["Will Smith"])
    assert players == []
    assert len(unresolved) == 1
    assert unresolved[0].reason == "ambiguous"
    assert [c.person_id for c in unresolved[0].candidates] == [2, 3]


def test_unknown_name_is_reported_as_not_found():
    stub = StubBulkClient({"Nobody": []})
    players, unresolved = resolve(stub, ["Nobody"])
    assert players == []
    assert unresolved[0].reason == "not found"


def test_clean_matches_resolve_and_duplicates_collapse():
    stub = StubBulkClient(
        {"Elly De La Cruz": [player(682829, "Elly De La Cruz")], "682829": [player(682829, "Elly De La Cruz")]}
    )
    players, unresolved = resolve(stub, ["Elly De La Cruz", "682829"])
    assert [p.person_id for p in players] == [682829]
    assert unresolved == []


# ----------------------------------------------------------------------
# per-player differential
# ----------------------------------------------------------------------
def test_difference_is_high_level_minus_low_level():
    payloads = levels(
        mlb=(600, 60, 180, 540, 135, 240),  # BB% 10.0, K% 30.0
        aaa=(300, 45, 60, 250, 80, 150),  # BB% 15.0, K% 20.0
    )
    row = diff_row(player(1, "Test"), payloads, MLB_AAA, min_pa=120, apply_filter=True)
    assert row.included is True
    assert row.deltas["bb"] == pytest.approx(0.10 - 0.15)
    assert row.deltas["k"] == pytest.approx(0.30 - 0.20)
    assert row.deltas["slg"] == pytest.approx(240 / 540 - 150 / 250)
    assert row.deltas["ops"] < 0


def test_row_is_excluded_when_the_lower_level_is_missing():
    row = diff_row(
        player(1, "Test"), levels(mlb=(600, 60, 180, 540, 135, 240)), MLB_AAA, 120, True
    )
    assert row.included is False
    assert "no AAA record" in row.note
    assert row.deltas["ops"] is None


def test_row_is_excluded_when_the_upper_level_is_missing():
    row = diff_row(
        player(1, "Prospect"), levels(aaa=(300, 30, 60, 260, 80, 140)), MLB_AAA, 120, True
    )
    assert row.included is False
    assert "no MLB record" in row.note


def test_pa_filter_looks_at_the_lower_level_only():
    """900 MLB PA next to 40 AAA PA must still be filtered out."""
    payloads = levels(
        mlb=(900, 90, 250, 800, 200, 350),
        aaa=(40, 4, 12, 35, 10, 18),
    )
    row = diff_row(player(1, "Cup of coffee"), payloads, MLB_AAA, min_pa=120, apply_filter=True)
    assert row.included is False
    assert row.note == "40 AAA PA < 120"

    # The reverse case passes: a small MLB sample is fine, the AAA side is big.
    payloads = levels(
        mlb=(45, 4, 15, 40, 9, 15),
        aaa=(500, 50, 120, 440, 130, 220),
    )
    row = diff_row(player(2, "Debut"), payloads, MLB_AAA, min_pa=120, apply_filter=True)
    assert row.included is True


def test_unchecking_the_filter_lets_small_samples_back_in():
    payloads = levels(mlb=(900, 90, 250, 800, 200, 350), aaa=(40, 4, 12, 35, 10, 18))
    row = diff_row(player(1, "Test"), payloads, MLB_AAA, min_pa=120, apply_filter=False)
    assert row.included is True
    assert row.note is None


def test_a_minor_only_pair_works_too():
    payloads = levels(aaa=(400, 40, 100, 350, 100, 180), aa=(400, 30, 120, 360, 95, 170))
    row = diff_row(player(1, "Climber"), payloads, AAA_AA, 120, True)
    assert row.included is True
    assert row.deltas["bb"] == pytest.approx(40 / 400 - 30 / 400)


# ----------------------------------------------------------------------
# mean / median
# ----------------------------------------------------------------------
def _row_with(deltas: dict, included: bool = True):
    from app.services.models import DiffRow

    return DiffRow(player=player(1, "x"), high=None, low=None, deltas=deltas, included=included)


def test_mean_and_median_over_included_rows():
    rows = [
        _row_with({"ops": -0.100, "slg": -0.050, "bb": -0.02, "k": 0.05}),
        _row_with({"ops": -0.200, "slg": -0.090, "bb": -0.04, "k": 0.07}),
        _row_with({"ops": -0.300, "slg": -0.130, "bb": -0.06, "k": 0.09}),
    ]
    stats = summarise(rows)
    assert stats["ops"].mean == pytest.approx(-0.200)
    assert stats["ops"].median == pytest.approx(-0.200)
    assert stats["ops"].n == 3


def test_median_differs_from_mean_with_an_outlier():
    rows = [
        _row_with({"ops": -0.10}),
        _row_with({"ops": -0.12}),
        _row_with({"ops": -0.98}),
    ]
    stats = summarise(rows)
    assert stats["ops"].median == pytest.approx(-0.12)
    assert stats["ops"].mean == pytest.approx(-0.40)


def test_excluded_rows_do_not_move_the_average():
    rows = [
        _row_with({"ops": -0.10}),
        _row_with({"ops": -0.20}),
        _row_with({"ops": 9.99}, included=False),
    ]
    stats = summarise(rows)
    assert stats["ops"].mean == pytest.approx(-0.15)
    assert stats["ops"].n == 2


def test_sample_size_is_counted_per_metric():
    rows = [
        _row_with({"ops": -0.10, "slg": -0.05, "bb": None, "k": 0.03}),
        _row_with({"ops": -0.20, "slg": None, "bb": -0.01, "k": 0.05}),
    ]
    stats = summarise(rows)
    assert {k: d.n for k, d in stats.items()} == {"ops": 2, "slg": 1, "bb": 1, "k": 2}


def test_no_included_rows_gives_none_not_a_crash():
    stats = summarise([_row_with({"ops": -0.1}, included=False)])
    assert stats["ops"].n == 0
    assert stats["ops"].mean is None and stats["ops"].median is None
    assert stats["ops"].q1 is None and stats["ops"].sd is None
    assert stats["ops"].has_box is False


def test_summarise_carries_the_full_distribution_not_just_the_centre():
    """The box plot needs quartiles and whiskers off the same call."""
    rows = [_row_with({"ops": v}) for v in (-0.30, -0.20, -0.10, -0.05, 0.02)]
    ops = summarise(rows)["ops"]
    assert ops.q1 is not None and ops.q3 is not None and ops.iqr is not None
    assert ops.sd is not None and ops.se_mean is not None and ops.se_median is not None
    assert ops.whisker_low is not None and ops.whisker_high is not None
    assert ops.has_box is True


# ----------------------------------------------------------------------
# end to end
# ----------------------------------------------------------------------
def test_build_comparison_fetches_only_the_two_levels_in_the_pair():
    stub = StubBulkClient(
        {"A": [player(1, "A")]},
        {1: levels(mlb=(600, 60, 180, 540, 135, 240), aa=(300, 30, 70, 260, 80, 140))},
    )
    result = build_comparison(stub, "A", MLB_AA, 120, True)
    assert stub.requested_sport_ids == [1, 12]
    assert result.high_code == "MLB" and result.low_code == "AA"
    assert result.included_count == 1


def test_build_comparison_mixes_included_excluded_and_unresolved():
    stub = StubBulkClient(
        matches_by_query={
            "Good": [player(1, "Good")],
            "Small": [player(2, "Small")],
            "Will Smith": [player(3, "Will Smith"), player(4, "Will Smith")],
            "Ghost": [],
        },
        payloads_by_person={
            1: levels(mlb=(600, 60, 180, 540, 135, 240), aaa=(400, 40, 90, 350, 110, 200)),
            2: levels(mlb=(600, 60, 180, 540, 135, 240), aaa=(30, 3, 8, 26, 8, 14)),
        },
    )
    result = build_comparison(stub, "Good\nSmall\nWill Smith\nGhost", MLB_AAA, 120, True)

    assert result.included_count == 1
    assert result.excluded_count == 1
    assert {e.reason for e in result.unresolved} == {"ambiguous", "not found"}
    assert result.counts["ops"] == 1


def test_build_comparison_with_no_resolvable_players_returns_empty_summary():
    stub = StubBulkClient({"Ghost": []})
    result = build_comparison(stub, "Ghost", MLB_AAA, 120, True)
    assert result.rows == []
    assert result.mean["ops"] is None
    assert len(result.unresolved) == 1
