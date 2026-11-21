# SPDX-License-Identifier: GPL-3.0-or-later
# Derived from the author's OCRX scan.py (Apple Vision OCR) and Athena@Home phase0/bench.py.
"""Tier 0: Apple Vision text recognition (macOS/iOS only), plus the page signals the tile policy reads.

Phase 0 found Vision fast (~0.2 s/page) and accurate, but on one page it silently dropped three amounts, so its
text is for instant search and for choosing a tile budget, never the text of record.
"""
from __future__ import annotations

import re
import tempfile
from dataclasses import dataclass

AMOUNT_OR_DATE = re.compile(r"\d[\d,]*\.\d\d|\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b|\b\d{3,}\b")


@dataclass
class Line:
    text: str
    x: float      # left edge, fraction of page width
    y: float      # top edge, fraction of page height (0 = top)
    w: float
    h: float      # line height, fraction of page height
    confidence: float


def recognize(image) -> list[Line]:
    """Apple Vision (accurate, language correction) on a PIL image. Lines in reading order (rows, then x)."""
    import Vision
    from Foundation import NSURL

    with tempfile.NamedTemporaryFile(suffix=".png") as f:
        image.convert("RGB").save(f.name)
        handler = Vision.VNImageRequestHandler.alloc().initWithURL_options_(NSURL.fileURLWithPath_(f.name), None)
        req = Vision.VNRecognizeTextRequest.alloc().init()
        req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
        req.setUsesLanguageCorrection_(True)
        ok, err = handler.performRequests_error_([req], None)
        if not ok:
            raise RuntimeError(f"Apple Vision failed: {err}")
        lines = []
        for obs in req.results() or []:
            cand = obs.topCandidates_(1)[0]
            bb = obs.boundingBox()  # normalized, origin bottom-left
            lines.append(Line(cand.string(), bb.origin.x, 1 - bb.origin.y - bb.size.height,
                              bb.size.width, bb.size.height, float(cand.confidence())))
    lines.sort(key=lambda l: (round(l.y, 2), l.x))
    return lines


def text_of(lines: list[Line]) -> str:
    """Join lines into rows (same baseline → one row, left to right)."""
    rows, cur, cur_y = [], [], None
    for ln in sorted(lines, key=lambda l: (l.y, l.x)):
        if cur_y is not None and abs(ln.y - cur_y) > 0.006:
            rows.append(" ".join(l.text for l in sorted(cur, key=lambda l: l.x)))
            cur = []
        if not cur:
            cur_y = ln.y
        cur.append(ln)
    if cur:
        rows.append(" ".join(l.text for l in sorted(cur, key=lambda l: l.x)))
    return "\n".join(rows)


@dataclass
class PageSignals:
    chars: int
    lines: int
    numeric_lines: int     # rows with 2+ amounts, dates or long numbers: tables and statements
    small_lines: int       # lines shorter than SMALL_TEXT (fine print, form labels)
    median_height: float


SMALL_TEXT = 0.0115  # line height as a fraction of page height; ~9 pt type on a US Letter page


def signals(lines: list[Line]) -> PageSignals:
    text = text_of(lines)
    rows = [r for r in text.splitlines() if r.strip()]
    hs = sorted(l.h for l in lines) or [0.0]
    return PageSignals(
        chars=len(text),
        lines=len(rows),
        numeric_lines=sum(1 for r in rows if len(AMOUNT_OR_DATE.findall(r)) >= 2),
        small_lines=sum(1 for l in lines if l.h < SMALL_TEXT),
        median_height=hs[len(hs) // 2],
    )
