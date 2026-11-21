# SPDX-License-Identifier: GPL-3.0-or-later
"""Tile budget policy: how many 768 px detail tiles DeepSeek-OCR-2 gets for a page.

Phase 0 (docs/phase0-results.md): the overview alone (0 tiles) halves OCR time and stays exact on sparse pages,
but dense tables need all 6 tiles, and small print can be misread without them (a W-2 label came out as
"Employer's" instead of "Employee's"). Records favour accuracy, so the policy is conservative: 0 tiles only for
pages that are clearly sparse, 6 otherwise. Thresholds were calibrated on the Phase 0 synthetic set only.
"""
from __future__ import annotations

from .tier0 import PageSignals

FULL = 6
OVERVIEW = 0

SPARSE_MAX_CHARS = 900
SPARSE_MAX_NUMERIC_LINES = 6
SPARSE_MIN_MEDIAN_HEIGHT = 0.014   # ~11 pt type on US Letter
SPARSE_MAX_SMALL_LINES = 1


def choose(sig: PageSignals | None) -> tuple[int, str]:
    """Return (tiles, reason)."""
    if sig is None:
        return FULL, "no tier-0 signals"
    if sig.numeric_lines > SPARSE_MAX_NUMERIC_LINES:
        return FULL, f"table-like ({sig.numeric_lines} number-heavy rows)"
    if sig.chars > SPARSE_MAX_CHARS:
        return FULL, f"dense ({sig.chars} chars)"
    if sig.small_lines > SPARSE_MAX_SMALL_LINES:
        return FULL, f"small print ({sig.small_lines} small lines)"
    if sig.median_height < SPARSE_MIN_MEDIAN_HEIGHT:
        return FULL, "small type"
    return OVERVIEW, "sparse page"


def parse(value, default: str = "auto") -> int | str:
    """Request field ``tiles``: "auto" or an int 0..6."""
    if value is None:
        value = default
    if isinstance(value, str) and value.strip().lower() == "auto":
        return "auto"
    n = int(value)
    if not 0 <= n <= FULL:
        raise ValueError(f"tiles must be 'auto' or 0..{FULL}, got {value!r}")
    return n
