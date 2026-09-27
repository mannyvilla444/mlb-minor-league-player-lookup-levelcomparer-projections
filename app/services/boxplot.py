"""Server-rendered inline SVG box plots. No JavaScript, no chart library.

One panel per metric, each with **its own x-axis**. That is deliberate: OPS and
SLG differences are in points (~0.1 scale) while BB% and K% are in percentage
points (~5 scale), and putting two scales on one plot invents a comparison that
is not in the data. Small multiples are the honest form.

Anatomy of a panel, left to right along one horizontal axis:

* thin whisker line spanning the Tukey whiskers (most extreme point within
  1.5 x IQR of the box);
* a washed, outlined box from Q1 to Q3 — the outline is what makes its two
  vertical edges legible *as* the quartiles;
* a heavier line at the median, overhanging the box top and bottom so it can
  never be mistaken for a third box edge;
* a rust diamond at the mean, ringed in the surface colour so it stays legible
  where it lands on the median;
* every individual player as a faint dot on a strip below, because a box plot
  over six players is otherwise easy to over-read;
* a hairline at zero — no difference between the levels — since that is the
  reference the whole question is asked against.

Colours are the app's blue and rust, verified against the dataviz palette checks
(lightness band, chroma floor, CVD separation, contrast) on a white surface.
Every mark also carries a native SVG ``<title>`` so hovering reads out the exact
value; nothing is gated behind the hover, since the same numbers sit in the
summary table directly above.
"""

from __future__ import annotations

import math
from typing import Optional

from markupsafe import Markup, escape

from app.services.distribution import Distribution
from app.services.formatting import fmt_axis, fmt_delta, fmt_spread

# ---------------------------------------------------------------------------
# geometry (SVG user units; the panel scales to its container via viewBox)
# ---------------------------------------------------------------------------
VIEW_W = 480
VIEW_H = 88
PLOT_LEFT = 10.0
PLOT_RIGHT = 470.0

CENTRE_Y = 28.0
BOX_HALF = 11.0
WHISKER_CAP_HALF = 7.0
MEDIAN_OVERHANG = 5.5
STRIP_Y = 49.0
AXIS_Y = 64.0
TICK_TEXT_Y = 78.0
ZERO_TOP = 11.0
ZERO_BOTTOM = 55.0

DOMAIN_PAD = 0.08

# ---------------------------------------------------------------------------
# palette — validated: all six checks pass on #FFFFFF
# ---------------------------------------------------------------------------
BLUE = "#2F6EA8"
RUST = "#B1442B"
INK_FAINT = "#8B95A3"
RULE = "#D9D7D1"
SURFACE = "#FFFFFF"


def nice_ticks(low: float, high: float, target: int = 6) -> list[float]:
    """Clean, evenly spaced tick values covering [low, high].

    Steps are 1/2/2.5/5 x a power of ten, so zero always lands on a tick
    whenever the domain straddles it.
    """
    span = high - low
    if span <= 0 or not math.isfinite(span):
        return [low]
    raw_step = span / max(1, target)
    magnitude = 10.0 ** math.floor(math.log10(raw_step))
    for multiple in (1, 2, 2.5, 5, 10):
        step = magnitude * multiple
        if raw_step <= step:
            break
    first = math.ceil(low / step) * step
    ticks: list[float] = []
    value = first
    # guard against fp drift accumulating past the right edge
    while value <= high + step * 1e-9 and len(ticks) < 12:
        ticks.append(0.0 if abs(value) < step * 1e-9 else value)
        value += step
    return ticks


def _domain(dist: Distribution) -> tuple[float, float]:
    """Value range to draw, always including zero, padded for breathing room."""
    points = list(dist.values) + [0.0]
    for extra in (dist.whisker_low, dist.whisker_high, dist.q1, dist.q3, dist.mean):
        if extra is not None:
            points.append(extra)
    low, high = min(points), max(points)
    if high - low < 1e-12:  # every player identical, and equal to zero
        pad = abs(low) * 0.5 or 0.01
        return low - pad, high + pad
    pad = (high - low) * DOMAIN_PAD
    return low - pad, high + pad


def svg_boxplot(dist: Distribution, kind: str, label: str = "") -> Markup:
    """Render one metric's distribution as a standalone inline SVG panel."""
    if not dist.has_box:
        return Markup("")

    low, high = _domain(dist)
    span = high - low

    def x_of(value: float) -> float:
        return PLOT_LEFT + (value - low) / span * (PLOT_RIGHT - PLOT_LEFT)

    def val(value: Optional[float]) -> str:
        return fmt_delta(value, kind)

    parts: list[str] = [
        f'<svg class="boxplot" viewBox="0 0 {VIEW_W} {VIEW_H}" '
        f'role="img" preserveAspectRatio="xMidYMid meet" '
        f'aria-label="{escape(label)} distribution: median {val(dist.median)}, '
        f'mean {val(dist.mean)}, Q1 {val(dist.q1)}, Q3 {val(dist.q3)}, '
        f'n {dist.n}">'
    ]

    # --- axis: solid hairline, recessive -----------------------------------
    parts.append(
        f'<line x1="{PLOT_LEFT:.1f}" y1="{AXIS_Y}" x2="{PLOT_RIGHT:.1f}" y2="{AXIS_Y}" '
        f'stroke="{RULE}" stroke-width="1"/>'
    )
    for tick in nice_ticks(low, high):
        tx = x_of(tick)
        is_zero = abs(tick) < 1e-12
        parts.append(
            f'<line x1="{tx:.1f}" y1="{AXIS_Y}" x2="{tx:.1f}" y2="{AXIS_Y + 4}" '
            f'stroke="{RULE}" stroke-width="1"/>'
        )
        parts.append(
            f'<text x="{tx:.1f}" y="{TICK_TEXT_Y}" text-anchor="middle" '
            f'class="bp-tick{" bp-tick-zero" if is_zero else ""}">'
            f"{escape(fmt_axis(tick, kind))}</text>"
        )

    # --- zero reference, drawn under the data ------------------------------
    if low <= 0.0 <= high:
        zx = x_of(0.0)
        parts.append(
            f'<line x1="{zx:.1f}" y1="{ZERO_TOP}" x2="{zx:.1f}" y2="{ZERO_BOTTOM}" '
            f'stroke="{INK_FAINT}" stroke-width="1"/>'
            f"<title>No difference between the two levels</title>"
        )

    # --- whiskers -----------------------------------------------------------
    wl, wh = x_of(dist.whisker_low), x_of(dist.whisker_high)
    parts.append(
        f'<g><line x1="{wl:.1f}" y1="{CENTRE_Y}" x2="{wh:.1f}" y2="{CENTRE_Y}" '
        f'stroke="{BLUE}" stroke-width="2" stroke-linecap="round"/>'
        f"<title>Whiskers {val(dist.whisker_low)} to {val(dist.whisker_high)} "
        f"(within 1.5 x IQR)</title></g>"
    )
    for cap_x in (wl, wh):
        parts.append(
            f'<line x1="{cap_x:.1f}" y1="{CENTRE_Y - WHISKER_CAP_HALF}" '
            f'x2="{cap_x:.1f}" y2="{CENTRE_Y + WHISKER_CAP_HALF}" '
            f'stroke="{BLUE}" stroke-width="2" stroke-linecap="round"/>'
        )

    # --- interquartile box: a wash, never a saturated block -----------------
    bx1, bx2 = x_of(dist.q1), x_of(dist.q3)
    parts.append(
        f'<g><rect x="{bx1:.1f}" y="{CENTRE_Y - BOX_HALF}" '
        f'width="{max(bx2 - bx1, 0.8):.1f}" height="{BOX_HALF * 2}" rx="1.5" '
        f'fill="{BLUE}" fill-opacity="0.13" stroke="{BLUE}" stroke-width="1.25" '
        f'stroke-opacity="0.85"/>'
        f"<title>Middle 50%: Q1 {val(dist.q1)} to Q3 {val(dist.q3)}, "
        f"IQR {fmt_spread(dist.iqr, kind)}</title></g>"
    )
    # Invisible wide hit strips so hovering a quartile edge is not pixel-hunting.
    for edge_x, name, edge_value in ((bx1, "Q1", dist.q1), (bx2, "Q3", dist.q3)):
        parts.append(
            f'<g><rect x="{edge_x - 5:.1f}" y="{CENTRE_Y - BOX_HALF}" width="10" '
            f'height="{BOX_HALF * 2}" fill="transparent"/>'
            f"<title>{name} {val(edge_value)}</title></g>"
        )

    # --- individual players, so a tiny n cannot masquerade as a smooth box --
    for value in dist.values:
        parts.append(
            f'<circle cx="{x_of(value):.1f}" cy="{STRIP_Y}" r="1.9" '
            f'fill="{BLUE}" fill-opacity="0.5"/>'
        )

    # --- median: heavy, overhanging the box --------------------------------
    mx = x_of(dist.median)
    parts.append(
        f'<g><line x1="{mx:.1f}" y1="{CENTRE_Y - BOX_HALF - MEDIAN_OVERHANG}" '
        f'x2="{mx:.1f}" y2="{CENTRE_Y + BOX_HALF + MEDIAN_OVERHANG}" '
        f'stroke="{SURFACE}" stroke-width="6"/>'
        f'<line x1="{mx:.1f}" y1="{CENTRE_Y - BOX_HALF - MEDIAN_OVERHANG}" '
        f'x2="{mx:.1f}" y2="{CENTRE_Y + BOX_HALF + MEDIAN_OVERHANG}" '
        f'stroke="{BLUE}" stroke-width="3.25"/>'
        f"<title>Median {val(dist.median)}</title></g>"
    )

    # --- mean: rust diamond, ringed so it survives landing on the median ----
    ax = x_of(dist.mean)
    parts.append(
        f'<g>{_diamond(ax, CENTRE_Y, 6.5, SURFACE, 2.0, RUST)}'
        f"<title>Mean {val(dist.mean)}"
        + (f" ± {fmt_spread(dist.se_mean, kind)} SE" if dist.se_mean is not None else "")
        + "</title></g>"
    )

    # --- outliers: kept, never dropped --------------------------------------
    for value in dist.outliers:
        ox = x_of(value)
        parts.append(
            f'<g><circle cx="{ox:.1f}" cy="{CENTRE_Y}" r="3.6" fill="{SURFACE}"/>'
            f'<circle cx="{ox:.1f}" cy="{CENTRE_Y}" r="2.4" fill="none" '
            f'stroke="{BLUE}" stroke-width="1.5"/>'
            f"<title>Outlier {val(value)} (beyond 1.5 x IQR)</title></g>"
        )

    parts.append("</svg>")
    return Markup("".join(parts))


def _diamond(
    cx: float, cy: float, radius: float, ring: str, ring_width: float, fill: str
) -> str:
    points = f"{cx:.1f},{cy - radius:.1f} {cx + radius:.1f},{cy:.1f} {cx:.1f},{cy + radius:.1f} {cx - radius:.1f},{cy:.1f}"
    return (
        f'<polygon points="{points}" fill="{fill}" '
        f'stroke="{ring}" stroke-width="{ring_width}" stroke-linejoin="round"/>'
        f'<polygon points="{points}" fill="{fill}"/>'
    )
