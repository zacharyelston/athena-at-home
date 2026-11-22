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


# --- from what someone would write on a slip ----------------------------------------------------------------------

_MONTH_WORDS = {name[:3].lower(): i for i, name in enumerate(_MONTHS, start=1)}
_SEASON_WORDS = {"spring": 21, "summer": 22, "autumn": 23, "fall": 23, "winter": 24}
_UNDATED = {"", "undated", "no date", "n.d.", "nd", "n.d", "unknown", "none", "?"}
_CIRCA = re.compile(r"^(?:circa|ca\.?|c\.|c|about|around|approx\.?|approximately|roughly)\s+(.+)$")
_HELP = ("write a date like 1962, April 1962, April 17 1962, spring 1962, about 1958, 1960s, 1960-1965, "
         "before 1970, or leave it empty for an undated item")


def _month(word: str) -> int | None:
    word = word.rstrip(".").lower()
    return _MONTH_WORDS.get(word[:3]) if len(word) >= 3 and word.isalpha() else None


def _plain(text: str) -> str:
    """One date, without "circa" or a range, as EDTF."""
    t = re.sub(r"\s+", " ", text.replace(",", " ")).strip().lower()
    t = re.sub(r"\b(\d{1,2})(st|nd|rd|th)\b", r"\1", t)
    t = re.sub(r"\b(the|of)\b ?", "", t).strip()
    if is_valid(t.upper()) and "/" not in t:
        return t.upper()
    if m := re.fullmatch(r"(\d{3})0'?s", t):                              # 1960s
        return f"{m.group(1)}X"
    if m := re.fullmatch(r"(\w+) (\d{4})", t):                            # April 1962, spring 1962
        if m.group(1) in _SEASON_WORDS:
            return f"{m.group(2)}-{_SEASON_WORDS[m.group(1)]}"
        if mo := _month(m.group(1)):
            return f"{m.group(2)}-{mo:02d}"
    if m := re.fullmatch(r"([a-z]+\.?) (\d{1,2}) (\d{4})", t):            # April 17 1962
        if mo := _month(m.group(1)):
            return f"{m.group(3)}-{mo:02d}-{int(m.group(2)):02d}"
    if m := re.fullmatch(r"(\d{1,2}) ([a-z]+\.?) (\d{4})", t):            # 17 April 1962
        if mo := _month(m.group(2)):
            return f"{m.group(3)}-{mo:02d}-{int(m.group(1)):02d}"
    if m := re.fullmatch(r"(\d{1,2})[/.](\d{1,2})[/.](\d{4})", t):        # 4/17/1962
        a, b, y = int(m.group(1)), int(m.group(2)), m.group(3)
        if a > 12 >= b:
            return f"{y}-{b:02d}-{a:02d}"
        if b > 12 >= a or a == b:
            return f"{y}-{a:02d}-{b:02d}"
        raise EdtfError(f"{text.strip()!r} could be read two ways (which number is the month?); "
                        "write the month as a word, like April 5 1962")
    if m := re.fullmatch(r"(\d{1,2})[/.](\d{4})", t):                     # 4/1962
        return f"{m.group(2)}-{int(m.group(1)):02d}"
    if re.search(r"(^|[ '/.-])\d{2}$", t) and not re.search(r"\d{4}", t):
        raise EdtfError(f"{text.strip()!r}: write the year in full (1962, not 62)")
    raise EdtfError(f"can't read {text.strip()!r} as a date; {_HELP}")


def _one(text: str) -> str:
    t = text.strip()
    unsure = t.endswith("?")
    t = t.rstrip("?").strip()
    circa = _CIRCA.match(t.lower())
    if circa:
        t = circa.group(1)
    body = _plain(t)
    if body[-1] in "?~%":
        return body
    return body + {(True, True): "%", (True, False): "~", (False, True): "?", (False, False): ""}[bool(circa), unsure]


def from_text(text: str | None) -> str | None:
    """EDTF from what an archivist would write on a slip: "April 1962", "about 1958", "1960s", "1960-1965",
    "spring 1962", "before 1970". Valid EDTF passes through. Returns None for an undated item; raises EdtfError,
    with examples, for anything it can't read. Numeric dates are accepted only when they can be read one way."""
    raw = re.sub(r"\s+", " ", (text or "").replace("–", "-").replace("—", "-")).strip()
    if raw.lower() in _UNDATED:
        return None
    if is_valid(raw):
        return raw
    low = raw.lower()
    out = None
    if m := re.fullmatch(r"(?:before|earlier than) (\d{4})", low):
        out = f"../{int(m.group(1)) - 1}"
    elif m := re.fullmatch(r"(?:after|later than) (\d{4})", low):
        out = f"{int(m.group(1)) + 1}/.."
    elif m := re.fullmatch(r"(?:until|by|up to|no later than) (.+)", low):
        out = f"../{_one(m.group(1))}"
    elif m := re.fullmatch(r"(?:from|since|no earlier than) (.+?)(?: on(?:wards?)?)?", low):
        if " to " not in m.group(1) and " - " not in m.group(1):
            out = f"{_one(m.group(1))}/.."
    if out is None:
        m = (re.fullmatch(r"(?:between|from) (.+?) (?:and|to|-) (.+)", low)
             or re.fullmatch(r"(.+?) (?:to|through|thru|-) (.+)", low)
             or re.fullmatch(r"(\d{4}[?~]?)\s*-\s*(\d{4}[?~]?)", low))
        out = f"{_one(m.group(1))}/{_one(m.group(2))}" if m else _one(raw)
    parse(out)      # e.g. an interval that ends before it starts
    return out
