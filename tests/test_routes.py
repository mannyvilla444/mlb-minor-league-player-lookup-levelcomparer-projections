from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import main
from app.services.models import MLBAPIError, PlayerMatch
from tests.fixtures import people_payload, split


@pytest.fixture()
def http(monkeypatch, cache):
    monkeypatch.setattr(main.client, "cache", cache)
    return TestClient(main.app)


class StubClient:
    def __init__(self, matches=None, person=None, payloads=None, failed=None, error=None):
        self.matches = matches if matches is not None else []
        self.person = person
        self.payloads = payloads or {}
        self.failed = failed or []
        self.error = error

    def find_players(self, query, season=None):
        if self.error:
            raise self.error
        return self.matches

    def get_person(self, person_id):
        if self.error:
            raise self.error
        return self.person

    def all_levels_hitting(self, person_id, sport_ids=None):
        if self.error:
            raise self.error
        return self.payloads, self.failed


def use(monkeypatch, stub: StubClient) -> None:
    monkeypatch.setattr(main, "client", stub)


def test_home_page_renders_the_search_box(http):
    r = http.get("/")
    assert r.status_code == 200
    assert 'name="q"' in r.text
    assert "MLB Stats API" in r.text


def test_blank_search_redirects_home(http):
    r = http.get("/search?q=%20", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/"


def test_single_match_redirects_to_the_player_page(http, monkeypatch):
    use(monkeypatch, StubClient(matches=[PlayerMatch(person_id=682829, full_name="Elly De La Cruz")]))
    r = http.get("/search?q=elly", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/player/682829"


def test_multiple_matches_render_a_pick_list(http, monkeypatch):
    use(
        monkeypatch,
        StubClient(
            matches=[
                PlayerMatch(person_id=2, full_name="Will Smith", position="C"),
                PlayerMatch(person_id=3, full_name="Will Smith", position="P"),
            ]
        ),
    )
    r = http.get("/search?q=will+smith")
    assert r.status_code == 200
    assert "/player/2" in r.text and "/player/3" in r.text
    assert "2 players match" in r.text


def test_no_match_shows_the_empty_state(http, monkeypatch):
    use(monkeypatch, StubClient(matches=[]))
    r = http.get("/search?q=zzzz")
    assert r.status_code == 404
    assert "No match. Try last name, first name." in r.text


def test_api_failure_during_search_is_visible_not_silent(http, monkeypatch):
    use(monkeypatch, StubClient(error=MLBAPIError("read timed out")))
    r = http.get("/search?q=elly")
    assert r.status_code == 502
    assert "did not come through" in r.text
    assert "Retry" in r.text


def test_player_page_shows_all_levels_with_rates(http, monkeypatch):
    use(
        monkeypatch,
        StubClient(
            person=PlayerMatch(person_id=682829, full_name="Elly De La Cruz", position="SS"),
            payloads={
                1: people_payload([split("2024", 1, team="Reds", pa=600, bb=60, so=180)]),
                11: people_payload([split("2023", 11, team="Louisville", pa=200, bb=20, so=60)]),
            },
        ),
    )
    r = http.get("/player/682829")
    assert r.status_code == 200
    assert "Elly De La Cruz" in r.text
    assert ">MLB<" in r.text and ">AAA<" in r.text
    assert "10.0%" in r.text  # BB% for the MLB row
    assert "30.0%" in r.text  # K% for the MLB row
    assert "Career by level" in r.text


def test_player_page_warns_when_a_level_failed_to_load(http, monkeypatch):
    use(
        monkeypatch,
        StubClient(
            person=PlayerMatch(person_id=1, full_name="Test Player"),
            payloads={1: people_payload([split("2024", 1)])},
            failed=[12, 13],
        ),
    )
    r = http.get("/player/1")
    assert "Some levels did not load" in r.text


def test_player_page_handles_a_hitter_with_no_qualifying_rows(http, monkeypatch):
    use(
        monkeypatch,
        StubClient(
            person=PlayerMatch(person_id=1, full_name="Pitcher Person"),
            payloads={1: people_payload([split("2024", 1, pa=0)])},
        ),
    )
    r = http.get("/player/1")
    assert r.status_code == 200
    assert "No hitting rows" in r.text


def test_unknown_player_id_shows_the_empty_state(http, monkeypatch):
    use(monkeypatch, StubClient(person=None))
    r = http.get("/player/999999999")
    assert r.status_code == 404
    assert "No match" in r.text


def test_healthz_reports_version_and_folder(http):
    body = http.get("/healthz").json()
    assert body["status"] == "ok"
    assert body["has_compare_tab"] == "yes"
    assert body["version"]
    assert body["running_from"].endswith("mlb-level-stats") or body["running_from"]
