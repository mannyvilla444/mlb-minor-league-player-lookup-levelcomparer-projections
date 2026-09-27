from __future__ import annotations

import math

import pytest

from app.services.models import PlayerMatch
from app.services.transforms import (
    build_report,
    career_by_level,
    extract_splits,
    rows_from_payloads,
    safe_rate,
    to_float,
)
from tests.fixtures import people_payload, split


def test_to_float_handles_baseball_style_strings():
    assert to_float(".812") == pytest.approx(0.812)
    assert to_float("0.812") == pytest.approx(0.812)
    assert to_float(None) is None
    assert to_float(".---") is None
    assert to_float("") is None


def test_bb_and_k_rates_are_walks_over_plate_appearances():
    payload = people_payload([split("2024", 1, pa=600, bb=60, so=150)])
    rows = rows_from_payloads({1: payload})
    assert len(rows) == 1
    row = rows[0]
    assert row.plate_appearances == 600
    assert row.walk_rate == pytest.approx(0.10)
    assert row.strikeout_rate == pytest.approx(0.25)


def test_zero_pa_rows_are_dropped():
    payload = people_payload(
        [split("2024", 1, pa=0), split("2023", 1, pa=400)]
    )
    rows = rows_from_payloads({1: payload})
    assert [r.season for r in rows] == ["2023"]


def test_missing_pa_field_drops_the_row():
    payload = people_payload([split("2024", 1, drop=("plateAppearances",))])
    assert rows_from_payloads({1: payload}) == []


def test_missing_optional_field_keeps_row_with_none():
    payload = people_payload([split("2024", 1, drop=("ops",))])
    row = rows_from_payloads({1: payload})[0]
    assert row.ops is None
    assert row.slg is not None
    assert row.walk_rate is not None


def test_missing_walks_leaves_bb_rate_none_but_keeps_k_rate():
    payload = people_payload([split("2024", 1, drop=("baseOnBalls",))])
    row = rows_from_payloads({1: payload})[0]
    assert row.walk_rate is None
    assert row.strikeout_rate is not None


def test_levels_are_combined_and_sorted_mlb_first_within_a_season():
    payloads = {
        1: people_payload([split("2024", 1, team="Reds", pa=500)]),
        11: people_payload([split("2024", 11, team="Louisville", pa=170)]),
        12: people_payload([split("2023", 12, team="Chattanooga", pa=250)]),
    }
    rows = rows_from_payloads(payloads)
    assert [(r.season, r.level_code) for r in rows] == [
        ("2024", "MLB"),
        ("2024", "AAA"),
        ("2023", "AA"),
    ]


def test_two_teams_in_one_season_at_one_level_fold_into_one_row():
    payload = people_payload(
        [
            split("2024", 11, team="Club A", pa=200, bb=20, so=50, ab=180, hits=45, tb=80),
            split("2024", 11, team="Club B", pa=300, bb=30, so=75, ab=270, hits=81, tb=140),
        ]
    )
    rows = rows_from_payloads({11: payload})
    assert len(rows) == 1
    row = rows[0]
    assert row.plate_appearances == 500
    assert row.team == "2 teams"
    assert row.walk_rate == pytest.approx(50 / 500)
    # SLG recomputed from totals, not averaged: 220 TB / 450 AB
    assert row.slg == pytest.approx(220 / 450)


def test_career_by_level_sums_pa_and_recomputes_rates_exactly():
    payload = people_payload(
        [
            split("2024", 1, pa=600, bb=60, so=150, ab=520, hits=140, tb=250, hbp=5, sf=6),
            split("2023", 1, pa=400, bb=30, so=110, ab=360, hits=95, tb=160, hbp=3, sf=4),
        ]
    )
    rows = rows_from_payloads({1: payload})
    summaries = career_by_level(rows)
    assert len(summaries) == 1
    mlb = summaries[0]
    assert mlb.level_code == "MLB"
    assert mlb.seasons == 2
    assert mlb.plate_appearances == 1000
    assert mlb.walk_rate == pytest.approx(90 / 1000)
    assert mlb.strikeout_rate == pytest.approx(260 / 1000)
    assert mlb.slg == pytest.approx(410 / 880)
    assert "exact" in mlb.method


def test_career_by_level_falls_back_to_pa_weighted_average_when_components_missing():
    payload = people_payload(
        [
            split("2024", 11, pa=600, ops=".900", slg=".500", drop=("totalBases",)),
            split("2023", 11, pa=200, ops=".700", slg=".400", drop=("totalBases",)),
        ]
    )
    rows = rows_from_payloads({11: payload})
    summary = career_by_level(rows)[0]
    assert summary.ops == pytest.approx((0.900 * 600 + 0.700 * 200) / 800)
    assert "PA-weighted" in summary.method


def test_career_by_level_orders_mlb_above_the_minors():
    payloads = {
        14: people_payload([split("2022", 14)]),
        1: people_payload([split("2024", 1)]),
        12: people_payload([split("2023", 12)]),
    }
    rows = rows_from_payloads(payloads)
    assert [s.level_code for s in career_by_level(rows)] == ["MLB", "AA", "A"]


def test_milestone_marks_the_season_the_running_mlb_pa_crosses_900():
    payloads = {
        1: people_payload(
            [
                split("2023", 1, pa=400),
                split("2024", 1, pa=600),
                split("2025", 1, pa=500),
            ]
        ),
        11: people_payload([split("2023", 11, pa=300)]),
    }
    player = PlayerMatch(person_id=1, full_name="Test Player")
    report = build_report(player, payloads)
    assert report.milestone_season == "2024"
    assert report.milestone_total_pa == 1000
    by_season = {r.season: r.cumulative_mlb_pa for r in report.rows if r.sport_id == 1}
    assert by_season == {"2023": 400, "2024": 1000, "2025": 1500}
    # Minor-league rows carry no MLB running total.
    assert all(r.cumulative_mlb_pa is None for r in report.rows if r.sport_id != 1)


def test_milestone_is_none_when_player_never_reaches_it():
    payloads = {1: people_payload([split("2025", 1, pa=120)])}
    report = build_report(PlayerMatch(person_id=1, full_name="Rookie"), payloads)
    assert report.milestone_season is None


def test_empty_payload_produces_empty_report():
    report = build_report(PlayerMatch(person_id=1, full_name="Nobody"), {1: {"people": []}})
    assert report.rows == []
    assert report.by_level == []
    assert report.has_rows is False


def test_extract_splits_ignores_non_hitting_groups():
    payload = people_payload([split("2024", 1)])
    payload["people"][0]["stats"].append(
        {
            "type": {"displayName": "yearByYear"},
            "group": {"displayName": "pitching"},
            "splits": [split("2024", 1, pa=99)],
        }
    )
    assert len(extract_splits(payload)) == 1


def test_safe_rate_guards_against_zero_and_none():
    assert safe_rate(10, 0) is None
    assert safe_rate(None, 100) is None
    assert math.isclose(safe_rate(10, 100), 0.1)
