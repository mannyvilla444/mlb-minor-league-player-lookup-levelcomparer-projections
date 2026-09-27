"""Number formatting shared by the Jinja filters and the SVG box plots.

Two families of units live on the Compare tab and they must never be confused:

* **slash stats** (OPS, SLG) are rates on a 0-1-ish scale, shown baseball-style
  with the leading zero dropped: ``.789``, and a difference as ``-.031``.
* **rate stats** (BB%, K%) are stored as fractions but *differenced in
  percentage points*: 22.0% against 18.0% is ``+4.0`` pp, never ``+22%``.
"""

from __future__ import annotations

from typing import Optional

DASH = "—"


def fmt_rate(value: Optional[float]) -> str:
    """0.0873 -> '8.7%'."""
    return DASH if value is None else f"{value * 100:.1f}%"


def fmt_slash(value: Optional[float]) -> str:
    """0.789 -> '.789'; 1.021 keeps its leading 1."""
    if value is None:
        return DASH
    text = f"{value:.3f}"
    return text[1:] if text.startswith("0.") else text


def fmt_int(value: Optional[int]) -> str:
    return DASH if value is None else f"{value:,}"


def fmt_delta_slash(value: Optional[float]) -> str:
    """A signed OPS/SLG difference: '-.031', '+.014'."""
    if value is None:
        return DASH
    sign = "-" if value < 0 else "+"
    text = f"{abs(value):.3f}"
    return sign + (text[1:] if text.startswith("0.") else text)


def fmt_delta_pp(value: Optional[float]) -> str:
    """A signed rate difference in percentage points: '-2.4', '+0.9'."""
    return DASH if value is None else f"{value * 100:+.1f}"


def fmt_delta(value: Optional[float], kind: str) -> str:
    """Signed difference in the units of `kind` ('slash' or 'rate')."""
    return fmt_delta_slash(value) if kind == "slash" else fmt_delta_pp(value)


def fmt_spread(value: Optional[float], kind: str) -> str:
    """An unsigned magnitude — SD, SE, IQR — in the units of `kind`.

    Spreads are never negative, so they carry no sign; a '+' in front of an SD
    would imply a direction it does not have.
    """
    if value is None:
        return DASH
    if kind == "slash":
        text = f"{value:.3f}"
        return text[1:] if text.startswith("0.") else text
    return f"{value * 100:.1f}"


def fmt_axis(value: float, kind: str) -> str:
    """Compact axis tick text. Zero is always a bare '0' — it is the anchor.

    Precision follows the value: a 2.5-percentage-point tick must not print as
    "2", and a .025 tick must not print as ".03". A tick that lies about its own
    position is worse than no tick.
    """
    if abs(value) < 1e-12:
        return "0"
    if kind == "slash":
        sign = "-" if value < 0 else ""
        text = f"{abs(value):.3f}"
        if text.endswith("0"):  # .050 -> .05, .300 -> .30
            text = text[:-1]
        return sign + (text[1:] if text.startswith("0.") else text)
    points = value * 100
    return f"{points:.0f}" if abs(points - round(points)) < 1e-9 else f"{points:.1f}"


def unit_label(kind: str) -> str:
    return "points" if kind == "slash" else "percentage points"
