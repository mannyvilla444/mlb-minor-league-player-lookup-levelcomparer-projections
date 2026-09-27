"""Tests for the projection maths and the dominant-league choice."""

from __future__ import annotations

import pytest

from app.services.models import PlayerMatch
from app.services.projection import (
    build_projections,
    league_lines,
    lines_by_level,
    mlb_line,
    player_projection,
    project,
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
# one projection per level
# ----------------------------------------------------------------------
def test_a_player_gets_one_line_per_level_they_played_at():
    """The headline behaviour: three rungs, three projections."""
    payloads = payloads_from(
        aaa=[league_split(2025, 11, "International League", pa=300, bb=33, so=70, ab=258, hits=78, tb=132)],
        aa=[league_split(2024, 12, "Eastern League", pa=480, bb=48, so=118, ab=420, hits=118, tb=205)],
        aplus=[league_split(2023, 13, "South Atlantic League", pa=350, bb=40, so=80, ab=300, hits=90, tb=150)],
    )
    entry = player_projection(player(name="Marcelo Mayer"), payloads, min_pa=120, apply_filter=True)
    assert [lvl.level_code for lvl in entry.levels] == ["AAA", "AA", "A+"]
    assert all(lvl.included for lvl in entry.levels)
    assert entry.row_count == 3


def test_levels_are_ordered_closest_to_the_majors_first():
    payloads = payloads_from(
        aplus=[league_split(2023, 13, "Midwest League", pa=300, bb=30, so=70, ab=260, hits=78, tb=130)],
        aaa=[league_split(2025, 11, "Pacific Coast League", pa=200, bb=20, so=45, ab=175, hits=52, tb=90)],
    )
    entry = player_projection(player(), payloads, 120, True)
    assert [lvl.level_code for lvl in entry.levels] == ["AAA", "A+"]


def test_each_level_uses_its_own_leagues_delta():
    """AAA and AA must not share a number."""
    payloads = payloads_from(
        aaa=[league_split(2025, 11, "Pacific Coast League", pa=500, bb=50, so=100, ab=440, hits=130, tb=220)],
        aa=[league_split(2024, 12, "Southern League", pa=500, bb=50, so=100, ab=440, hits=130, tb=220)],
    )
    entry = player_projection(player(), payloads, 120, True)
    aaa, aa = entry.levels[0], entry.levels[1]
    # identical input lines, different leagues -> different projections
    assert aaa.source.ops == pytest.approx(aa.source.ops)
    assert aaa.projected["ops"] == pytest.approx(aa.source.ops - 0.202)
    assert aa.projected["ops"] == pytest.approx(aa.source.ops - 0.073)


def test_within_a_level_the_league_with_the_most_pa_is_chosen():
    payloads = payloads_from(
        aaa=[
            league_split(2024, 11, "International League", pa=120, bb=12, so=28, ab=105, hits=30, tb=52),
            league_split(2025, 11, "Pacific Coast League", pa=380, bb=40, so=85, ab=330, hits=100, tb=170),
        ]
    )
    entry = player_projection(player(), payloads, 120, True)
    assert len(entry.levels) == 1
    aaa = entry.levels[0]
    assert aaa.source.league_key == "pacific-coast"
    assert aaa.source.plate_appearances == 380
    assert [a.league_key for a in aaa.alternates] == ["international"]


def test_the_losing_league_is_not_blended_into_the_winner():
    """Its PA are reported as an alternate, never folded into the source line."""
    payloads = payloads_from(
        aaa=[
            league_split(2024, 11, "International League", pa=120, bb=12, so=28, ab=105, hits=30, tb=52),
            league_split(2025, 11, "Pacific Coast League", pa=380, bb=40, so=85, ab=330, hits=100, tb=170),
        ]
    )
    aaa = player_projection(player(), payloads, 120, True).levels[0]
    assert aaa.source.plate_appearances == 380  # not 500
    assert aaa.alternates[0].plate_appearances == 120


def test_two_leagues_at_different_levels_do_not_compete():
    """A big AA season must not suppress the AAA line — they are separate rows."""
    payloads = payloads_from(
        aaa=[league_split(2025, 11, "International League", pa=140, bb=14, so=32, ab=122, hits=36, tb=60)],
        aa=[league_split(2024, 12, "Texas League", pa=520, bb=52, so=115, ab=455, hits=135, tb=230)],
    )
    entry = player_projection(player(), payloads, 120, True)
    assert {lvl.level_code for lvl in entry.levels} == {"AAA", "AA"}
    assert all(lvl.included for lvl in entry.levels)


def test_the_pa_filter_is_applied_per_level_not_per_player():
    payloads = payloads_from(
        aaa=[league_split(2025, 11, "International League", pa=60, bb=6, so=15, ab=52, hits=15, tb=26)],
        aa=[league_split(2024, 12, "Eastern League", pa=480, bb=48, so=110, ab=420, hits=125, tb=210)],
    )
    entry = player_projection(player(), payloads, min_pa=120, apply_filter=True)
    by_code = {lvl.level_code: lvl for lvl in entry.levels}
    assert by_code["AAA"].included is False
    assert "60 PA < 120" in by_code["AAA"].note
    assert by_code["AA"].included is True  # the other level is unaffected


def test_unchecking_the_filter_includes_every_level():
    payloads = payloads_from(
        aaa=[league_split(2025, 11, "International League", pa=60, bb=6, so=15, ab=52, hits=15, tb=26)],
        aa=[league_split(2024, 12, "Eastern League", pa=480, bb=48, so=110, ab=420, hits=125, tb=210)],
    )
    entry = player_projection(player(), payloads, 120, apply_filter=False)
    assert all(lvl.included for lvl in entry.levels)


def test_the_actual_mlb_line_is_shared_by_every_level_row():
    payloads = payloads_from(
        aaa=[league_split(2025, 11, "International League", pa=300, bb=30, so=70, ab=260, hits=78, tb=130)],
        aa=[league_split(2024, 12, "Eastern League", pa=400, bb=40, so=90, ab=350, hits=105, tb=175)],
        mlb=[league_split(2026, 1, "AL", pa=500, bb=45, so=130, ab=445, hits=115, tb=190)],
    )
    entry = player_projection(player(), payloads, 120, True)
    assert entry.has_actual
    # one actual, but a residual per level — that is the point of three rows
    residuals = [lvl.residual["ops"] for lvl in entry.levels]
    assert all(r is not None for r in residuals)
    assert residuals[0] != residuals[1]


def test_a_player_with_no_mlb_time_still_projects_every_level():
    payloads = payloads_from(
        aa=[league_split(2024, 12, "Eastern League", pa=400, bb=40, so=90, ab=350, hits=105, tb=175)]
    )
    entry = player_projection(player(), payloads, 120, True)
    assert entry.has_actual is False
    assert entry.levels[0].included is True
    assert all(v is None for v in entry.levels[0].residual.values())


def test_a_player_with_no_covered_minor_league_has_no_level_rows():
    payloads = payloads_from(
        mlb=[league_split(2024, 1, "NL", pa=600, bb=60, so=150, ab=520, hits=140, tb=250)]
    )
    entry = player_projection(player(), payloads, 120, True)
    assert entry.levels == ()
    assert entry.has_levels is False
    assert entry.row_count == 1  # still occupies a row, with its reason
    assert "no AAA / AA / A+" in entry.note
    assert entry.actual is not None


def test_an_unrecognised_league_is_named_on_the_player():
    payloads = payloads_from(
        aaa=[league_split(2024, 11, "Mexican League", pa=400, bb=40, so=90, ab=350, hits=105, tb=175)],
        aa=[league_split(2023, 12, "Texas League", pa=300, bb=30, so=70, ab=260, hits=78, tb=130)],
    )
    entry = player_projection(player(), payloads, 120, True)
    assert entry.unmatched_leagues == ("Mexican League",)
    assert [lvl.level_code for lvl in entry.levels] == ["AA"]


def test_a_near_tie_within_a_level_is_flagged():
    payloads = payloads_from(
        aaa=[
            league_split(2024, 11, "International League", pa=300, bb=30, so=70, ab=260, hits=78, tb=130),
            league_split(2025, 11, "Pacific Coast League", pa=290, bb=29, so=65, ab=250, hits=75, tb=125),
        ]
    )
    assert player_projection(player(), payloads, 120, True).levels[0].is_close_call is True


def test_a_lopsided_split_within_a_level_is_not_a_close_call():
    payloads = payloads_from(
        aaa=[
            league_split(2024, 11, "International League", pa=600, bb=60, so=140, ab=520, hits=155, tb=260),
            league_split(2025, 11, "Pacific Coast League", pa=100, bb=10, so=22, ab=88, hits=26, tb=44),
        ]
    )
    assert player_projection(player(), payloads, 120, True).levels[0].is_close_call is False


def test_a_single_league_at_a_level_is_never_a_close_call():
    payloads = payloads_from(
        aa=[league_split(2024, 12, "Texas League", pa=400, bb=40, so=90, ab=350, hits=105, tb=175)]
    )
    assert player_projection(player(), payloads, 120, True).levels[0].is_close_call is False


def test_lines_by_level_keeps_each_level_sorted_by_pa():
    payloads = payloads_from(
        aaa=[
            league_split(2024, 11, "International League", pa=100, bb=10, so=24, ab=88, hits=26, tb=44),
            league_split(2025, 11, "Pacific Coast League", pa=400, bb=40, so=90, ab=350, hits=105, tb=175),
        ],
        aa=[league_split(2023, 12, "Texas League", pa=250, bb=25, so=55, ab=218, hits=65, tb=110)],
    )
    lines, _ = league_lines(payloads)
    grouped = lines_by_level(lines)
    assert [l.league_key for l in grouped["AAA"]] == ["pacific-coast", "international"]
    assert [l.league_key for l in grouped["AA"]] == ["texas"]


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
# residual scoring, per level
# ----------------------------------------------------------------------
def _scored_player(pid, *, aaa=None, aa=None, mlb):
    kwargs = {}
    if aaa:
        kwargs["aaa"] = [league_split(2025, 11, "International League", **aaa)]
    if aa:
        kwargs["aa"] = [league_split(2024, 12, "Eastern League", **aa)]
    kwargs["mlb"] = [league_split(2026, 1, "AL", **mlb)]
    return player_projection(player(pid), payloads_from(**kwargs), 120, True)


def test_residuals_are_kept_separately_per_level():
    """The whole reason for three rows: comparing the rungs against each other."""
    entry = _scored_player(
        1,
        aaa=dict(pa=300, bb=30, so=70, ab=260, hits=78, tb=130),
        aa=dict(pa=400, bb=40, so=90, ab=350, hits=105, tb=175),
        mlb=dict(pa=500, bb=45, so=130, ab=445, hits=115, tb=190),
    )
    residuals, mean_abs = summarise_residuals([entry])
    assert residuals["AAA"]["ops"].n == 1
    assert residuals["AA"]["ops"].n == 1
    assert residuals["A+"]["ops"].n == 0
    assert residuals["AAA"]["ops"].mean != residuals["AA"]["ops"].mean
    assert mean_abs["AAA"]["ops"] is not None


def test_only_players_with_real_mlb_numbers_are_scored():
    scored = _scored_player(1, aaa=dict(pa=300, bb=30, so=70, ab=260, hits=78, tb=130),
                            mlb=dict(pa=500, bb=45, so=130, ab=445, hits=115, tb=190))
    unscored = player_projection(
        player(2),
        payloads_from(aaa=[league_split(2025, 11, "International League", pa=300, bb=30, so=70, ab=260, hits=78, tb=130)]),
        120, True,
    )
    residuals, _ = summarise_residuals([scored, unscored])
    assert residuals["AAA"]["ops"].n == 1


def test_skipped_levels_do_not_enter_the_score():
    entry = _scored_player(1, aaa=dict(pa=40, bb=4, so=10, ab=35, hits=10, tb=17),
                           mlb=dict(pa=500, bb=45, so=130, ab=445, hits=115, tb=190))
    residuals, _ = summarise_residuals([entry])
    assert residuals["AAA"]["ops"].n == 0


def test_mean_absolute_residual_does_not_cancel_out():
    """Two equal-and-opposite misses are zero bias but a real typical miss."""
    from app.services.projection import LeagueLine, LevelProjection, PlayerProjection
    from app.services.models import StatLine

    def fake(pid, residual):
        line = LeagueLine(league_key="international", plate_appearances=300,
                          ops=0.8, slg=0.45, walk_rate=0.1, strikeout_rate=0.2)
        level = LevelProjection(level_code="AAA", source=line, included=True,
                                residual={"ops": residual, "slg": None, "bb": None, "k": None})
        return PlayerProjection(
            player=player(pid), levels=(level,),
            actual=StatLine("MLB", 500, 0.7, 0.4, 0.08, 0.25),
        )

    residuals, mean_abs = summarise_residuals([fake(1, 0.10), fake(2, -0.10)])
    assert residuals["AAA"]["ops"].mean == pytest.approx(0.0)
    assert mean_abs["AAA"]["ops"] == pytest.approx(0.10)


def test_no_scored_players_gives_empty_residuals_for_every_level():
    residuals, mean_abs = summarise_residuals([])
    for code in ("AAA", "AA", "A+"):
        assert residuals[code]["ops"].n == 0
        assert mean_abs[code]["ops"] is None


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
    assert result.projected_line_count == 1
    assert result.projected_player_count == 1


def test_one_player_with_three_levels_yields_three_projected_lines():
    stub = StubBulkClient(
        {"Mayer": [player(1, "Marcelo Mayer")]},
        {1: payloads_from(
            aaa=[league_split(2025, 11, "International League", pa=300, bb=33, so=70, ab=258, hits=78, tb=132)],
            aa=[league_split(2024, 12, "Eastern League", pa=480, bb=48, so=118, ab=420, hits=118, tb=205)],
            aplus=[league_split(2023, 13, "South Atlantic League", pa=350, bb=40, so=80, ab=300, hits=90, tb=150)],
        )},
    )
    result = build_projections(stub, "Mayer", 120, True)
    assert result.projected_player_count == 1
    assert result.projected_line_count == 3
    assert [lvl.level_code for lvl in result.players[0].levels] == ["AAA", "AA", "A+"]


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
    assert result.projected_line_count == 1
    assert result.skipped_line_count == 1
    assert [e.reason for e in result.unresolved] == ["not found"]


def test_build_projections_with_nobody_resolvable_is_empty_not_broken():
    result = build_projections(StubBulkClient({"Ghost": []}), "Ghost", 120, True)
    assert result.players == []
    assert result.residuals["AAA"]["ops"].n == 0
    assert result.levels_with_scores == []
