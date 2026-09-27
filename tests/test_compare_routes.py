from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import main
from app.services.models import MLBAPIError, PlayerMatch
from tests.test_compare import StubBulkClient, levels, player


@pytest.fixture()
def http(monkeypatch, cache):
    monkeypatch.setattr(main.client, "cache", cache)
    return TestClient(main.app)


def use(monkeypatch, stub) -> None:
    monkeypatch.setattr(main, "client", stub)


SAMPLE = StubBulkClient(
    matches_by_query={
        "Good": [player(1, "Good Hitter")],
        "Small": [player(2, "Small Sample")],
        "Will Smith": [
            PlayerMatch(person_id=3, full_name="Will Smith", position="C"),
            PlayerMatch(person_id=4, full_name="Will Smith", position="P"),
        ],
    },
    payloads_by_person={
        1: levels(mlb=(600, 60, 180, 540, 135, 240), aaa=(400, 40, 90, 350, 110, 200)),
        2: levels(mlb=(600, 60, 180, 540, 135, 240), aaa=(30, 3, 8, 26, 8, 14)),
    },
)


@pytest.mark.parametrize("path", ["/", "/compare"])
def test_both_tabs_are_present_in_the_masthead_of_every_page(http, path):
    text = http.get(path).text
    assert 'href="/compare"' in text
    assert 'class="tabs"' in text
    assert "One player" in text and "Compare" in text


def test_home_page_carries_an_in_body_link_to_compare(http):
    assert 'class="crosslink" href="/compare"' in http.get("/").text


def test_the_active_tab_tracks_the_current_page(http):
    home = http.get("/").text
    compare = http.get("/compare").text
    assert home.index('href="/"') < home.index("is-active") + 200
    assert 'href="/compare" class="is-active"' in compare.replace("  ", " ")


def test_compare_form_renders_all_five_level_pairs(http):
    text = http.get("/compare").text
    assert text.count("<option") == 5
    for expected in ("MLB vs AAA", "MLB vs AA", "MLB vs A+", "AAA vs AA", "AA vs A+"):
        assert expected in text


def test_empty_submission_asks_for_names_instead_of_erroring(http):
    r = http.post("/compare", data={"players": "   "})
    assert r.status_code == 200
    assert "Add at least one player" in r.text


def test_submitting_a_list_renders_deltas_and_summary_rows(http, monkeypatch):
    use(monkeypatch, SAMPLE)
    r = http.post(
        "/compare",
        data={"players": "Good\nSmall\nWill Smith", "pair": "mlb-aaa", "min_pa": "120", "exclude": "on"},
    )
    assert r.status_code == 200
    assert "Good Hitter" in r.text
    assert "Mean" in r.text and "Median" in r.text
    assert "Players in sample (n)" in r.text
    # Small Sample is greyed out, with the reason shown
    assert "30 AAA PA &lt; 120" in r.text or "30 AAA PA < 120" in r.text
    # Will Smith is not guessed at
    assert "Unresolved" in r.text and "ambiguous" in r.text


def test_unchecking_the_box_includes_the_small_sample(http, monkeypatch):
    use(monkeypatch, SAMPLE)
    with_filter = http.post(
        "/compare", data={"players": "Good\nSmall", "min_pa": "120", "exclude": "on"}
    ).text
    without_filter = http.post(
        "/compare", data={"players": "Good\nSmall", "min_pa": "120"}
    ).text
    assert "1 of 2 players" in with_filter
    assert "2 of 2 players" in without_filter


def test_level_pair_choice_is_honoured_and_echoed_back(http, monkeypatch):
    stub = StubBulkClient(
        {"Good": [player(1, "Good Hitter")]},
        {1: levels(mlb=(600, 60, 180, 540, 135, 240), aa=(400, 40, 90, 350, 110, 200))},
    )
    use(monkeypatch, stub)
    r = http.post("/compare", data={"players": "Good", "pair": "mlb-aa", "min_pa": "120"})
    assert stub.requested_sport_ids == [1, 12]
    assert 'value="mlb-aa" selected' in r.text
    assert "MLB − AA" in r.text


def test_an_unknown_pair_key_falls_back_to_the_default(http, monkeypatch):
    stub = StubBulkClient(
        {"Good": [player(1, "Good Hitter")]},
        {1: levels(mlb=(600, 60, 180, 540, 135, 240), aaa=(400, 40, 90, 350, 110, 200))},
    )
    use(monkeypatch, stub)
    r = http.post("/compare", data={"players": "Good", "pair": "nonsense"})
    assert r.status_code == 200
    assert stub.requested_sport_ids == [1, 11]


def test_the_submitted_list_is_echoed_back_into_the_textarea(http, monkeypatch):
    use(monkeypatch, SAMPLE)
    r = http.post("/compare", data={"players": "Good\nSmall", "min_pa": "120"})
    assert "Good\nSmall" in r.text


def test_too_many_names_are_truncated_with_a_warning(http, monkeypatch):
    names = "\n".join(f"Player {i}" for i in range(main.MAX_BULK_PLAYERS + 5))
    use(monkeypatch, StubBulkClient({}))
    r = http.post("/compare", data={"players": names})
    assert "only the first" in r.text


def test_api_failure_shows_the_error_page(http, monkeypatch):
    class Boom:
        def bulk_find_players(self, queries):
            raise MLBAPIError("read timed out")

    use(monkeypatch, Boom())
    r = http.post("/compare", data={"players": "Good"})
    assert r.status_code == 502
    assert "did not come through" in r.text
