# SPDX-License-Identifier: GPL-3.0-or-later
from datetime import date

import pytest

from athena.bundle import edtf
from athena.bundle.edtf import EdtfError, parse

D = date


@pytest.mark.parametrize("text, lo, hi, approx, unsure", [
    ("1962", D(1962, 1, 1), D(1962, 12, 31), False, False),
    ("1962-04", D(1962, 4, 1), D(1962, 4, 30), False, False),
    ("1964-02", D(1964, 2, 1), D(1964, 2, 29), False, False),      # leap year
    ("1962-04-17", D(1962, 4, 17), D(1962, 4, 17), False, False),
    ("196X", D(1960, 1, 1), D(1969, 12, 31), False, False),
    ("19XX", D(1900, 1, 1), D(1999, 12, 31), False, False),
    ("1962-XX", D(1962, 1, 1), D(1962, 12, 31), False, False),
    ("1962-04-XX", D(1962, 4, 1), D(1962, 4, 30), False, False),
    ("1962-21", D(1962, 3, 1), D(1962, 5, 31), False, False),      # spring
    ("1962-22", D(1962, 6, 1), D(1962, 8, 31), False, False),      # summer
    ("1962-23", D(1962, 9, 1), D(1962, 11, 30), False, False),     # autumn
    ("1963-24", D(1963, 12, 1), D(1964, 2, 29), False, False),     # winter runs into the next year
    ("1962~", D(1962, 1, 1), D(1962, 12, 31), True, False),
    ("1962?", D(1962, 1, 1), D(1962, 12, 31), False, True),
    ("1962%", D(1962, 1, 1), D(1962, 12, 31), True, True),
    ("1962-04~", D(1962, 4, 1), D(1962, 4, 30), True, False),
])
def test_single_dates(text, lo, hi, approx, unsure):
    d = parse(text)
    assert (d.earliest, d.latest, d.approximate, d.uncertain, d.interval) == (lo, hi, approx, unsure, False)


@pytest.mark.parametrize("text, lo, hi", [
    ("1960/1969", D(1960, 1, 1), D(1969, 12, 31)),
    ("1960-05/1961-02", D(1960, 5, 1), D(1961, 2, 28)),
    ("../1969", None, D(1969, 12, 31)),
    ("1960/..", D(1960, 1, 1), None),
    ("/1969", None, D(1969, 12, 31)),
    ("1960/", D(1960, 1, 1), None),
    ("195X/196X", D(1950, 1, 1), D(1969, 12, 31)),
    ("1960~/1965", D(1960, 1, 1), D(1965, 12, 31)),
])
def test_intervals(text, lo, hi):
    d = parse(text)
    assert d.interval and (d.earliest, d.latest) == (lo, hi)


def test_years_helpers():
    d = parse("1960/1969")
    assert (d.earliest_year, d.latest_year) == (1960, 1969)
    assert parse("../1969").earliest_year is None


@pytest.mark.parametrize("text", [
    "", "  ", "62", "1962-4", "1962-13", "1962-00", "1962-02-30", "1962-21-01", "1962/1960", "/", "../..",
    "1962/1963/1964", "196X-04", "1X62", "1962-XX-17", "circa 1962", "1962~~", "0000", "1962-04-17T10:00",
])
def test_rejects(text):
    with pytest.raises(EdtfError):
        parse(text)


# --- dates as someone would write them on a slip ----------------------------------------------------------------

@pytest.mark.parametrize("written, stored", [
    ("1962", "1962"), ("April 1962", "1962-04"), ("Apr. 17, 1962", "1962-04-17"), ("17 April 1962", "1962-04-17"),
    ("April 17th, 1962", "1962-04-17"), ("spring 1962", "1962-21"), ("Fall of 1958", "1958-23"),
    ("about 1958", "1958~"), ("c. 1958", "1958~"), ("circa April 1962", "1962-04~"), ("1960s", "196X"),
    ("1960's", "196X"), ("1960-1965", "1960/1965"), ("1960 – 1965", "1960/1965"),
    ("between 1960 and 1965", "1960/1965"), ("from 1960 to 1965", "1960/1965"),
    ("April 1962 to June 1962", "1962-04/1962-06"), ("before 1970", "../1969"), ("after 1945", "1946/.."),
    ("until 1969", "../1969"), ("since 1960", "1960/.."), ("1962?", "1962?"), ("April 1962?", "1962-04?"),
    ("about 1958?", "1958%"), ("4/17/1962", "1962-04-17"), ("17/4/1962", "1962-04-17"), ("4/1962", "1962-04"),
    ("  1962-21 ", "1962-21"), ("196X", "196X"), ("1960/1969", "1960/1969"), ("../1969", "../1969"),
])
def test_from_text_reads_what_is_on_a_slip(written, stored):
    assert edtf.from_text(written) == stored
    assert edtf.from_text(stored) == stored          # and EDTF itself passes through
    edtf.parse(stored)


@pytest.mark.parametrize("written", ["", "  ", "undated", "n.d.", "No date", "unknown", None])
def test_from_text_undated(written):
    assert edtf.from_text(written) is None


@pytest.mark.parametrize("written, says", [
    ("4/5/1962", "could be read two ways"), ("'62", "year in full"), ("April 62", "year in full"),
    ("sometime", "write a date like 1962"), ("1965-1960", "ends before it starts"), ("Feb 30 1962", "no such day"),
    ("the sixties", "write a date like"),
])
def test_from_text_refuses_what_it_cannot_read_and_says_how_to_write_it(written, says):
    with pytest.raises(edtf.EdtfError, match=says):
        edtf.from_text(written)
