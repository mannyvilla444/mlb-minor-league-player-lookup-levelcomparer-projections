"""Tests for the inline SVG box plots."""

from __future__ import annotations

import re

import pytest

from app.services.boxplot import nice_ticks, svg_boxplot
from app.services.distribution import describe

SAMPLE = describe([-0.31, -0.24, -0.20, -0.16, -0.11, -0.09, -0.04, 0.02])


# ----------------------------------------------------------------------
# tick generation
# ----------------------------------------------------------------------
def test_ticks_land_on_clean_round_numbers():
    ticks = nice_ticks(0.0, 10.0, target=4)
    assert all(abs(t / 2.5 - round(t / 2.5)) < 1e-9 for t in ticks)


def test_zero_is_always_a_tick_when_the_domain_straddles_it():
    ticks = nice_ticks(-0.35, 0.12)
    assert any(abs(t) < 1e-12 for t in ticks)


def test_ticks_stay_inside_the_domain():
    low, high = -0.4, 0.25
    for tick in nice_ticks(low, high):
        assert low - 1e-9 <= tick <= high + 1e-9


def test_a_degenerate_domain_does_not_loop_forever():
    assert nice_ticks(5.0, 5.0) == [5.0]
    assert len(nice_ticks(0.0, 1e-300)) <= 12


# ----------------------------------------------------------------------
# rendered output
# ----------------------------------------------------------------------
def test_every_requested_mark_is_present():
    svg = str(svg_boxplot(SAMPLE, "slash", "Δ OPS"))
    assert svg.startswith("<svg")
    assert svg.endswith("</svg>")
    assert "<rect" in svg  # the Q1-Q3 box
    assert "<polygon" in svg  # the mean diamond
    assert "<circle" in svg  # the per-player strip
    assert svg.count("<line") >= 5  # whiskers, caps, quartile edges, median, axis


def test_q1_median_mean_and_q3_are_all_labelled_in_tooltips():
    svg = str(svg_boxplot(SAMPLE, "slash", "Δ OPS"))
    assert "<title>Q1 " in svg
    assert "<title>Q3 " in svg
    assert "<title>Median " in svg
    assert "<title>Mean " in svg


def test_the_accessible_label_carries_the_four_headline_numbers():
    svg = str(svg_boxplot(SAMPLE, "slash", "Δ OPS"))
    label = re.search(r'aria-label="([^"]+)"', svg).group(1)
    for word in ("median", "mean", "Q1", "Q3", "n "):
        assert word in label


def test_a_zero_reference_line_is_drawn_when_zero_is_in_range():
    svg = str(svg_boxplot(SAMPLE, "slash", "Δ OPS"))
    assert "No difference between the two levels" in svg


def test_zero_is_always_in_range_even_for_all_negative_data():
    """Zero is the anchor the whole question is asked against."""
    only_negative = describe([-0.40, -0.35, -0.31, -0.28, -0.25])
    svg = str(svg_boxplot(only_negative, "slash", "Δ OPS"))
    assert "No difference between the two levels" in svg


def test_outliers_are_drawn_and_named():
    with_outlier = describe([0.01, 0.02, 0.03, 0.04, 0.05, 0.90])
    svg = str(svg_boxplot(with_outlier, "slash", "Δ OPS"))
    assert "Outlier" in svg


def test_rate_metrics_render_in_percentage_points():
    rates = describe([-0.021, -0.013, -0.008, 0.004, 0.011])
    svg = str(svg_boxplot(rates, "rate", "Δ K%"))
    # -0.021 as a fraction is -2.1 percentage points
    assert "-2.1" in svg
    # No *value* carries a percent sign — a difference of rates is in pp. The
    # metric label ("Δ K%") and the phrase "Middle 50%" legitimately contain one,
    # so check the value-bearing marks and the axis ticks only.
    value_titles = [
        t
        for t in re.findall(r"<title>([^<]*)</title>", svg)
        if t.startswith(("Median", "Mean", "Q1", "Q3", "Whiskers", "Outlier"))
    ]
    ticks = re.findall(r'class="bp-tick[^"]*">([^<]*)<', svg)
    assert value_titles and ticks
    assert not any("%" in text for text in value_titles + ticks)


def test_slash_metrics_drop_the_leading_zero():
    svg = str(svg_boxplot(SAMPLE, "slash", "Δ OPS"))
    assert "-.31" in svg or "-.310" in svg


def test_one_player_renders_nothing_rather_than_a_misleading_box():
    assert str(svg_boxplot(describe([0.1]), "slash", "Δ OPS")) == ""
    assert str(svg_boxplot(describe([]), "slash", "Δ OPS")) == ""


def test_identical_values_still_render_without_dividing_by_zero():
    svg = str(svg_boxplot(describe([0.2] * 5), "slash", "Δ OPS"))
    assert svg.startswith("<svg")
    assert "nan" not in svg.lower()
    assert "inf" not in svg.lower()


def test_no_nan_or_inf_reaches_the_markup_for_any_shape_of_input():
    for values in (
        [0.0, 0.0],
        [-1.0, 1.0],
        [0.001, 0.002, 0.003],
        [-0.5, -0.5, -0.5, 2.0],
        list(range(40)),
    ):
        svg = str(svg_boxplot(describe(values), "slash", "x"))
        assert "nan" not in svg.lower()
        assert "infinity" not in svg.lower()
        assert "None" not in svg


def test_the_label_is_html_escaped():
    svg = str(svg_boxplot(SAMPLE, "slash", '<script>alert("x")</script>'))
    assert "<script>" not in svg
    assert "&lt;script&gt;" in svg


def test_mean_diamond_is_ringed_in_the_surface_colour():
    """So it stays readable where it lands on top of the median line."""
    svg = str(svg_boxplot(SAMPLE, "slash", "Δ OPS"))
    assert 'stroke="#FFFFFF"' in svg


def test_marks_stay_within_the_viewbox():
    svg = str(svg_boxplot(SAMPLE, "slash", "Δ OPS"))
    for x in re.findall(r'c?x1?="([-\d.]+)"', svg):
        assert -1.0 <= float(x) <= 481.0
