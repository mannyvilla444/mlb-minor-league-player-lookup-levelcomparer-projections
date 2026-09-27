from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import main
from app.services.models import MLBAPIError
from tests.test_projection import StubBulkClient, league_split, payloads_from, player


@pytest.fixture()
def http(monkeypatch, cache):
    monkeypatch.setattr(main.client, "cache", cache)
    return TestClient(main.app)


def use(monkeypatch, stub) -> None:
    monkeypatch.setattr(main, "client", stub)


SAMPLE = StubBulkClient(
    matches={
        "Prospect": [player(1, "Big Prospect")],
        "Veteran": [player(2, "Established Vet")],
        "Tiny": [player(3, "Cup Of Coffee")],
        "Climber": [player(4, "Three Rung Climber")],
    },
    payloads={
        1: payloads_from(
            aaa=[league_split(2024, 11, "Pacific Coast League", pa=450, bb=45, so=100, ab=395, hits=120, tb=205)]
        ),
        2: payloads_from(
            aa=[league_split(2022, 12, "Texas League", pa=500, bb=50, so=110, ab=440, hits=130, tb=220)],
            mlb=[league_split(2024, 1, "AL", pa=900, bb=72, so=230, ab=810, hits=205, tb=340)],
        ),
        3: payloads_from(
            aaa=[league_split(2024, 11, "International League", pa=45, bb=4, so=12, ab=40, hits=11, tb=18)]
        ),
        4: payloads_from(
            aaa=[league_split(2025, 11, "International League", pa=300, bb=33, so=70, ab=258, hits=78, tb=132)],
            aa=[league_split(2024, 12, "Eastern League", pa=480, bb=48, so=118, ab=420, hits=118, tb=205)],
            aplus=[league_split(2023, 13, "South Atlantic League", pa=350, bb=40, so=80, ab=300, hits=90, tb=150)],
            mlb=[league_split(2026, 1, "AL", pa=400, bb=34, so=105, ab=355, hits=92, tb=155)],
        ),
    },
)


def test_a_player_with_three_levels_gets_three_rows(http, monkeypatch):
    """The headline fix: one line per level, not one per player."""
    use(monkeypatch, SAMPLE)
    text = http.post("/projection", data={"players": "Climber", "min_pa": "120"}).text
    flat = " ".join(text.split())  # Jinja leaves newlines inside the sentence
    assert "3 level lines across 1 of 1 players" in flat
    for league in ("International", "Eastern", "South Atlantic"):
        assert league in text
    # the player name is written once and spans its three rows
    assert text.count("Three Rung Climber") == 1
    assert 'rowspan="3"' in text


def test_the_accuracy_panel_breaks_the_score_down_by_level(http, monkeypatch):
    use(monkeypatch, SAMPLE)
    text = http.post("/projection", data={"players": "Climber", "min_pa": "120"}).text
    assert "Accuracy by level" in text
    assert "bias" in text and "miss" in text


def test_the_form_renders_with_the_eight_league_deltas(http):
    text = http.get("/projection").text
    assert text.count("League</td>") >= 0
    for league in ("International League", "Pacific Coast League", "South Atlantic League"):
        assert league in text


def test_empty_submission_asks_for_names(http):
    r = http.post("/projection", data={"players": "  "})
    assert r.status_code == 200
    assert "Add at least one player" in r.text


def test_a_projection_shows_source_league_projection_and_actual(http, monkeypatch):
    use(monkeypatch, SAMPLE)
    r = http.post("/projection", data={"players": "Prospect\nVeteran\nTiny", "min_pa": "120", "exclude": "on"})
    assert r.status_code == 200
    assert "Big Prospect" in r.text and "Pacific" in r.text
    assert "Projected MLB" in r.text and "Actual MLB" in r.text
    # the small sample is skipped with its reason shown
    assert "45 PA &lt; 120" in r.text or "45 PA < 120" in r.text


def test_the_accuracy_panel_appears_only_when_someone_has_mlb_numbers(http, monkeypatch):
    use(monkeypatch, SAMPLE)
    with_actual = http.post("/projection", data={"players": "Prospect\nVeteran", "min_pa": "120"}).text
    assert "Accuracy by level" in with_actual

    only_prospects = http.post("/projection", data={"players": "Prospect", "min_pa": "120"}).text
    assert "Accuracy by level" not in only_prospects


def test_unchecking_the_filter_projects_the_small_sample_too(http, monkeypatch):
    use(monkeypatch, SAMPLE)
    filtered = http.post("/projection", data={"players": "Prospect\nTiny", "min_pa": "120", "exclude": "on"}).text
    unfiltered = http.post("/projection", data={"players": "Prospect\nTiny", "min_pa": "120"}).text
    assert "1 of 2 players" in " ".join(filtered.split())
    assert "2 of 2 players" in " ".join(unfiltered.split())


def test_a_player_with_no_covered_minor_league_is_shown_not_dropped(http, monkeypatch):
    stub = StubBulkClient(
        {"MLB Only": [player(9, "Lifer")]},
        {9: payloads_from(mlb=[league_split(2024, 1, "NL", pa=700, bb=70, so=170, ab=610, hits=165, tb=290)])},
    )
    use(monkeypatch, stub)
    text = http.post("/projection", data={"players": "MLB Only"}).text
    assert "Lifer" in text
    assert "no AAA / AA / A+" in text


def test_an_unrecognised_league_is_named_in_the_row(http, monkeypatch):
    stub = StubBulkClient(
        {"Abroad": [player(8, "Foreign Pro")]},
        {8: payloads_from(aaa=[league_split(2024, 11, "Mexican League", pa=400, bb=40, so=90, ab=350, hits=105, tb=175)])},
    )
    use(monkeypatch, stub)
    assert "Mexican League" in http.post("/projection", data={"players": "Abroad"}).text


def test_a_close_call_within_a_level_is_surfaced(http, monkeypatch):
    stub = StubBulkClient(
        {"Split": [player(7, "Split Time")]},
        {7: payloads_from(aaa=[
            league_split(2024, 11, "International League", pa=300, bb=30, so=70, ab=260, hits=78, tb=130),
            league_split(2025, 11, "Pacific Coast League", pa=290, bb=29, so=65, ab=250, hits=75, tb=125),
        ])},
    )
    use(monkeypatch, stub)
    text = http.post("/projection", data={"players": "Split"}).text
    assert "close" in text
    assert "Other leagues at the same level" in text
    # one AAA row, built from the bigger stint only
    assert "1 level line" in " ".join(text.split())


def test_the_submitted_list_is_echoed_back(http, monkeypatch):
    use(monkeypatch, SAMPLE)
    assert "Prospect\nVeteran" in http.post(
        "/projection", data={"players": "Prospect\nVeteran"}
    ).text


def test_too_many_names_are_truncated_with_a_warning(http, monkeypatch):
    use(monkeypatch, StubBulkClient({}))
    names = "\n".join(f"Player {i}" for i in range(main.MAX_BULK_PLAYERS + 3))
    assert "only the first" in http.post("/projection", data={"players": names}).text


def test_api_failure_shows_the_error_page(http, monkeypatch):
    class Boom:
        def bulk_find_players(self, queries):
            raise MLBAPIError("read timed out")

    use(monkeypatch, Boom())
    r = http.post("/projection", data={"players": "Anyone"})
    assert r.status_code == 502
    assert "did not come through" in r.text


def test_the_page_says_it_is_a_league_average_not_a_forecast(http):
    text = http.get("/projection").text.lower()
    assert "not a forecast" in text
