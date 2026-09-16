"""Content-based table column widths (shared by `form autofit` and `write`).

One pure function so the two callers stay consistent: a per-column **min clamp**
(so the longest word in a column isn't cut) plus **√(content) damping** of the
remaining width. Total is preserved exactly.
"""

from __future__ import annotations

from math import sqrt

CHAR_W = 1000  # ~HWPUNIT per CJK glyph at 10pt (coarse)
MIN_FLOOR = 3000


def compute_column_widths(
    contents: list[float],
    longest: list[int],
    total: int,
    min_widths: dict[int, int] | None = None,
    *,
    char_w: int = CHAR_W,
    floor: int = MIN_FLOOR,
) -> list[int]:
    """Distribute *total* width over columns by content volume.

    ``contents[c]`` = amount of text in column c (chars); ``longest[c]`` = longest
    unbreakable word (chars) → its min width. ``min_widths`` pins specific columns.
    Returns integer widths summing exactly to *total*.
    """
    ncols = len(contents)
    if ncols == 0:
        return []
    pins = min_widths or {}
    col_min = [
        min(pins.get(c, max(floor, longest[c] * char_w)), total) for c in range(ncols)
    ]
    smin = sum(col_min)
    if smin > total:  # clamps don't fit — scale them down proportionally
        col_min = [int(m * total / smin) for m in col_min]
        smin = sum(col_min)
    remaining = total - smin
    weights = [sqrt(contents[c]) if contents[c] > 0 else 0.0 for c in range(ncols)]
    sw = sum(weights)
    new = [
        col_min[c] + (int(remaining * weights[c] / sw) if sw > 0 else remaining // ncols)
        for c in range(ncols)
    ]
    new[max(range(ncols), key=lambda c: new[c])] += total - sum(new)  # keep sum == total
    return new
