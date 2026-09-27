"""Tests for the projection maths and the dominant-league choice."""

from __future__ import annotations

import pytest

from app.services.models import PlayerMatch
from app.services.projection import (
    build_projections,
    league_lines,
    mlb_line,
    project,
    projection_row,
    summarise_residuals,
)
from app.services.translations import find_translation
from tests.fixtures import people_payload, split


def player(pid: int = 1, name: str = "Test Player") -> PlayerMatch:
    return PlayerMatch(person_id=pid, full_name=name)


def league_split(season, sport_id, league, *, pa, bb, so, ab, hits, tb):
    """A yearByYear split carrying its league, which is what projection keys on.

    OPS and SLG are derived from the components rather than left at the fixture
    defaults: a single split is taken at its reported value (that is the real
    API's own number), so an inconsistent fixture would test nothing real.
    """
    slg = tb / ab
    obp = (hits + bb) / (ab + bb)
    s = split(
        "2024", sport_id, pa=pa, bb=bb, so=so, ab=ab, hits=hits, tb=tb, hbp=0, sf=0,
        slg=f"{slg:.3f}", ops=f"{obp + slg:.3f}",
    )
    s["season"] = str(season)
    s["league"] = {"id": 999, "name": league}
    return s


def payloads_from(**by_sport):
    """`payloads_from(aaa=[split, ...], mlb=[...])` -> {sportId: people payload}."""
    ids = {"mlb": 1, "aaa": 11, "aa": 12, "aplus": 13}
    out = {}
    for key, sport_id in ids.items():
        out[sport_id] = people_payload(by_sport.get(key) or [])
    return out


class StubBulkClient:
    def __init__(self, matches=None, payloads=None):
        self.matches = matches or {}
        self.payloads = payloads or {}
        self.requested_sport_ids: list[int] = []

    def bulk_find_players(self, queries):
        queries = list(queries)
        return {q: self.matches.get(q, []) for q in queries}, []

    def bulk_levels_hitting(self, person_ids, sport_ids):
        self.requested_sport_ids = list(sport_ids)
        return {pid: self.payloads.get(pid, {}) for pid in person_ids}, []


# ----------------------------------------------------------------------
# dominant league
# ----------------------------------------------------------------------
def test_the_league_with_the_most_pa_wins():
    payloads = payloads_from(
        aaa=[league_split(2024, 11, "International League", pa=200, bb=20, so=50, ab=175, hits=50, tb=85)],
        aa=[league_split(2023, 12, "Texas League", pa=450, bb=45, so=100, ab=390, hits=110, tb=190)],
    )
    lines, unmatched = league_lines(payloads)
    assert [l.translation.key for l in lines] == ["texas", "international"]
    assert lines[0].plate_appearances == 450
    assert unmatched == []


def test_pa_wins_even_when_the_other_league_is_a_higher_level():
    """A brief Triple-A stint does not outrank a full Double-A season."""
    payloads = payloads_from(
        aaa=[league_split(2024, 11, "Pacific Coast League", pa=60, bb=5, so=18, ab=52, hits=14, tb=24)],
        aa=[league_split(2023, 12, "Eastern League", pa=520, bb=55, so=110, ab=450, hits=130, tb=215)],
    )
    lines, _ = league_lines(payloads)
    assert lines[0].translation.key == "eastern"


def test_two_leagues_at_the_same_level_stay_separate():
    """The deltas are per league, so AAA must not be blended into one line."""
    payloads = payloads_from(
        aaa=[
            league_split(2023, 11, "International League", pa=300, bb=30, so=70, ab=260, hits=75, tb=130),
            league_split(2024, 11, "Pacific Coast League", pa=200, bb=25, so=45, ab=170, hits=55, tb=95),
        ]
    )
    lines, _ = league_lines(payloads)
    assert [l.translation.key for l in lines] == ["international", "pacific-coast"]
    assert [l.plate_appearances for l in lines] == [300, 200]


def test_seasons_in_the_same_league_are_summed_into_one_career_line():
    payloads = payloads_from(
        aa=[
            league_split(2023, 12, "Texas League", pa=300, bb=30, so=60, ab=260, hits=78, tb=130),
            league_split(2024, 12, "Texas League", pa=200, bb=20, so=40, ab=175, hits=52, tb=90),
        ]
    )
    lines, _ = league_lines(payloads)
    assert len(lines) == 1
    assert lines[0].plate_appearances == 500
    assert lines[0].span == "2023–2024"
    # SLG recomputed from totals: 220 TB / 435 AB, not an average of averages
    assert lines[0].slg == pytest.approx(220 / 435)


def test_lower_levels_are_ignored_entirely():
    """Only AAA, AA and A+ are covered by the translation table."""
    payloads = payloads_from(
        aa=[league_split(2024, 12, "Southern League", pa=150, bb=15, so=35, ab=130, hits=38, tb=62)]
    )
    payloads[14] = people_payload(
        [league_split(2022, 14, "Carolina League", pa=900, bb=90, so=200, ab=800, hits=240, tb=400)]
    )
    lines, unmatched = league_lines(payloads)
    assert [l.translation.key for l in lines] == ["southern"]
    assert unmatched == []  # a level we never look at is not an "unmatched league"


def test_an_unrecognised_league_is_reported_not_substituted():
    payloads = payloads_from(
        aaa=[league_split(2024, 11, "Mexican League", pa=400, bb=40, so=80, ab=350, hits=105, tb=175)]
    )
    lines, unmatched = league_lines(payloads)
    assert lines == []
    assert unmatched == ["Mexican League"]


def test_a_2021_placeholder_league_name_still_matches():
    payloads = payloads_from(
        aaa=[league_split(2021, 11, "Triple-A East", pa=300, bb=30, so=70, ab=260, hits=75, tb=130)]
    )
    lines, unmatched = league_lines(payloads)
    assert lines[0].translation.key == "international"
    assert unmatched == []


# ----------------------------------------------------------------------
# the projection arithmetic
# ----------------------------------------------------------------------
def test_each_metric_gets_its_own_league_delta():
    payloads = payloads_from(
        # 500 PA, 50 BB (10.0%), 100 K (20.0%), 440 AB, 130 H, 220 TB
        aaa=[league_split(2024, 11, "Pacific Coast League", pa=500, bb=50, so=100, ab=440, hits=130, tb=220)]
    )
    lines, _ = league_lines(payloads)
    projected, clamped = project(lines[0])
    pacific = find_translation("Pacific Coast League")

    assert projected["slg"] == pytest.approx(220 / 440 + pacific.d_slg)
    assert projected["bb"] == pytest.approx(0.10 - 0.033)  # -3.3 pp
    assert projected["k"] == pytest.approx(0.20 + 0.042)  # +4.2 pp
    assert clamped == ()


def test_rate_deltas_are_percentage_points_not_percent():
    """The classic unit bug: -3.3 pp off 10.0% is 6.7%, not 9.67%."""
    payloads = payloads_from(
        aaa=[league_split(2024, 11, "Pacific Coast League", pa=1000, bb=100, so=200, ab=880, hits=260, tb=440)]
    )
    lines, _ = league_lines(payloads)
    projected, _clamped = project(lines[0])
    assert projected["bb"] == pytest.approx(0.067)


def test_a_negative_projected_rate_is_clamped_and_flagged():
    """1.0% walk rate minus 3.3 points is not a negative walk rate."""
    payloads = payloads_from(
        aaa=[league_split(2024, 11, "Pacific Coast League", pa=400, bb=4, so=90, ab=390, hits=100, tb=160)]
    )
    lines, _ = league_lines(payloads)
    projected, clamped = project(lines[0])
    assert projected["bb"] == 0.0
    assert "bb" in clamped


def test_different_leagues_at_one_level_give_different_projections():
    """Southern -.073 vs Texas -.126 must not collapse to a single AA number."""
    def ops_for(league):
        payloads = payloads_from(
            aa=[league_split(2024, 12, league, pa=500, bb=50, so=100, ab=440, hits=130, tb=220)]
        )
        lines, _ = league_lines(payloads)
        projected, _clamped = project(lines[0])
        return projected["ops"]

    southern, texas = ops_for("Southern League"), ops_for("Texas League")
    assert southern > texas
    assert southern - texas == pytest.approx(0.126 - 0.073)


# ----------------------------------------------------------------------
# rows
# ----------------------------------------------------------------------
def test_a_row_carries_the_source_line_the_projection_and_the_actual():
    payloads = payloads_from(
        aaa=[league_split(2023, 11, "International League", pa=400, bb=40, so=90, ab=350, hits=105, tb=175)],
        mlb=[league_split(2024, 1, "American League", pa=500, bb=45, so=130, ab=445, hits=115, tb=190)],
    )
    row = projection_row(player(), payloads, min_pa=120, apply_filter=True)
    assert row.included is True
    assert row.source.translation.key == "international"
    assert row.actual is not None and row.actual.plate_appearances == 500
    assert row.residual["ops"] == pytest.approx(row.actual.ops - row.projected["ops"])


def test_a_player_with_no_mlb_time_still_projects():
    payloads = payloads_from(
        aa=[league_split(2024, 12, "Eastern League", pa=400, bb=40, so=90, ab=350, hits=105, tb=175)]
    )
    row = projection_row(player(), payloads, min_pa=120, apply_filter=True)
    assert row.included is True
    assert row.has_actual is False
    assert all(v is None for v in row.residual.values())


def test_a_player_with_no_covered_minor_league_is_skipped_with_a_reason():
    payloads = payloads_from(
        mlb=[league_split(2024, 1, "National League", pa=600, bb=60, so=150, ab=520, hits=140, tb=250)]
    )
    row = projection_row(player(), payloads, min_pa=120, apply_filter=True)
    assert row.included is False
    assert "no AAA / AA / A+" in row.note
    assert row.actual is not None  # their MLB line is still shown


def test_the_pa_filter_applies_to_the_dominant_league():
    payloads = payloads_from(
        aaa=[league_split(2024, 11, "International League", pa=60, bb=6, so=15, ab=52, hits=15, tb=26)]
    )
    row = projection_row(player(), payloads, min_pa=120, apply_filter=True)
    assert row.included is False
    assert "60 PA in International < 120" in row.note

    loose = projection_row(player(), payloads, min_pa=120, apply_filter=False)
    assert loose.included is True


def test_a_near_tie_between_leagues_is_flagged_as_a_close_call():
    payloads = payloads_from(
        aaa=[league_split(2024, 11, "International League", pa=300, bb=30, so=70, ab=260, hits=78, tb=130)],
        aa=[league_split(2023, 12, "Eastern League", pa=290, bb=29, so=65, ab=250, hits=75, tb=125)],
    )
    row = projection_row(player(), payloads, min_pa=120, apply_filter=True)
    assert row.source.translation.key == "international"
    assert row.is_close_call is True


def test_a_lopsided_split_is_not_a_close_call():
    payloads = payloads_from(
        aaa=[league_split(2024, 11, "International League", pa=600, bb=60, so=140, ab=520, hits=155, tb=260)],
        aa=[league_split(2023, 12, "Eastern League", pa=100, bb=10, so=22, ab=88, hits=26, tb=44)],
    )
    assert projection_row(player(), payloads, min_pa=120, apply_filter=True).is_close_call is False


def test_the_mlb_line_aggregates_every_season():
    payloads = payloads_from(
        mlb=[
            league_split(2023, 1, "AL", pa=300, bb=30, so=80, ab=265, hits=70, tb=115),
            league_split(2024, 1, "AL", pa=500, bb=50, so=125, ab=440, hits=120, tb=205),
        ]
    )
    line = mlb_line(payloads)
    assert line.plate_appearances == 800
    assert line.walk_rate == pytest.approx(80 / 800)


def test_no_mlb_payload_gives_no_actual_line():
    assert mlb_line(payloads_from()) is None


# ----------------------------------------------------------------------
# residual scoring
# ----------------------------------------------------------------------
def test_residuals_only_count_players_with_real_mlb_numbers():
    with_mlb = payloads_from(
        aaa=[league_split(2023, 11, "International League", pa=400, bb=40, so=90, ab=350, hits=105, tb=175)],
        mlb=[league_split(2024, 1, "AL", pa=500, bb=45, so=130, ab=445, hits=115, tb=190)],
    )
    without = payloads_from(
        aaa=[league_split(2023, 11, "International League", pa=400, bb=40, so=90, ab=350, hits=105, tb=175)]
    )
    rows = [
        projection_row(player(1), with_mlb, 120, True),
        projection_row(player(2), without, 120, True),
    ]
    residuals, mean_abs = summarise_residuals(rows)
    assert residuals["ops"].n == 1
    assert mean_abs["ops"] is not None


def test_mean_absolute_residual_does_not_cancel_out():
    """Two equal-and-opposite misses are zero bias but a real typical miss."""
    from app.services.projection import ProjectionRow

    rows = [
        ProjectionRow(player=player(1), included=True, actual=object(), residual={"ops": 0.10}),
        ProjectionRow(player=player(2), included=True, actual=object(), residual={"ops": -0.10}),
    ]
    residuals, mean_abs = summarise_residuals(rows)
    assert residuals["ops"].mean == pytest.approx(0.0)
    assert mean_abs["ops"] == pytest.approx(0.10)


def test_no_scored_players_gives_empty_residuals_not_a_crash():
    residuals, mean_abs = summarise_residuals([])
    assert residuals["ops"].n == 0
    assert mean_abs["ops"] is None


# ----------------------------------------------------------------------
# end to end
# ----------------------------------------------------------------------
def test_build_projections_fetches_mlb_plus_the_three_covered_levels():
    stub = StubBulkClient(
        {"A": [player(1, "A")]},
        {1: payloads_from(aaa=[league_split(2024, 11, "International League", pa=400, bb=40, so=90, ab=350, hits=105, tb=175)])},
    )
    result = build_projections(stub, "A", 120, True)
    assert stub.requested_sport_ids == [1, 11, 12, 13]
    assert result.projected_count == 1


def test_build_projections_mixes_projected_skipped_and_unresolved():
    stub = StubBulkClient(
        matches={
            "Good": [player(1, "Good")],
            "Tiny": [player(2, "Tiny")],
            "Ghost": [],
        },
        payloads={
            1: payloads_from(aa=[league_split(2024, 12, "Texas League", pa=500, bb=50, so=110, ab=440, hits=130, tb=220)]),
            2: payloads_from(aa=[league_split(2024, 12, "Texas League", pa=40, bb=4, so=9, ab=35, hits=10, tb=17)]),
        },
    )
    result = build_projections(stub, "Good\nTiny\nGhost", 120, True)
    assert result.projected_count == 1
    assert result.skipped_count == 1
    assert [e.reason for e in result.unresolved] == ["not found"]


def test_build_projections_with_nobody_resolvable_is_empty_not_broken():
    result = build_projections(StubBulkClient({"Ghost": []}), "Ghost", 120, True)
    assert result.rows == []
    assert result.residuals["ops"].n == 0
