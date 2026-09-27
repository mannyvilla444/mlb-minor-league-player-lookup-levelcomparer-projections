"""The Python delta table and the static HTML page must never drift apart.

The Translations tab is a document; `app/services/translations.py` is what the
Projection tab computes with. Two copies of the same eight rows is exactly the
setup where one gets edited and the other does not — so this parses the numbers
back out of the HTML and compares them.
"""

from __future__ import annotations

import json
import re

import pytest

from app.config import TRANSLATIONS_PATH
from app.services.translations import (
    TRANSLATIONS,
    find_translation,
    normalise_league_name,
)


def _deltas_from_the_html_page() -> dict[str, dict[str, float]]:
    """Pull the `const data=[...]` array out of the published page."""
    text = TRANSLATIONS_PATH.read_text(encoding="utf-8")
    match = re.search(r"const\s+data\s*=\s*(\[.*?\]);", text, re.S)
    assert match, "could not find the data array in the translations HTML"

    raw = match.group(1)
    # JS object literals use bare keys and leading-dot floats; make it JSON.
    raw = re.sub(r"([{,])\s*(\w+)\s*:", r'\1"\2":', raw)
    raw = re.sub(r":\s*(-?)\.(\d)", r": \g<1>0.\2", raw)
    raw = raw.replace("'", '"')
    return {row["name"]: row for row in json.loads(raw)}


def test_every_python_league_appears_in_the_html_page():
    page = _deltas_from_the_html_page()
    assert len(page) == len(TRANSLATIONS) == 8
    for translation in TRANSLATIONS:
        assert translation.short in page, f"{translation.short} missing from the HTML"


@pytest.mark.parametrize("translation", TRANSLATIONS, ids=lambda t: t.key)
def test_the_numbers_match_the_published_page(translation):
    row = _deltas_from_the_html_page()[translation.short]
    assert translation.d_ops == pytest.approx(row["ops"])
    assert translation.d_slg == pytest.approx(row["slg"])
    assert translation.d_bb == pytest.approx(row["bb"])
    assert translation.d_k == pytest.approx(row["k"])


@pytest.mark.parametrize("translation", TRANSLATIONS, ids=lambda t: t.key)
def test_the_level_labels_match_the_published_page(translation):
    row = _deltas_from_the_html_page()[translation.short]
    expected = {"AAA": "AAA", "AA": "AA", "A+": "High-A"}[translation.level_code]
    assert row["level"] == expected


# ----------------------------------------------------------------------
# unit conventions — getting these wrong silently ruins every projection
# ----------------------------------------------------------------------
def test_rate_deltas_convert_from_percentage_points_to_fractions():
    pacific = find_translation("Pacific Coast League")
    assert pacific.d_bb == pytest.approx(-3.3)  # as published, in pp
    assert pacific.d_bb_fraction == pytest.approx(-0.033)  # as used internally
    assert pacific.delta("bb") == pytest.approx(-0.033)


def test_slash_deltas_are_used_as_published():
    pacific = find_translation("Pacific Coast League")
    assert pacific.delta("ops") == pytest.approx(-0.202)
    assert pacific.delta("slg") == pytest.approx(-0.127)


# ----------------------------------------------------------------------
# league name matching
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "api_name,expected_key",
    [
        ("International League", "international"),
        ("Pacific Coast League", "pacific-coast"),
        ("Eastern League", "eastern"),
        ("Southern League", "southern"),
        ("Texas League", "texas"),
        ("Midwest League", "midwest"),
        ("Northwest League", "northwest"),
        ("South Atlantic League", "south-atlantic"),
    ],
)
def test_current_league_names_resolve(api_name, expected_key):
    assert find_translation(api_name).key == expected_key


@pytest.mark.parametrize(
    "api_name,expected_key",
    [
        ("Triple-A East", "international"),
        ("Triple-A West", "pacific-coast"),
        ("Double-A Northeast", "eastern"),
        ("Double-A South", "southern"),
        ("Double-A Central", "texas"),
        ("High-A East", "south-atlantic"),
        ("High-A Central", "midwest"),
        ("High-A West", "northwest"),
    ],
)
def test_the_2021_placeholder_names_resolve_too(api_name, expected_key):
    """MiLB ran 2021 under these names before the historic ones returned."""
    assert find_translation(api_name).key == expected_key


def test_matching_ignores_case_punctuation_and_the_word_league():
    for spelling in ("pacific coast", "PACIFIC COAST LEAGUE", "Pacific-Coast", "  Pacific Coast  "):
        assert find_translation(spelling).key == "pacific-coast"


def test_an_unknown_league_returns_none_rather_than_a_guess():
    for unknown in ("Florida State League", "California League", "Nippon Professional Baseball", "", None):
        assert find_translation(unknown) is None


def test_normalisation_strips_the_word_league_and_punctuation():
    assert normalise_league_name("Triple-A East") == "triple a east"
    assert normalise_league_name("South Atlantic League") == "south atlantic"


def test_the_eight_leagues_cover_exactly_aaa_aa_and_high_a():
    by_level: dict[str, int] = {}
    for t in TRANSLATIONS:
        by_level[t.level_code] = by_level.get(t.level_code, 0) + 1
    assert by_level == {"AAA": 2, "AA": 3, "A+": 3}
