from __future__ import annotations

import time

import pytest
import statsapi

from app.services.models import MLBAPIError
from tests.fixtures import PERSON_ONLY, ROSTER, people_payload, split


class FakeAPI:
    """Stands in for statsapi.get and records every call."""

    def __init__(self, responses: dict[str, object] | None = None, error: Exception | None = None):
        self.responses = responses or {}
        self.error = error
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, endpoint, params=None, force=False, *, request_kwargs=None):
        self.calls.append((endpoint, dict(params or {})))
        if self.error is not None:
            raise self.error
        if endpoint in self.responses:
            value = self.responses[endpoint]
            return value(params) if callable(value) else value
        raise ValueError(f"unexpected endpoint {endpoint}")


@pytest.fixture()
def fake(monkeypatch):
    def install(api: FakeAPI) -> FakeAPI:
        monkeypatch.setattr(statsapi, "get", api)
        return api

    return install


# ----------------------------------------------------------------------
# name lookup
# ----------------------------------------------------------------------
def test_single_name_match_returns_one_player(client, fake):
    fake(FakeAPI({"people_search_ext": PERSON_ONLY}))
    matches = client.find_players("Elly De La Cruz")
    assert len(matches) == 1
    assert matches[0].person_id == 682829
    assert matches[0].position == "SS"


def test_multiple_matches_are_all_returned(client, fake):
    api = fake(
        FakeAPI({"people_search_ext": {"people": ROSTER["people"][1:]}})
    )
    matches = client.find_players("Will Smith")
    assert [m.person_id for m in matches] == [2, 3]
    assert api.calls[0][0] == "people_search_ext"


def test_unknown_name_returns_empty_list(client, fake):
    fake(FakeAPI({"people_search_ext": {"people": []}, "sports_players": {"people": []}}))
    assert client.find_players("Zzzz Nobody") == []


def test_blank_query_makes_no_api_call(client, fake):
    api = fake(FakeAPI({}))
    assert client.find_players("   ") == []
    assert api.calls == []


def test_numeric_query_is_treated_as_a_person_id(client, fake):
    api = fake(FakeAPI({"people": PERSON_ONLY}))
    matches = client.find_players("682829")
    assert matches[0].full_name == "Elly De La Cruz"
    assert api.calls[0][0] == "people"


def test_roster_scan_is_used_when_the_search_endpoint_fails(client, fake):
    def responder(params):
        if params.get("sportId") == 1:
            return ROSTER
        return {"people": []}

    api = fake(FakeAPI({"sports_players": responder}))
    matches = client.find_players("will smith")
    assert {m.person_id for m in matches} == {2, 3}
    assert any(endpoint == "sports_players" for endpoint, _ in api.calls)


def test_roster_scan_matches_every_term_in_the_query(client, fake):
    fake(FakeAPI({"sports_players": lambda p: ROSTER if p.get("sportId") == 1 else {"people": []}}))
    assert [m.full_name for m in client.find_players("aaron judge")] == ["Aaron Judge"]


# ----------------------------------------------------------------------
# stats
# ----------------------------------------------------------------------
def test_all_levels_hitting_requests_one_hydrate_per_sport_id(client, fake):
    api = fake(FakeAPI({"people": lambda p: people_payload([split("2024", 1)])}))
    payloads, failed = client.all_levels_hitting(682829, sport_ids=[1, 11, 12])
    assert set(payloads) == {1, 11, 12}
    assert failed == []
    hydrates = [params["hydrate"] for _, params in api.calls]
    assert "sportId=1)" in hydrates[0]
    assert len(api.calls) == 3


def test_one_failing_level_does_not_sink_the_whole_lookup(client, fake):
    def responder(params):
        if "sportId=12)" in params.get("hydrate", ""):
            raise ConnectionError("boom")
        return people_payload([split("2024", 1)])

    fake(FakeAPI({"people": responder}))
    payloads, failed = client.all_levels_hitting(682829, sport_ids=[1, 11, 12])
    assert set(payloads) == {1, 11}
    assert failed == [12]


def test_total_api_failure_raises_a_visible_error(client, fake):
    fake(FakeAPI(error=TimeoutError("read timed out")))
    with pytest.raises(MLBAPIError) as exc:
        client.all_levels_hitting(682829, sport_ids=[1, 11])
    assert "failed" in str(exc.value).lower()


def test_requests_are_retried_before_giving_up(client, fake):
    api = fake(FakeAPI(error=TimeoutError("read timed out")))
    with pytest.raises(MLBAPIError):
        client.all_levels_hitting(682829, sport_ids=[1])
    # max_retries=2 in the fixture
    assert len(api.calls) == 2


def test_timeout_is_passed_through_to_statsapi(client, monkeypatch):
    seen: dict = {}

    def fake_get(endpoint, params=None, force=False, *, request_kwargs=None):
        seen.update(request_kwargs or {})
        return people_payload([split("2024", 1)])

    monkeypatch.setattr(statsapi, "get", fake_get)
    client.year_by_year_hitting(1, 1)
    assert seen == {"timeout": 1.0}


# ----------------------------------------------------------------------
# cache
# ----------------------------------------------------------------------
def test_second_lookup_is_served_from_cache(client, fake):
    api = fake(FakeAPI({"people": lambda p: people_payload([split("2024", 1)])}))
    client.year_by_year_hitting(682829, 1)
    client.year_by_year_hitting(682829, 1)
    assert len(api.calls) == 1


def test_cache_key_separates_levels(client, fake):
    api = fake(FakeAPI({"people": lambda p: people_payload([split("2024", 1)])}))
    client.year_by_year_hitting(682829, 1)
    client.year_by_year_hitting(682829, 11)
    assert len(api.calls) == 2


def test_expired_cache_entries_are_refetched(cache, fake, monkeypatch):
    from app.services.mlb_client import MLBClient

    cache.ttl_seconds = 0
    c = MLBClient(cache=cache, timeout=1.0, max_retries=1, backoff=0.0)
    api = fake(FakeAPI({"people": lambda p: people_payload([split("2024", 1)])}))
    c.year_by_year_hitting(1, 1)
    c.year_by_year_hitting(1, 1)
    assert len(api.calls) == 2


# ----------------------------------------------------------------------
# bulk helpers (Compare tab)
# ----------------------------------------------------------------------
def test_bulk_levels_hitting_covers_every_player_and_level(client, fake):
    api = fake(FakeAPI({"people": lambda p: people_payload([split("2024", 1)])}))
    payloads, failed = client.bulk_levels_hitting([1, 2, 3], [1, 11])
    assert set(payloads) == {1, 2, 3}
    assert all(set(v) == {1, 11} for v in payloads.values())
    assert failed == []
    assert len(api.calls) == 6


def test_bulk_levels_hitting_never_exceeds_the_concurrency_cap(client, fake):
    import threading

    in_flight = 0
    peak = 0
    lock = threading.Lock()

    def responder(params):
        nonlocal in_flight, peak
        with lock:
            in_flight += 1
            peak = max(peak, in_flight)
        time.sleep(0.02)
        with lock:
            in_flight -= 1
        return people_payload([split("2024", 1)])

    fake(FakeAPI({"people": responder}))
    client.bulk_levels_hitting(list(range(10)), [1, 11])
    assert peak <= client.max_concurrency


def test_bulk_levels_hitting_reports_partial_failures(client, fake):
    def responder(params):
        if str(params.get("personIds")) == "2":
            raise ConnectionError("boom")
        return people_payload([split("2024", 1)])

    fake(FakeAPI({"people": responder}))
    payloads, failed = client.bulk_levels_hitting([1, 2], [1, 11])
    assert payloads[1] and payloads[2] == {}
    assert sorted(failed) == [(2, 1), (2, 11)]


def test_bulk_find_players_dedupes_queries(client, fake):
    api = fake(FakeAPI({"people_search_ext": PERSON_ONLY}))
    results, failed = client.bulk_find_players(["Elly De La Cruz", "Elly De La Cruz", "  "])
    assert list(results) == ["Elly De La Cruz"]
    assert failed == []
    assert len(api.calls) == 1
