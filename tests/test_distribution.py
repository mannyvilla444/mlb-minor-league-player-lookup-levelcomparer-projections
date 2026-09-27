"""Tests for the summary statistics behind the Compare tab's new rows.

Expected values are hand-computed or taken from the documented convention
(linear-interpolation quartiles, Tukey whiskers), not from the implementation.
"""

from __future__ import annotations

import math

import pytest

from app.services.distribution import EMPTY, describe


# ----------------------------------------------------------------------
# centre and spread
# ----------------------------------------------------------------------
def test_mean_median_and_sample_sd():
    # 2, 4, 4, 4, 5, 5, 7, 9 — the textbook set.
    # mean 5; population SD 2, so the SAMPLE SD (ddof=1) is sqrt(32/7) = 2.1381
    d = describe([2, 4, 4, 4, 5, 5, 7, 9])
    assert d.n == 8
    assert d.mean == pytest.approx(5.0)
    assert d.median == pytest.approx(4.5)
    assert d.sd == pytest.approx(math.sqrt(32 / 7))


def test_se_of_mean_is_sd_over_root_n():
    d = describe([2, 4, 4, 4, 5, 5, 7, 9])
    assert d.se_mean == pytest.approx(d.sd / math.sqrt(8))
    # and it is strictly smaller than the SD, which is the point of reporting both
    assert d.se_mean < d.sd


def test_se_of_the_median_is_bootstrapped_and_positive():
    d = describe(list(range(1, 21)))
    assert d.se_median is not None
    assert d.se_median > 0


def test_the_bootstrap_is_seeded_so_a_reload_never_changes_the_number():
    values = [-0.31, -0.2, -0.14, -0.09, -0.02, 0.05, 0.11]
    first = describe(values).se_median
    second = describe(values).se_median
    assert first == second


def test_a_different_seed_gives_a_different_draw():
    values = [-0.31, -0.2, -0.14, -0.09, -0.02, 0.05, 0.11]
    assert describe(values, seed=1).se_median != describe(values, seed=2).se_median


# ----------------------------------------------------------------------
# quartiles, IQR, whiskers
# ----------------------------------------------------------------------
def test_quartiles_use_linear_interpolation_matching_excel_quartile_inc():
    # 1..10: QUARTILE.INC gives Q1 = 3.25, Q3 = 7.75
    d = describe(list(range(1, 11)))
    assert d.q1 == pytest.approx(3.25)
    assert d.q3 == pytest.approx(7.75)
    assert d.iqr == pytest.approx(4.5)


def test_iqr_is_q3_minus_q1():
    d = describe([10, 20, 30, 40, 50, 60])
    assert d.iqr == pytest.approx(d.q3 - d.q1)


def test_whiskers_stop_at_the_last_point_inside_the_fence():
    # 1..10 -> Q1 3.25, Q3 7.75, IQR 4.5, fences at -3.5 and 14.5
    # Every point is inside, so the whiskers are just min and max.
    d = describe(list(range(1, 11)))
    assert d.whisker_low == pytest.approx(1)
    assert d.whisker_high == pytest.approx(10)
    assert d.outliers == []


def test_a_far_point_becomes_an_outlier_and_the_whisker_pulls_back():
    values = [1, 2, 3, 4, 5, 6, 7, 8, 9, 100]
    d = describe(values)
    # Q1 3.25, Q3 8.75 -> IQR 5.5 -> upper fence 8.75 + 8.25 = 17.0
    assert d.outliers == [100.0]
    assert d.whisker_high == pytest.approx(9)
    assert d.maximum == pytest.approx(100)


def test_outliers_still_count_toward_mean_median_and_sd():
    """They are flagged for the eye, never dropped from the arithmetic."""
    values = [1, 2, 3, 4, 5, 6, 7, 8, 9, 100]
    d = describe(values)
    assert d.mean == pytest.approx(sum(values) / len(values))
    assert d.n == 10


def test_outliers_on_both_tails_are_both_caught():
    d = describe([-500, 1, 2, 3, 4, 5, 6, 7, 8, 900])
    assert -500.0 in d.outliers and 900.0 in d.outliers
    assert d.whisker_low > -500 and d.whisker_high < 900


# ----------------------------------------------------------------------
# degenerate cases — the ones that turn into "nan" on a page
# ----------------------------------------------------------------------
def test_no_values_gives_a_wholly_empty_distribution():
    d = describe([])
    assert d is EMPTY
    assert d.n == 0
    assert d.mean is None and d.median is None and d.sd is None
    assert d.q1 is None and d.iqr is None
    assert d.has_data is False and d.has_box is False


def test_one_value_has_a_centre_but_no_spread():
    """SD at n=1 is genuinely undefined — it must be None, never NaN."""
    d = describe([0.42])
    assert d.n == 1
    assert d.mean == pytest.approx(0.42)
    assert d.median == pytest.approx(0.42)
    assert d.sd is None
    assert d.se_mean is None
    assert d.se_median is None
    assert d.has_box is False  # nothing to draw


def test_two_values_are_enough_for_a_box():
    d = describe([0.1, 0.3])
    assert d.sd == pytest.approx(math.sqrt(0.02))
    assert d.q1 == pytest.approx(0.15)
    assert d.q3 == pytest.approx(0.25)
    assert d.has_box is True


def test_identical_values_give_zero_spread_and_no_outliers():
    d = describe([0.2] * 6)
    assert d.sd == pytest.approx(0.0)
    assert d.iqr == pytest.approx(0.0)
    assert d.se_median == pytest.approx(0.0)
    assert d.outliers == []
    assert d.whisker_low == pytest.approx(0.2)
    assert d.whisker_high == pytest.approx(0.2)


def test_none_and_nan_entries_are_dropped_before_anything_is_computed():
    d = describe([1.0, None, 2.0, float("nan"), 3.0, None])
    assert d.n == 3
    assert d.mean == pytest.approx(2.0)


def test_every_reported_number_is_finite_or_none():
    """Nothing may reach a template as NaN or inf."""
    for values in ([], [1.0], [1.0, 1.0], [0.0, 0.0, 0.0], [-1.0, 0.0, 1.0, 50.0]):
        d = describe(values)
        for name in (
            "mean", "median", "sd", "se_mean", "se_median",
            "q1", "q3", "iqr", "whisker_low", "whisker_high", "minimum", "maximum",
        ):
            value = getattr(d, name)
            assert value is None or math.isfinite(value), f"{name} on {values}"


def test_values_come_back_sorted_for_the_strip_plot():
    d = describe([0.3, -0.1, 0.2, -0.4])
    assert d.values == sorted(d.values)


def test_negative_only_data_behaves_normally():
    """The common real case: every player got worse moving up."""
    d = describe([-0.30, -0.22, -0.18, -0.11, -0.04])
    assert d.mean < 0 and d.median < 0
    assert d.q1 < d.q3
    assert d.iqr > 0
