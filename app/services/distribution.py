"""Summary statistics for a set of per-player differentials.

One :class:`Distribution` describes one metric's column of deltas: centre
(mean, median), spread (SD), precision of the centre (standard errors), and the
five-number box-plot summary with Tukey whiskers and outliers.

Conventions, stated because they change the numbers:

* **SD is the sample standard deviation** (``ddof=1``) — the spread of players,
  undefined for n < 2.
* **SE of the mean** is ``SD / sqrt(n)`` — how precisely the mean is pinned down.
  This is what "standard deviation of the mean" means literally, and it is a
  different quantity from SD.
* **SE of the median** has no clean closed form without assuming normality, so
  it is **bootstrapped** (resample with replacement, take the median, repeat,
  report the SD of those medians). The generator is seeded, so the same data
  always produces the same figure — a page refresh never changes it.
* **Quartiles use linear interpolation** (numpy's default, type 7). This matches
  Excel's ``QUARTILE.INC`` and R's ``type=7``, so cross-checks agree.
* **Whiskers are Tukey's**: the most extreme observation still within
  1.5 x IQR of the box. Anything past that is listed as an outlier — it is not
  discarded, and it still counts toward the mean, median and SD.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Sequence

import numpy as np

BOOTSTRAP_SAMPLES = 2000
BOOTSTRAP_SEED = 20260912  # fixed: the median's SE must not jitter between reloads
WHISKER_IQR_MULTIPLIER = 1.5


@dataclass(frozen=True)
class Distribution:
    """Everything the summary rows and the box plot need for one metric."""

    n: int
    values: list[float] = field(default_factory=list)  # sorted
    mean: Optional[float] = None
    median: Optional[float] = None
    sd: Optional[float] = None
    se_mean: Optional[float] = None
    se_median: Optional[float] = None
    q1: Optional[float] = None
    q3: Optional[float] = None
    iqr: Optional[float] = None
    whisker_low: Optional[float] = None
    whisker_high: Optional[float] = None
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    outliers: list[float] = field(default_factory=list)

    @property
    def has_data(self) -> bool:
        return self.n > 0

    @property
    def has_box(self) -> bool:
        """A box plot needs at least a quartile range to draw."""
        return self.n >= 2 and self.q1 is not None and self.q3 is not None


EMPTY = Distribution(n=0)


def _opt(value: float) -> Optional[float]:
    """Turn numpy's NaN into None so templates render an em dash, not 'nan'."""
    return None if value is None or not np.isfinite(value) else float(value)


def describe(
    raw_values: Sequence[Optional[float]],
    bootstrap_samples: int = BOOTSTRAP_SAMPLES,
    seed: int = BOOTSTRAP_SEED,
) -> Distribution:
    """Summarise one metric's deltas. ``None`` entries are dropped first."""
    values = np.array(
        [float(v) for v in raw_values if v is not None and np.isfinite(float(v))],
        dtype=float,
    )
    n = int(values.size)
    if n == 0:
        return EMPTY

    values.sort()
    mean = float(values.mean())
    median = float(np.median(values))

    # numpy's std with ddof=1 is NaN at n == 1, which is the honest answer.
    sd = _opt(float(values.std(ddof=1))) if n >= 2 else None
    se_mean = _opt(sd / np.sqrt(n)) if sd is not None else None
    se_median = _bootstrap_se_median(values, bootstrap_samples, seed) if n >= 2 else None

    q1 = float(np.percentile(values, 25))
    q3 = float(np.percentile(values, 75))
    iqr = q3 - q1

    reach = WHISKER_IQR_MULTIPLIER * iqr
    low_fence, high_fence = q1 - reach, q3 + reach
    inside = values[(values >= low_fence) & (values <= high_fence)]
    # `inside` is never empty: Q1 and Q3 themselves always sit within the fences.
    whisker_low = float(inside.min())
    whisker_high = float(inside.max())
    outliers = [float(v) for v in values if v < low_fence or v > high_fence]

    return Distribution(
        n=n,
        values=[float(v) for v in values],
        mean=mean,
        median=median,
        sd=sd,
        se_mean=se_mean,
        se_median=se_median,
        q1=q1,
        q3=q3,
        iqr=iqr,
        whisker_low=whisker_low,
        whisker_high=whisker_high,
        minimum=float(values[0]),
        maximum=float(values[-1]),
        outliers=outliers,
    )


def _bootstrap_se_median(
    values: np.ndarray, samples: int, seed: int
) -> Optional[float]:
    """SD of the median across `samples` resamples. Distribution-free."""
    if samples < 2:
        return None
    rng = np.random.default_rng(seed)
    draws = rng.choice(values, size=(samples, values.size), replace=True)
    medians = np.median(draws, axis=1)
    return _opt(float(medians.std(ddof=1)))
