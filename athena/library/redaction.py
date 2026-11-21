# SPDX-License-Identifier: GPL-3.0-or-later
"""Redactions on a page: what a feed must hide. Stored on ``page.redaction`` as a JSON list; never applied to the
originals, only burned into what the publisher builds.

    {"type": "region", "x": 0.12, "y": 0.40, "w": 0.30, "h": 0.05}
        a rectangle on the page image, as fractions of its width and height (origin top-left)
    {"type": "text", "text_id": 41, "start": 120, "end": 132}
        characters [start, end) of one specific text row's plain text
    {"type": "text_checked", "text_id": 41}
        "I read this text row against the page's region redactions; nothing more needs hiding"

Text entries name the text row they were made against. If the page's current text is a different row (a new OCR
run, a correction), the old spans may no longer line up, so the publisher withholds the page's text until the
redactions are redone. Likewise a page with a region redaction but no text entry for its current text has its text
withheld: the OCR text very likely still contains what the rectangle covers.
"""
from __future__ import annotations

from dataclasses import dataclass

from psycopg.types.json import Jsonb

from . import records

REDACTED = "[redacted]"


class RedactionError(ValueError):
    pass


@dataclass(frozen=True)
class Region:
    x: float
    y: float
    w: float
    h: float


def validate(entries) -> list[dict]:
    """Check a redaction list and return it normalized. Raises RedactionError with a readable message."""
    if not isinstance(entries, list):
        raise RedactionError("redactions are a list")
    out = []
    for i, e in enumerate(entries):
        if not isinstance(e, dict):
            raise RedactionError(f"redaction {i}: not an object")
        kind = e.get("type")
        if kind == "region":
            try:
                x, y, w, h = (float(e[k]) for k in ("x", "y", "w", "h"))
            except (KeyError, TypeError, ValueError):
                raise RedactionError(f"redaction {i}: a region needs numbers x, y, w, h") from None
            if not (0 <= x < 1 and 0 <= y < 1 and w > 0 and h > 0 and x + w <= 1.0001 and y + h <= 1.0001):
                raise RedactionError(f"redaction {i}: region must lie inside the page (fractions 0 to 1)")
            entry = {"type": "region", "x": x, "y": y, "w": min(w, 1 - x), "h": min(h, 1 - y)}
        elif kind == "text":
            try:
                tid, start, end = int(e["text_id"]), int(e["start"]), int(e["end"])
            except (KeyError, TypeError, ValueError):
                raise RedactionError(f"redaction {i}: a text span needs text_id, start, end") from None
            if start < 0 or end <= start:
                raise RedactionError(f"redaction {i}: text span must have 0 <= start < end")
            entry = {"type": "text", "text_id": tid, "start": start, "end": end}
        elif kind == "text_checked":
            try:
                entry = {"type": "text_checked", "text_id": int(e["text_id"])}
            except (KeyError, TypeError, ValueError):
                raise RedactionError(f"redaction {i}: text_checked needs text_id") from None
        else:
            raise RedactionError(f"redaction {i}: type must be region, text or text_checked, not {kind!r}")
        if e.get("note"):
            entry["note"] = str(e["note"])
        out.append(entry)
    return out


def regions(entries) -> list[Region]:
    return [Region(e["x"], e["y"], e["w"], e["h"]) for e in entries if e.get("type") == "region"]


def redact_text(plain: str, text_id: int, entries) -> tuple[str | None, str]:
    """The text a feed may carry for a page whose current text row is ``text_id``, and why.

    Returns (text, status): status is ``ok`` (no redactions, or spans applied), or a reason the text is withheld
    (then text is None)."""
    has_region = any(e.get("type") == "region" for e in entries)
    text_entries = [e for e in entries if e.get("type") in ("text", "text_checked")]
    stale = [e for e in text_entries if e["text_id"] != text_id]
    current = [e for e in text_entries if e["text_id"] == text_id]
    if stale and not current:
        return None, "withheld: text redactions were made against an earlier text and need redoing"
    if stale:
        return None, "withheld: some text redactions were made against an earlier text and need redoing"
    if has_region and not current:
        return None, "withheld: the page has a region redaction and its text hasn't been checked"
    spans = sorted((e["start"], e["end"]) for e in current if e["type"] == "text")
    for start, end in spans:
        if end > len(plain):
            return None, "withheld: a text redaction runs past the end of the text"
    out, pos = [], 0
    for start, end in spans:
        if start < pos:                      # overlapping spans: extend the previous redaction
            pos = max(pos, end)
            continue
        out.append(plain[pos:start])
        out.append(REDACTED)
        pos = end
    out.append(plain[pos:])
    return "".join(out), "ok"


def set_page_redaction(conn, library: str, page_id: str, entries, who: str, why: str = "") -> int:
    """Replace a page's redactions (validated), with an assertion and an event. Returns the assertion id."""
    clean = validate(entries)
    with conn.transaction():
        cur = conn.cursor()
        row = cur.execute("SELECT item FROM page WHERE id = %s", (page_id,)).fetchone()
        if row is None:
            raise KeyError(f"no page {page_id}")
        text_ids = {e["text_id"] for e in clean if "text_id" in e}
        if text_ids:
            known = {r["id"] for r in cur.execute("SELECT id FROM text WHERE page = %s AND id = ANY(%s)",
                                                  (page_id, list(text_ids)))}
            if text_ids - known:
                raise RedactionError(f"text rows {sorted(text_ids - known)} don't belong to page {page_id}")
        cur.execute("UPDATE page SET redaction = %s WHERE id = %s", (Jsonb(clean), page_id))
        aid = records.assert_(cur, "page", page_id, "page.redaction", {"redaction": clean, "why": why},
                              f"person:{who}")
        records.emit(cur, library, "page.redacted", item=row["item"], page=page_id)
    return aid
