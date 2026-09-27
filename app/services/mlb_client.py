"""A thin, cached, retrying wrapper around the official MLB Stats API.

Everything goes through :func:`statsapi.get` from the ``MLB-StatsAPI`` package —
we never scrape FanGraphs, Baseball-Reference or anything else.

Two endpoints are used:

``sports_players``
    ``/api/v1/sports/players?sportId=..&season=..`` — the roster endpoint that
    ``statsapi.lookup_player`` itself calls. We call it directly (and reuse the
    same substring matching) so that we can attach a timeout, retry with
    backoff, and cache the JSON. ``statsapi.lookup_player`` accepts neither a
    timeout nor a cache.

``people``
    ``/api/v1/people?personIds=..&hydrate=stats(group=[hitting],type=[yearByYear],sportId=N)``
    — one call per sportId, joined afterwards on the MLBAM person id.

An optional first attempt is made against ``/api/v1/people/search``, which finds
retired players that no current roster contains. If that endpoint is not
available the client falls back to the roster scan without complaining.
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from typing import Any, Iterable, Optional

import statsapi

from app.config import (
    LEVEL_BY_SPORT_ID,
    MAX_CONCURRENT_REQUESTS,
    MAX_RETRIES,
    REQUEST_TIMEOUT_SECONDS,
    RETRY_BACKOFF_SECONDS,
    SPORT_IDS,
)
from app.services.cache import JSONCache
from app.services.models import MLBAPIError, PlayerMatch

logger = logging.getLogger(__name__)

_PERSON_FIELDS = (
    "people,id,fullName,firstName,lastName,useName,nickName,nameSlug,"
    "primaryNumber,currentTeam,name,primaryPosition,abbreviation,mlbDebutDate"
)

# Register the free-text search endpoint on the statsapi endpoint table so the
# call still flows through statsapi.get (and therefore our single HTTP path).
_SEARCH_ENDPOINT = "people_search_ext"
if _SEARCH_ENDPOINT not in statsapi.ENDPOINTS:
    statsapi.ENDPOINTS[_SEARCH_ENDPOINT] = {
        "url": "https://statsapi.mlb.com/api/{ver}/people/search",
        "path_params": {
            "ver": {
                "type": "str",
                "default": "v1",
                "leading_slash": False,
                "trailing_slash": False,
                "required": True,
            }
        },
        "query_params": ["names", "sportIds", "activeStatus", "limit", "fields", "hydrate"],
        "required_params": [[]],
    }


class MLBClient:
    """Fetches player identity and year-by-year hitting stats by level."""

    def __init__(
        self,
        cache: Optional[JSONCache] = None,
        timeout: float = REQUEST_TIMEOUT_SECONDS,
        max_retries: int = MAX_RETRIES,
        backoff: float = RETRY_BACKOFF_SECONDS,
        max_concurrency: int = MAX_CONCURRENT_REQUESTS,
    ) -> None:
        self.cache = cache if cache is not None else JSONCache()
        self.timeout = timeout
        self.max_retries = max(1, max_retries)
        self.backoff = backoff
        self.max_concurrency = max(1, max_concurrency)
        self._search_endpoint_ok = True

    # ------------------------------------------------------------------
    # HTTP plumbing
    # ------------------------------------------------------------------
    def _request(self, endpoint: str, params: dict[str, Any]) -> Any:
        """Call statsapi.get with a timeout and bounded retries."""
        last_error: Optional[Exception] = None
        for attempt in range(self.max_retries):
            try:
                return statsapi.get(
                    endpoint, params, request_kwargs={"timeout": self.timeout}
                )
            except Exception as exc:  # noqa: BLE001 - statsapi raises bare ValueError
                last_error = exc
                if attempt < self.max_retries - 1:
                    sleep_for = self.backoff * (2**attempt)
                    logger.warning(
                        "MLB API %s failed (attempt %s/%s): %s — retrying in %.2fs",
                        endpoint,
                        attempt + 1,
                        self.max_retries,
                        exc,
                        sleep_for,
                    )
                    time.sleep(sleep_for)
        raise MLBAPIError(
            f"MLB Stats API request to '{endpoint}' failed after "
            f"{self.max_retries} attempts: {last_error}"
        ) from last_error

    def _cached_request(self, cache_key: str, endpoint: str, params: dict[str, Any]) -> Any:
        cached = self.cache.get(cache_key)
        if cached is not None:
            return cached
        payload = self._request(endpoint, params)
        self.cache.set(cache_key, payload)
        return payload

    # ------------------------------------------------------------------
    # Player identity
    # ------------------------------------------------------------------
    def find_players(self, query: str, season: Optional[int] = None) -> list[PlayerMatch]:
        """Resolve a typed name (or a raw MLBAM id) to candidate players."""
        query = (query or "").strip()
        if not query:
            return []

        if query.isdigit():
            person = self.get_person(int(query))
            return [person] if person else []

        matches = self._search_by_name_endpoint(query)
        if matches:
            return matches
        return self._search_by_roster_scan(query, season)

    def _search_by_name_endpoint(self, query: str) -> list[PlayerMatch]:
        """Best-effort free-text search; returns [] if the endpoint is unusable."""
        if not self._search_endpoint_ok:
            return []
        cache_key = f"search:{query.lower()}"
        try:
            payload = self._cached_request(
                cache_key,
                _SEARCH_ENDPOINT,
                {"names": query, "limit": 25, "fields": _PERSON_FIELDS},
            )
        except MLBAPIError:
            logger.info("people/search unavailable; falling back to roster scan")
            self._search_endpoint_ok = False
            return []
        return _people_to_matches(payload.get("people", []) if isinstance(payload, dict) else [])

    def _search_by_roster_scan(self, query: str, season: Optional[int]) -> list[PlayerMatch]:
        """Scan each level's season roster, the way statsapi.lookup_player does."""
        seasons: list[int] = [season] if season else [date.today().year, date.today().year - 1]
        for yr in seasons:
            found: dict[int, PlayerMatch] = {}
            for payload in self._fetch_rosters(yr):
                people = payload.get("people", []) if isinstance(payload, dict) else []
                for match in _people_to_matches(_filter_people(people, query)):
                    found.setdefault(match.person_id, match)
            if found:
                return sorted(found.values(), key=lambda m: m.full_name)
        return []

    def _fetch_rosters(self, season: int) -> list[dict[str, Any]]:
        def one(sport_id: int) -> dict[str, Any]:
            return self._cached_request(
                f"roster:{sport_id}:{season}",
                "sports_players",
                {"sportId": sport_id, "season": season, "fields": _PERSON_FIELDS},
            )

        payloads: list[dict[str, Any]] = []
        errors: list[Exception] = []
        with ThreadPoolExecutor(max_workers=self.max_concurrency) as pool:
            for sport_id, result in zip(SPORT_IDS, pool.map(_safe(one, errors), SPORT_IDS)):
                if result is not None:
                    payloads.append(result)
                else:
                    logger.warning("roster fetch failed for sportId=%s", sport_id)
        if not payloads and errors:
            raise MLBAPIError(f"Could not reach the MLB Stats API: {errors[0]}")
        return payloads

    def get_person(self, person_id: int) -> Optional[PlayerMatch]:
        payload = self._cached_request(
            f"person:{person_id}",
            "people",
            {"personIds": person_id, "fields": _PERSON_FIELDS},
        )
        people = payload.get("people", []) if isinstance(payload, dict) else []
        matches = _people_to_matches(people)
        return matches[0] if matches else None

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------
    def year_by_year_hitting(self, person_id: int, sport_id: int) -> dict[str, Any]:
        """Raw ``people`` payload hydrated with year-by-year hitting for one level."""
        hydrate = f"stats(group=[hitting],type=[yearByYear],sportId={sport_id})"
        return self._cached_request(
            f"people:{person_id}:{sport_id}",
            "people",
            {"personIds": person_id, "hydrate": hydrate},
        )

    def all_levels_hitting(
        self, person_id: int, sport_ids: Iterable[int] = SPORT_IDS
    ) -> tuple[dict[int, dict[str, Any]], list[int]]:
        """Fetch every level for one player.

        Returns ``(payload_by_sport_id, failed_sport_ids)``. Requests are capped
        at ``MAX_CONCURRENT_REQUESTS`` in flight so we never flood the API.
        """
        sport_ids = list(sport_ids)
        payloads: dict[int, dict[str, Any]] = {}
        failed: list[int] = []
        errors: list[Exception] = []

        def one(sport_id: int) -> dict[str, Any]:
            return self.year_by_year_hitting(person_id, sport_id)

        with ThreadPoolExecutor(max_workers=self.max_concurrency) as pool:
            for sport_id, result in zip(sport_ids, pool.map(_safe(one, errors), sport_ids)):
                if result is None:
                    failed.append(sport_id)
                else:
                    payloads[sport_id] = result

        if not payloads:
            level_names = ", ".join(
                LEVEL_BY_SPORT_ID[s].code for s in failed if s in LEVEL_BY_SPORT_ID
            )
            raise MLBAPIError(
                f"Every level request failed ({level_names}). "
                f"Last error: {errors[0] if errors else 'unknown'}"
            )
        return payloads, failed

    # ------------------------------------------------------------------
    # Bulk helpers for the Compare tab
    # ------------------------------------------------------------------
    def bulk_find_players(
        self, queries: Iterable[str]
    ) -> tuple[dict[str, list[PlayerMatch]], list[str]]:
        """Resolve many names at once. Returns ``(matches_by_query, failed_queries)``.

        Every request still goes through the shared concurrency cap, so a
        60-name paste never turns into 60 simultaneous connections.
        """
        unique = list(dict.fromkeys(q.strip() for q in queries if q and q.strip()))
        results: dict[str, list[PlayerMatch]] = {}
        failed: list[str] = []
        errors: list[Exception] = []

        with ThreadPoolExecutor(max_workers=self.max_concurrency) as pool:
            for query, matches in zip(
                unique, pool.map(_safe(self.find_players, errors), unique)
            ):
                if matches is None:
                    failed.append(query)
                else:
                    results[query] = matches
        return results, failed

    def bulk_levels_hitting(
        self, person_ids: Iterable[int], sport_ids: Iterable[int]
    ) -> tuple[dict[int, dict[int, dict[str, Any]]], list[tuple[int, int]]]:
        """Fetch ``sport_ids`` for every person in one bounded pass.

        Flattening every (person, sportId) pair into a single pool keeps the
        concurrency cap honest — nesting one pool per player would multiply it.

        Returns ``({person_id: {sport_id: payload}}, [(person_id, sport_id), ...failed])``.
        """
        person_ids = list(dict.fromkeys(person_ids))
        sport_ids = list(sport_ids)
        jobs = [(pid, sid) for pid in person_ids for sid in sport_ids]
        if not jobs:
            return {}, []

        payloads: dict[int, dict[int, dict[str, Any]]] = {pid: {} for pid in person_ids}
        failed: list[tuple[int, int]] = []
        errors: list[Exception] = []

        def one(job: tuple[int, int]) -> dict[str, Any]:
            person_id, sport_id = job
            return self.year_by_year_hitting(person_id, sport_id)

        with ThreadPoolExecutor(max_workers=self.max_concurrency) as pool:
            for job, result in zip(jobs, pool.map(_safe(one, errors), jobs)):
                if result is None:
                    failed.append(job)
                else:
                    payloads[job[0]][job[1]] = result

        if not any(payloads.values()):
            raise MLBAPIError(
                "Every stat request failed. "
                f"Last error: {errors[0] if errors else 'unknown'}"
            )
        return payloads, failed


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------
def _safe(fn, errors: list[Exception]):
    """Wrap a worker so one failed level does not kill the whole lookup."""

    def inner(arg):
        try:
            return fn(arg)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
            logger.warning("MLB API sub-request failed: %s", exc)
            return None

    return inner


def _filter_people(people: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    """Substring match on every field, one term at a time (statsapi's rule)."""
    terms = str(query).lower().split()
    out: list[dict[str, Any]] = []
    for person in people:
        values = " ".join(str(v).lower() for v in person.values())
        if all(term in values for term in terms):
            out.append(person)
    return out


def _people_to_matches(people: list[dict[str, Any]]) -> list[PlayerMatch]:
    matches: list[PlayerMatch] = []
    for person in people:
        person_id = person.get("id")
        if person_id is None:
            continue
        position = (person.get("primaryPosition") or {}).get("abbreviation")
        team = (person.get("currentTeam") or {}).get("name")
        matches.append(
            PlayerMatch(
                person_id=int(person_id),
                full_name=person.get("fullName") or person.get("nameSlug") or str(person_id),
                position=position,
                current_team=team,
                mlb_debut_date=person.get("mlbDebutDate"),
            )
        )
    return matches
