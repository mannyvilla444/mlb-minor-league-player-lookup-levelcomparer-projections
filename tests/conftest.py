from __future__ import annotations

import pytest

from app.services.cache import JSONCache
from app.services.mlb_client import MLBClient


@pytest.fixture()
def cache(tmp_path) -> JSONCache:
    return JSONCache(path=tmp_path / "test-cache.sqlite3", ttl_seconds=3600)


@pytest.fixture()
def client(cache) -> MLBClient:
    # No sleeping in tests; retries still exercised via max_retries.
    return MLBClient(cache=cache, timeout=1.0, max_retries=2, backoff=0.0, max_concurrency=2)
