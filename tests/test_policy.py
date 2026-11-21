# SPDX-License-Identifier: GPL-3.0-or-later
import pytest

from sieve import guard, tiles
from sieve.tier0 import Line, PageSignals, signals, text_of


def sig(chars=500, lines=15, numeric=2, small=0, median=0.015):
    return PageSignals(chars=chars, lines=lines, numeric_lines=numeric, small_lines=small, median_height=median)


def test_sparse_page_gets_overview_only():
    assert tiles.choose(sig()) == (0, "sparse page")


@pytest.mark.parametrize("s, why", [
    (sig(numeric=16), "table-like"),
    (sig(chars=2400), "dense"),
    (sig(small=3), "small print"),
    (sig(median=0.011), "small type"),
])
def test_anything_not_clearly_sparse_gets_full_tiles(s, why):
    n, reason = tiles.choose(s)
    assert n == 6 and reason.startswith(why)


def test_no_tier0_means_full_tiles():
    assert tiles.choose(None) == (6, "no tier-0 signals")


@pytest.mark.parametrize("v, want", [(None, "auto"), ("auto", "auto"), ("AUTO", "auto"), (0, 0), ("6", 6), (2, 2)])
def test_parse_tiles(v, want):
    assert tiles.parse(v) == want


@pytest.mark.parametrize("v", [7, -1, "lots"])
def test_parse_tiles_rejects(v):
    with pytest.raises(ValueError):
        tiles.parse(v)


def test_loop_guard():
    assert not guard.looped("| ok |\n" * 10)                # under 8 chars: too short to count
    assert guard.looped("| 09/02 | DEPOSIT | 1.00 |\n" * 4)
    assert not guard.looped("| row one of a table |\n| row two of a table |\n" * 4)


def test_rows_and_signals_from_vision_lines():
    lines = [Line("Date", 0.1, 0.20, 0.05, 0.012, 1), Line("Amount", 0.7, 0.201, 0.08, 0.012, 1),
             Line("09/02", 0.1, 0.23, 0.05, 0.012, 1), Line("2,450.00", 0.7, 0.231, 0.08, 0.012, 1),
             Line("fine print", 0.1, 0.9, 0.2, 0.009, 1)]
    assert text_of(lines).splitlines() == ["Date Amount", "09/02 2,450.00", "fine print"]
    s = signals(lines)
    assert (s.lines, s.numeric_lines, s.small_lines) == (3, 1, 1)
