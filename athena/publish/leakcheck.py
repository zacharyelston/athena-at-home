# SPDX-License-Identifier: GPL-3.0-or-later
"""Proving a built feed (or a site made from one) carries only what it should.

Three independent checks, used by the publisher's tests, by ``athena publish check`` and by the site and deploy
steps that follow:

``scan_for_strings``  no forbidden string anywhere: file paths, raw bytes, gzip-compressed content (a static search
                      index compresses its fragments, which would hide a leak from a plain grep) and JSON with
                      escaped characters.
``check_images``      no image carries EXIF, XMP, IPTC or an embedded colour profile.
``check_feed``        against the library itself: only the items and pages the feed should carry are present, no
                      file is a copy of an original, no image is as large as its page image, every pixel inside a
                      redaction rectangle is black in every size, and no redacted text span survives.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import warnings
import zlib
from datetime import date
from pathlib import Path

from PIL import Image

from athena.library import redaction
from athena.library.core import Library

from .build import MARKER, get_feed, opaque_id, plan_feed

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".tif", ".tiff"}
METADATA_MARKERS = {b"Exif\x00\x00": "EXIF", b"http://ns.adobe.com/xap": "XMP", b"ICC_PROFILE": "ICC profile",
                    b"Photoshop 3.0": "IPTC", b"<x:xmpmeta": "XMP"}
DARK = 64            # a redacted pixel may carry JPEG noise, never content
EDGE = 3             # output pixels at a rectangle's edge that resampling may blend with what's outside it


def _files(root: Path):
    return sorted(p for p in Path(root).rglob("*") if p.is_file())


def _views(data: bytes, name: str) -> list[tuple[str, bytes]]:
    """The byte strings to search for one file: as stored, decompressed, and with JSON escapes resolved."""
    views = [("", data)]
    if data[:2] == b"\x1f\x8b":
        try:
            views.append((" (gzip)", gzip.decompress(data)))
        except (OSError, EOFError, zlib.error):
            pass
    for label, blob in list(views):
        if name.endswith((".json", ".jsonl", ".js", ".html")) or label:
            try:
                text = blob.decode("utf-8")
                with warnings.catch_warnings():          # scripts hold backslashes that aren't escapes; fine
                    warnings.simplefilter("ignore", DeprecationWarning)
                    unescaped = text.encode("utf-8").decode("unicode_escape", errors="ignore").encode("utf-8", "ignore")
                if unescaped != blob:
                    views.append((label + " (unescaped)", unescaped))
            except UnicodeDecodeError:
                pass
    return views


def scan_for_strings(root: str | Path, forbidden) -> list[str]:
    """Problems for every forbidden string found (case-insensitive) in a path or in any file's content."""
    root = Path(root)
    needles = [(s, s.lower().encode("utf-8")) for s in forbidden if s]
    problems = []
    for f in _files(root):
        rel = f.relative_to(root).as_posix()
        for s, needle in needles:
            if needle in rel.lower().encode("utf-8"):
                problems.append(f"{rel}: path contains {s!r}")
        data = f.read_bytes()
        for label, blob in _views(data, f.name):
            low = blob.lower()
            for s, needle in needles:
                if needle in low:
                    problems.append(f"{rel}{label}: contains {s!r}")
    return problems


def check_images(root: str | Path) -> list[str]:
    root = Path(root)
    problems = []
    for f in _files(root):
        if f.suffix.lower() not in IMAGE_EXT:
            continue
        rel = f.relative_to(root).as_posix()
        data = f.read_bytes()
        for marker, what in METADATA_MARKERS.items():
            if marker in data:
                problems.append(f"{rel}: carries {what} metadata")
        try:
            with Image.open(f) as im:
                if im.getexif():
                    problems.append(f"{rel}: carries EXIF tags")
                for key in ("icc_profile", "xmp", "photoshop", "comment"):
                    if im.info.get(key):
                        problems.append(f"{rel}: carries {key}")
        except Exception as e:  # noqa: BLE001
            problems.append(f"{rel}: unreadable image ({type(e).__name__})")
    return problems


def _region_is_dark(im: Image.Image, region) -> bool:
    w, h = im.size
    box = (int(region.x * w) + EDGE, int(region.y * h) + EDGE,
           int((region.x + region.w) * w) - EDGE, int((region.y + region.h) * h) - EDGE)
    if box[2] <= box[0] or box[3] <= box[1]:
        return True                       # too small at this size to hold any content
    lo, hi = im.convert("L").crop(box).getextrema()
    return hi <= DARK


def check_feed(lib: Library, feed_name: str, root: str | Path, today: date | None = None) -> list[str]:
    """Check a built feed directory against the library it came from. Returns problems (empty = clean)."""
    root = Path(root)
    problems = []
    if not (root / MARKER).is_file():
        return [f"{root} is not a feed directory (no {MARKER})"]
    feed = get_feed(lib, feed_name)
    meta = json.loads((root / MARKER).read_text("utf-8"))
    if meta.get("feed") != feed_name or meta.get("library") != lib.name:
        problems.append(f"{MARKER}: built as feed {meta.get('feed')!r} of library {meta.get('library')!r}")
    today = today or date.fromisoformat(meta.get("decided_as_of") or date.today().isoformat())
    oid = lambda kind, rid: opaque_id(lib.name, feed_name, kind, rid)   # noqa: E731

    with lib.connect() as conn:
        selection = plan_feed(lib, conn, feed, today)
        originals = {r["sha"] for r in conn.execute(
            "SELECT original_sha256 AS sha FROM page UNION SELECT page_sha256 FROM page")}
        text_rows = {r["id"]: r["plain"] for r in conn.execute("SELECT id, plain FROM text")}

    allowed_items = {oid("item", item_id) for item_id, _, _ in selection.items}
    allowed_pages = {}
    for _, _, pages in selection.items:
        for p, entries, _ in pages:
            allowed_pages[oid("page", p["id"])] = (p, entries)

    # Only what the feed should carry.
    for f in _files(root):
        rel = f.relative_to(root).as_posix()
        parts = rel.split("/")
        if parts[0] == "items" and f.stem not in allowed_items:
            problems.append(f"{rel}: an item the feed must not carry")
        elif parts[0] == "pages" and f.stem not in allowed_pages:
            problems.append(f"{rel}: a page the feed must not carry")
        elif parts[0] == "images" and (len(parts) < 3 or parts[1] not in allowed_pages):
            problems.append(f"{rel}: an image of a page the feed must not carry")
        elif parts[0] not in ("items", "pages", "images") and rel not in (MARKER, "log.jsonl"):
            problems.append(f"{rel}: unexpected file")
        if hashlib.sha256(f.read_bytes()).hexdigest() in originals:
            problems.append(f"{rel}: is a byte-for-byte copy of an original")

    # Images: smaller than the page image, redactions black at every size.
    for page_oid, (p, entries) in allowed_pages.items():
        regions = redaction.regions(entries)
        for f in sorted((root / "images" / page_oid).glob("*")):
            rel = f.relative_to(root).as_posix()
            try:
                with Image.open(f) as im:
                    im.load()
                    if max(im.size) >= max(p["width"], p["height"]):
                        problems.append(f"{rel}: {im.size} is as large as the page image")
                    for r in regions:
                        if not _region_is_dark(im, r):
                            problems.append(f"{rel}: a redacted region is not black")
            except Exception as e:  # noqa: BLE001
                problems.append(f"{rel}: unreadable image ({type(e).__name__})")

        # Text: no redacted span survives anywhere in the page's JSON.
        page_json = root / "pages" / f"{page_oid}.json"
        if page_json.is_file():
            blob = page_json.read_text("utf-8")
            for e in entries:
                if e["type"] != "text":
                    continue
                hidden = text_rows.get(e["text_id"], "")[e["start"]:e["end"]].strip()
                if len(hidden) >= 3 and (hidden in blob or json.dumps(hidden)[1:-1] in blob):
                    problems.append(f"pages/{page_oid}.json: redacted text is still present")
    return problems + check_images(root)


def diff(old: str | Path, new: str | Path) -> dict[str, list[str]]:
    """Paths removed, changed and added between two builds (for purging a host's cache after a takedown)."""
    def tree(root):
        root = Path(root)
        return {f.relative_to(root).as_posix(): hashlib.sha256(f.read_bytes()).hexdigest() for f in _files(root)}
    a, b = tree(old), tree(new)
    return {"removed": sorted(a.keys() - b.keys()),
            "changed": sorted(k for k in a.keys() & b.keys() if a[k] != b[k]),
            "added": sorted(b.keys() - a.keys())}
