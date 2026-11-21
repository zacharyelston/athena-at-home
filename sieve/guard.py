# SPDX-License-Identifier: GPL-3.0-or-later
# Derived from Athena@Home phase0/bench.py (loop detector).
"""Repetition-loop guard for DeepSeek-OCR-2.

On vLLM the model needed an NGram logits processor to avoid repetition loops; mlx-vlm has none. Phase 0 saw no
loops at temperature 0 with a 3,000-token cap, so Sieve keeps the cap and flags suspicious output instead.
"""
DEFAULT_MAX_TOKENS = 3000


def looped(text: str, min_repeats: int = 4, min_len: int = 8) -> bool:
    """True if some line of at least ``min_len`` characters repeats ``min_repeats`` times in a row."""
    lines = [l.strip() for l in text.splitlines() if len(l.strip()) >= min_len]
    run = 1
    for a, b in zip(lines, lines[1:]):
        run = run + 1 if a == b else 1
        if run >= min_repeats:
            return True
    return False
