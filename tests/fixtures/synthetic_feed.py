# SPDX-License-Identifier: GPL-3.0-or-later
"""A made-up feed directory, written without a library or a database, for testing what reads feeds (the site
builder) and for measuring it at the size of a real archive. Everything in it is invented.

    python tests/fixtures/synthetic_feed.py OUT --items 800 --pool 40 --page-size 3600x4660 --noise

writes about 2,500 pages (items have 1 to 8 pages). ``--pool N`` draws only N distinct page images and reuses
them, which keeps a large feed quick to make while its file count and byte size stay realistic.
"""
from __future__ import annotations

import hashlib
import json
import random
import shutil
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from athena.publish import images

SOCIETY = "RIVERSIDE GARDEN SOCIETY"
SERIES = [("newsletters", "Newsletters", "newsletter", "The society's newsletter, sent to members a few times a year."),
          ("minutes", "Minutes", "minutes", ""),
          ("yearbooks", "Yearbooks", "yearbook", "Printed each autumn with the year's programme."),
          ("photographs", "Photographs", "photograph", "")]
WORDS = ("garden peony dahlia hedge border compost trowel seedling orchard meadow tulip iris fern arbor trellis "
         "harvest prune mulch bloom frost shade soil plot ribbon exhibit judge luncheon committee treasurer motion "
         "seconded carried spring summer autumn winter annual report members meeting library green village river "
         "bridge church hall plant sale show table prize first second cuttings bulbs roses lilac maple birch").split()
SEASONS = {"21": "Spring", "22": "Summer", "23": "Autumn", "24": "Winter"}
SPEC = images.ImageSpec(300, 1600, 3200, 0.9, 85, 0.004)


def _font(size: int):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _oid(*parts) -> str:
    return hashlib.sha256("\0".join(str(p) for p in parts).encode()).hexdigest()[:20]


def _date(rng: random.Random) -> dict | None:
    roll = rng.random()
    if roll < 0.06:
        return None
    year = rng.randint(1931, 2004)
    if roll < 0.16:
        return {"edtf": f"{year}~", "display": f"circa {year}", "earliest_year": year, "latest_year": year}
    if roll < 0.5:
        code = rng.choice(sorted(SEASONS))
        return {"edtf": f"{year}-{code}", "display": f"{SEASONS[code].lower()} {year}", "earliest_year": year,
                "latest_year": year}
    return {"edtf": str(year), "display": str(year), "earliest_year": year, "latest_year": year}


def _text(rng: random.Random, words: int) -> str:
    lines, line = [], []
    for _ in range(words):
        line.append(rng.choice(WORDS))
        if len(line) >= rng.randint(8, 13):
            lines.append(" ".join(line).capitalize() + ".")
            line = []
    return "\n".join(lines + ([" ".join(line)] if line else []))


def _draw(path: Path, size: tuple[int, int], heading: str, text: str, noise: bool, rng: random.Random) -> None:
    w, h = size
    img = Image.new("RGB", size, (250, 247, 240))
    d = ImageDraw.Draw(img)
    unit = w / 1700
    d.text((int(120 * unit), int(160 * unit)), SOCIETY, fill=(30, 30, 30), font=_font(int(64 * unit)))
    d.text((int(120 * unit), int(280 * unit)), heading[:48], fill=(30, 30, 30), font=_font(int(52 * unit)))
    y = int(420 * unit)
    for line in text.split("\n")[:40]:
        d.text((int(120 * unit), y), line[:90], fill=(40, 40, 40), font=_font(int(30 * unit)))
        y += int(42 * unit)
    if noise:                                        # paper grain, so JPEG sizes are nearer a real photograph's
        import numpy as np
        arr = np.asarray(img, dtype=np.int16)
        grain = np.random.default_rng(rng.randrange(1 << 30)).normal(0, 6, arr.shape[:2] + (1,))
        img = Image.fromarray(np.clip(arr + grain, 0, 255).astype("uint8"))
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "JPEG", quality=92)


def make_feed(root: str | Path, items: int = 24, seed: int = 1, feed: str = "public", tiers=("public",),
              page_size: tuple[int, int] = (1700, 2200), pool: int | None = None, noise: bool = False,
              max_pages: int = 8, marker: str = "") -> dict:
    """Write a feed directory at ``root`` and return {"items": [...ids], "pages": n, "words": {id: a word unique to
    that item's text}}. ``marker`` is put into every item's title and text (for leak tests)."""
    root = Path(root)
    if root.exists():
        shutil.rmtree(root)
    rng = random.Random(seed)
    work = root.parent / (root.name + ".work")
    pooled: list[dict] = []
    series_counts: dict[str, int] = {}
    out = {"items": [], "pages": 0, "words": {}}

    def page_images(page_id: str, heading: str, text: str) -> dict:
        if pool is not None and len(pooled) >= pool:
            src = rng.choice(pooled)
        else:
            raw = work / f"{page_id}.jpg"
            _draw(raw, page_size, heading, text, noise, rng)
            sizes = images.render_to_dir(str(raw), [], SPEC, str(work / page_id))
            raw.unlink()
            src = {"dir": work / page_id, "sizes": sizes}
            if pool is not None:
                pooled.append(src)
        img = {}
        for name, (w, h) in src["sizes"].items():
            dest = root / "images" / page_id / f"{name}.jpg"
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src["dir"] / f"{name}.jpg", dest)
            img[name] = {"path": f"images/{page_id}/{name}.jpg", "width": w, "height": h}
        return img

    for i in range(items):
        slug, series_title, kind, _ = SERIES[min(rng.choices(range(4), weights=(50, 30, 8, 12))[0], 3)]
        item_id = _oid(seed, "item", i)
        date = _date(rng)
        unique = f"zq{hashlib.sha256(item_id.encode()).hexdigest()[:8]}"
        title = f"{kind.capitalize()} {date['display'] if date else 'undated'} {marker}".strip()
        n_pages = 1 if kind == "photograph" else rng.choice([1, 2, 2, 3, 4, 4, 6, max_pages])
        pages = []
        for n in range(1, n_pages + 1):
            page_id = _oid(seed, "page", i, n)
            text = None if kind == "photograph" else f"{title}\n{unique} {marker}\n" + _text(rng, rng.randint(120, 260))
            status = "image-only" if text is None else rng.choice(["machine-ocr", "machine-ocr", "checked"])
            img = page_images(page_id, title, text or "")
            page = {"id": page_id, "n": n, "side": None, "kind": "photo" if text is None else "text",
                    "images": img, "text_status": status}
            (root / "pages").mkdir(parents=True, exist_ok=True)
            (root / "pages" / f"{page_id}.json").write_text(json.dumps(
                dict(page, item=item_id, text=text, redacted=False), indent=1, sort_keys=True), "utf-8")
            pages.append(page)
        (root / "items").mkdir(parents=True, exist_ok=True)
        (root / "items" / f"{item_id}.json").write_text(json.dumps({
            "id": item_id, "title": title, "date": date, "type": kind, "creator": None, "language": "en",
            "tags": rng.sample(WORDS, 2), "series": {"slug": slug, "title": series_title},
            "rights": "Shared for personal and research use (made-up fixture).", "pages": pages},
            indent=1, sort_keys=True), "utf-8")
        series_counts[slug] = series_counts.get(slug, 0) + 1
        out["items"].append(item_id)
        out["words"][item_id] = unique
        out["pages"] += n_pages

    (root / "feed.json").write_text(json.dumps({
        "feed_version": 1, "feed": feed, "tiers": sorted(tiers), "library": "synthetic",
        "built_at": "2026-12-01T12:00:00+00:00", "decided_as_of": "2026-12-01", "builder": "synthetic-feed/1",
        "zoom": "pyramid", "pd_cutoff_year": None,
        "counts": {"items": items, "pages": out["pages"], "images": sum(1 for _ in (root / "images").rglob("*.jpg"))},
        "series": [{"slug": s, "title": t, "description": desc, "items": series_counts[s]}
                   for s, t, _, desc in SERIES if s in series_counts]}, indent=1, sort_keys=True), "utf-8")
    (root / "log.jsonl").write_text("", "utf-8")
    shutil.rmtree(work, ignore_errors=True)
    return out


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("out")
    ap.add_argument("--items", type=int, default=24)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--pool", type=int)
    ap.add_argument("--page-size", default="1700x2200")
    ap.add_argument("--noise", action="store_true")
    a = ap.parse_args()
    w, h = (int(x) for x in a.page_size.split("x"))
    made = make_feed(a.out, items=a.items, seed=a.seed, pool=a.pool, page_size=(w, h), noise=a.noise)
    print(f"{len(made['items'])} items, {made['pages']} pages -> {a.out}", file=sys.stderr)
