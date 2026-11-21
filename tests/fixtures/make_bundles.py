# SPDX-License-Identifier: GPL-3.0-or-later
"""Synthetic capture bundles for tests: made-up items, pages drawn with Pillow, no real records.

    python tests/fixtures/make_bundles.py OUT_DIR     write the example bundles to OUT_DIR

Bundles are generated at test time, never committed.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw

from athena.bundle import PageInput, write_bundle

STATION = {"name": "test-desk", "software": "athena-tests", "version": "0"}
CREATED = datetime(2026, 12, 8, 14, 3, 11, tzinfo=timezone.utc)


def draw_page(path: Path, lines: list[str], size=(850, 1100), fmt="JPEG", color="white", **save) -> tuple[int, int]:
    img = Image.new("RGB", size, color)
    d = ImageDraw.Draw(img)
    for i, line in enumerate(lines):
        d.text((60, 60 + 28 * i), line, fill="black")
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, fmt, **save)
    return size


def example_items() -> list[dict]:
    """Three made-up items covering the shapes a club archive has: a two-sided newsletter, a clipping, a photo."""
    return [
        {"series": "newsletters", "source": "camera",
         "item": {"title": "Newsletter, spring issue", "date": "1962-21", "type": "newsletter",
                  "physical": {"container": "box 1, folder 3", "format": "mimeograph", "extent": "2 pages"},
                  "language": "en", "tags": ["flower-show"]},
         "pages": [("recto", "text", ["RIVERSIDE GARDEN SOCIETY", "Spring Newsletter", "Flower show: May 12"]),
                   ("verso", "text", ["Plant sale results", "Thank you to all volunteers"])]},
        {"series": "clippings", "source": "flatbed",
         "item": {"title": "Article on the town green planting", "date": "1958~", "type": "clipping",
                  "physical": {"format": "newsprint"}},
         "pages": [("recto", "text", ["TOWN GREEN GETS NEW BEDS", "Members of the society planted 400 bulbs"])]},
        {"series": "photographs", "source": "flatbed",
         "item": {"title": "Members at the flower show", "date": "1960/1965", "type": "photograph"},
         "pages": [("recto", "photo", []), ("verso", "handwriting", ["flower show"])]},
    ]


def make_bundle(dest: Path, spec: dict, work: Path, *, with_quick: bool = True, slip: bool = True) -> Path:
    """Write one bundle from an ``example_items()``-style spec, using ``work`` for the source images."""
    work.mkdir(parents=True, exist_ok=True)
    pages = []
    for n, (side, kind, lines) in enumerate(spec["pages"], start=1):
        raw = work / f"IMG_{n:04d}.jpg"
        page = work / f"page_{n:03d}.jpg"
        draw_page(raw, lines, size=(1000, 1300), color="#eee8d5", quality=92)
        w, h = draw_page(page, lines, quality=95)
        quick = [{"text": t, "confidence": 0.99} for t in lines] if with_quick and kind == "text" else None
        pages.append(PageInput(raw=raw, page=page, width=w, height=h, side=side, kind=kind, ppi=100.0,
                               ppi_source="declared", quick=quick))
    slip_path = None
    if slip:
        slip_path = work / "slip.jpg"
        draw_page(slip_path, [spec["item"].get("title", "")], size=(400, 300))
    quality = {str(n): {"sharpness": 210.5, "glare": 0.0, "skew_deg": 0.3} for n in range(1, len(pages) + 1)}
    return write_bundle(dest, station=STATION, source=spec["source"], operator="station-1",
                        describe={"collection": "archive", "series": spec["series"], "item": spec["item"]},
                        pages=pages, quality=quality, slip=slip_path, created_at=CREATED,
                        device={"make": "Synthetic", "model": "Renderer"})


def make_fixture_bundles(dest: Path, work: Path | None = None) -> list[Path]:
    work = work or dest.parent / f"{dest.name}-work"
    return [make_bundle(dest, spec, work / str(i)) for i, spec in enumerate(example_items())]


if __name__ == "__main__":
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "bundles")
    for b in make_fixture_bundles(out):
        print(b)
