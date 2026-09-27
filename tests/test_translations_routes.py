"""Tests for the Translations tab.

The page itself is an author-supplied static document. The contract this tab has
to hold is narrow but real: the file is served **unmodified**, the app's
navigation survives around it, and a missing file fails loudly rather than
rendering an empty frame.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import main
from app.config import TRANSLATIONS_FILENAME, TRANSLATIONS_PATH, TRANSLATIONS_URL


@pytest.fixture()
def http(monkeypatch, cache):
    monkeypatch.setattr(main.client, "cache", cache)
    return TestClient(main.app)


# ----------------------------------------------------------------------
# the static document
# ----------------------------------------------------------------------
def test_the_document_ships_with_the_app():
    assert TRANSLATIONS_PATH.is_file()


def test_the_static_file_is_served_byte_for_byte(http):
    r = http.get(TRANSLATIONS_URL)
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert r.content == TRANSLATIONS_PATH.read_bytes()


def test_the_document_still_contains_its_league_data(http):
    """Guards against a truncated or mangled copy landing in static/."""
    text = TRANSLATIONS_PATH.read_text(encoding="utf-8")
    for league in (
        "International",
        "Pacific",
        "Southern",
        "Texas",
        "Eastern",
        "Northwest",
        "Midwest",
        "South Atlantic",
    ):
        assert league in text
    assert text.rstrip().endswith("</html>")


# ----------------------------------------------------------------------
# the tab
# ----------------------------------------------------------------------
def test_the_tab_renders_and_frames_the_document(http):
    r = http.get("/translations")
    assert r.status_code == 200
    assert TRANSLATIONS_URL in r.text
    assert "<iframe" in r.text


def test_the_tab_does_not_inline_the_document_body(http):
    """It must embed the file, not copy it — one source of truth."""
    page = http.get("/translations").text
    assert "From the minors" not in page  # that headline lives in the framed file
    assert "data-metric" not in page


def test_all_tabs_appear_on_every_page(http):
    for path in ("/", "/compare", "/projection", "/translations"):
        text = http.get(path).text
        for href in ('href="/"', 'href="/compare"', 'href="/projection"', 'href="/translations"'):
            assert href in text


@pytest.mark.parametrize(
    "path,expected",
    [
        ("/", "One player"),
        ("/compare", "Compare"),
        ("/projection", "Projection"),
        ("/translations", "Translations"),
    ],
)
def test_the_right_tab_is_marked_active(http, path, expected):
    text = http.get(path).text
    marker = text.index('aria-current="page"')
    # the label of the current tab follows its aria-current attribute
    assert expected in text[marker : marker + 120]


def test_only_one_tab_is_active_at_a_time(http):
    for path in ("/", "/compare", "/projection", "/translations"):
        assert http.get(path).text.count('aria-current="page"') == 1


def test_the_page_says_the_numbers_are_supplied_not_computed(http):
    """The tab must not read as app output — it is someone else's research."""
    text = http.get("/translations").text
    assert "supplied research values" in text.lower()
    assert "not" in text.lower()


def test_a_missing_document_fails_loudly(http, monkeypatch, tmp_path):
    monkeypatch.setattr(main, "TRANSLATIONS_PATH", tmp_path / "gone.html")
    r = http.get("/translations")
    assert r.status_code == 404
    assert TRANSLATIONS_FILENAME in r.text or "missing" in r.text.lower()


def test_healthz_reports_the_translations_tab(http):
    assert http.get("/healthz").json()["has_translations_tab"] == "yes"
