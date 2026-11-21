# SPDX-License-Identifier: GPL-3.0-or-later
"""Item dates in EDTF (Library of Congress Extended Date/Time Format), the subset an archive needs.

Accepted (EDTF levels 0 and 1):

    1962          a year                  1962-04       a month           1962-04-17   a day
    196X          a decade                19XX          a century         1962-XX      some month of 1962
    1962-21       spring 1962 (22 summer, 23 autumn, 24 winter: Northern Hemisphere months)
    1962~         circa                   1962?         uncertain         1962%        both
    1960/1969     between                 ../1969       up to 1969        1960/..      from 1960 on
    /1969         start unknown           1960/         end unknown

``parse`` returns the earliest and latest calendar dates the expression allows; policies compare against those
(an age policy uses the latest possible date, so uncertainty never makes an item look older than it may be).
"""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date

_SINGLE = re.compile(
    r"^(?P<year>\d{4}|\d{3}X|\d{2}XX)"
    r"(?:-(?P<month>\d{2}|XX)(?:-(?P<day>\d{2}|XX))?)?$"
)
_QUALIFIER = re.compile(r"[?~%]$")
SEASONS = {21: (3, 5), 22: (6, 8), 23: (9, 11), 24: (12, 2)}  # spring, summer, autumn, winter → first, last month


class EdtfError(ValueError):
    pass


@dataclass(frozen=True)
class EdtfDate:
    text: str
    earliest: date | None   # None: open or unknown start
    latest: date | None     # None: open or unknown end
    approximate: bool = False
    uncertain: bool = False
    interval: bool = False

    @property
    def earliest_year(self) -> int | None:
        return self.earliest.year if self.earliest else None

    @property
    def latest_year(self) -> int | None:
        return self.latest.year if self.latest else None


def _last_day(y: int, m: int) -> int:
    return calendar.monthrange(y, m)[1]


def _single(text: str) -> tuple[date, date]:
    m = _SINGLE.match(text)
    if not m:
        raise EdtfError(f"not an EDTF date: {text!r}")
    ys, ms, ds = m.group("year"), m.group("month"), m.group("day")
    y0, y1 = int(ys.replace("X", "0")), int(ys.replace("X", "9"))
    if y0 < 1:
        raise EdtfError(f"year out of range: {text!r}")
    if "X" in ys and (ms is not None or ds is not None):
        raise EdtfError(f"an unspecified year can't have a month or day: {text!r}")
    if ms is None:
        return date(y0, 1, 1), date(y1, 12, 31)
    if ms == "XX":
        if ds is not None and ds != "XX":
            raise EdtfError(f"a day needs a known month: {text!r}")
        return date(y0, 1, 1), date(y0, 12, 31)
    mo = int(ms)
    if mo in SEASONS:
        if ds is not None:
            raise EdtfError(f"a season can't have a day: {text!r}")
        first, last = SEASONS[mo]
        end_year = y0 + 1 if last < first else y0
        return date(y0, first, 1), date(end_year, last, _last_day(end_year, last))
    if not 1 <= mo <= 12:
        raise EdtfError(f"month must be 01-12 or a season 21-24: {text!r}")
    if ds is None or ds == "XX":
        return date(y0, mo, 1), date(y0, mo, _last_day(y0, mo))
    try:
        d = date(y0, mo, int(ds))
    except ValueError:
        raise EdtfError(f"no such day: {text!r}") from None
    return d, d


def _qualified(text: str) -> tuple[str, bool, bool]:
    q = _QUALIFIER.search(text)
    if not q:
        return text, False, False
    mark = q.group(0)
    return text[:-1], mark in "~%", mark in "?%"


def parse(text: str) -> EdtfDate:
    """Parse an EDTF date or interval. Raises EdtfError with a readable message."""
    if not isinstance(text, str) or not text.strip():
        raise EdtfError("empty date (leave the field out for an undated item)")
    raw = text.strip()
    if "/" not in raw:
        body, approx, unsure = _qualified(raw)
        lo, hi = _single(body)
        return EdtfDate(raw, lo, hi, approx, unsure)
    if raw.count("/") != 1:
        raise EdtfError(f"an interval has one '/': {raw!r}")
    start, end = raw.split("/")
    if start in ("", "..") and end in ("", ".."):
        raise EdtfError(f"an interval needs at least one end: {raw!r}")
    approx = unsure = False
    lo = hi = None
    if start not in ("", ".."):
        s, a, u = _qualified(start)
        lo = _single(s)[0]
        approx, unsure = approx or a, unsure or u
    if end not in ("", ".."):
        e, a, u = _qualified(end)
        hi = _single(e)[1]
        approx, unsure = approx or a, unsure or u
    if lo and hi and lo > hi:
        raise EdtfError(f"interval ends before it starts: {raw!r}")
    return EdtfDate(raw, lo, hi, approx, unsure, interval=True)


def is_valid(text: str) -> bool:
    try:
        parse(text)
        return True
    except EdtfError:
        return False


_MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
           "November", "December")
_SEASON_NAMES = {21: "spring", 22: "summer", 23: "autumn", 24: "winter"}


def _display_single(body: str) -> str:
    m = _SINGLE.match(body)
    ys, ms, ds = m.group("year"), m.group("month"), m.group("day")
    if ys.endswith("XX"):
        return f"{ys[:2]}00s"
    if ys.endswith("X"):
        return f"{ys[:3]}0s"
    if ms is None or ms == "XX":
        return ys
    mo = int(ms)
    if mo in _SEASON_NAMES:
        return f"{_SEASON_NAMES[mo]} {ys}"
    if ds is None or ds == "XX":
        return f"{_MONTHS[mo - 1]} {ys}"
    return f"{_MONTHS[mo - 1]} {int(ds)}, {ys}"


def _display_part(part: str) -> str:
    body, approx, unsure = _qualified(part)
    text = _display_single(body)
    if approx:
        text = f"circa {text}"
    return f"{text}?" if unsure else text


def display(text: str | None) -> str:
    """A date as a reader would write it: "April 1962", "spring 1962", "circa 1958", "1960s", "1960–1965"."""
    if not text:
        return "undated"
    parse(text)  # raises on a bad date
    raw = text.strip()
    if "/" not in raw:
        return _display_part(raw)
    start, end = raw.split("/")
    if start in ("", ".."):
        return f"until {_display_part(end)}" if start == ".." else f"before or in {_display_part(end)}"
    if end in ("", ".."):
        return f"from {_display_part(start)}"
    return f"{_display_part(start)}–{_display_part(end)}"
