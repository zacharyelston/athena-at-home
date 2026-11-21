# SPDX-License-Identifier: GPL-3.0-or-later
# Derived from the author's myvect ingest.py and watcher.py (hash dedupe; polling a folder for new work).
"""Intake: capture bundles dropped into the intake directory become items, pages and text in the library.

For each bundle: validate it (athena.bundle), copy its files into the write-once store, make the OCR-sized page
derivatives, then write rows, assertions and outbox events in **one transaction**. A bundle that fails validation is
moved to ``intake/rejected/`` with a reason file and nothing from it is stored; an ingested one moves to
``intake/ingested/``. Ingesting the same bundle twice is a no-op.
"""
from __future__ import annotations

import io
import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from psycopg.types.json import Jsonb

from athena.bundle import read_bundle, validate
from athena.bundle.manifest import PARTIAL_SUFFIX

from . import records
from .core import INGESTED, REJECTED, Library


@dataclass
class IntakeResult:
    bundle: Path
    status: str                 # ingested | duplicate | rejected
    item: str | None = None
    reason: str = ""
    moved_to: Path | None = None


def pending(lib: Library) -> list[Path]:
    """Bundle directories waiting in the intake directory, oldest first (ULIDs sort by time)."""
    if not lib.intake.is_dir():
        return []
    return sorted(p for p in lib.intake.iterdir()
                  if p.is_dir() and not p.name.startswith(".") and not p.name.endswith(PARTIAL_SUFFIX)
                  and p.name not in (INGESTED, REJECTED))


def scan(lib: Library) -> list[IntakeResult]:
    return [ingest(lib, b, move=True) for b in pending(lib)]


def _move(src: Path, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name
    n = 1
    while dest.exists():
        n += 1
        dest = dest_dir / f"{src.name}.{n}"
    shutil.move(str(src), dest)
    return dest


def _reject(lib: Library, bundle: Path, reason: str, move: bool) -> IntakeResult:
    moved = None
    if move:
        moved = _move(bundle, lib.intake / REJECTED)
        (moved.parent / f"{moved.name}.reason.txt").write_text(
            f"{datetime.now(timezone.utc).isoformat(timespec='seconds')}\n{reason}\n", encoding="utf-8")
    return IntakeResult(bundle, "rejected", reason=reason, moved_to=moved)


def ocr_derivative(path: Path, ppi: float | None, target_ppi: int, max_edge: int) -> bytes:
    """A JPEG (q90) sized for OCR: about ``target_ppi`` when the page's ppi is known, never upscaled, and never
    larger than ``max_edge`` on its long side. DeepSeek-OCR-2 discards detail above ~200 ppi (Phase 0)."""
    from PIL import Image

    with Image.open(path) as im:
        im = im.convert("RGB")
        scale = 1.0
        if ppi:
            scale = min(scale, target_ppi / ppi)
        scale = min(scale, max_edge / max(im.size))
        if scale < 1.0:
            im = im.resize((max(1, round(im.width * scale)), max(1, round(im.height * scale))), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=90)
        return buf.getvalue()


def _quick_text(path: Path) -> str:
    """Plain text from a bundle's tier-0 file: a list of lines ({"text": …}), {"lines": [...]}, or {"text": …}."""
    data = json.loads(path.read_text("utf-8"))
    if isinstance(data, dict) and isinstance(data.get("text"), str):
        return data["text"]
    lines = data.get("lines", []) if isinstance(data, dict) else data
    return "\n".join(str(ln.get("text", "")) if isinstance(ln, dict) else str(ln) for ln in lines)


def ingest(lib: Library, bundle_dir: str | Path, move: bool = False) -> IntakeResult:
    bundle_dir = Path(bundle_dir)
    cfg = lib.cfg
    report = validate(bundle_dir, collections=cfg.collection_slugs() or None, series=cfg.series_slugs() or None,
                      types=set(cfg.types) or None)
    if not report.ok:
        return _reject(lib, bundle_dir, "\n".join(report.errors), move)
    b = read_bundle(bundle_dir)

    with lib.connect() as conn:
        if conn.execute("SELECT 1 FROM item WHERE id = %s", (b.id,)).fetchone():
            moved = _move(bundle_dir, lib.intake / INGESTED) if move else None
            return IntakeResult(bundle_dir, "duplicate", item=b.id, moved_to=moved)

        # Files first: the store is content-addressed and write-once, so a crash here leaves only harmless
        # duplicates of content, never a row pointing at a missing file.
        pages = []
        for p in b.pages:
            raw_ext, page_ext = p.raw.suffix.lstrip(".").lower(), p.page.suffix.lstrip(".").lower()
            lib.store.put(p.raw, raw_ext, expect_sha256=p.raw_sha256)
            lib.store.put(p.page, page_ext, expect_sha256=p.page_sha256)
            ocr_sha = None
            if p.kind not in cfg.ocr_skip_kinds:
                data = ocr_derivative(p.page, p.ppi, cfg.ocr_derivative_ppi, cfg.ocr_max_edge)
                ocr_sha = lib.store.put_derivative("ocr", data, "jpg")
            pages.append((p, raw_ext, page_ext, ocr_sha))
        slip_sha = slip_ext = None
        if b.manifest.get("slip"):
            slip_path = bundle_dir / b.manifest["slip"]
            slip_ext = slip_path.suffix.lstrip(".").lower()
            slip_sha = lib.store.put(slip_path, slip_ext, expect_sha256=b.files[b.manifest["slip"]]["sha256"])
        quality = {}
        if b.manifest.get("quality"):
            quality = json.loads((bundle_dir / b.manifest["quality"]).read_text("utf-8"))

        m = b.manifest
        source = f"capture:{m['station']['name']}"
        series_row = conn.execute("SELECT slug, collection FROM series WHERE slug = %s", (b.series,)).fetchone()
        collection_known = conn.execute("SELECT 1 FROM collection WHERE slug = %s", (b.collection,)).fetchone()
        warnings = list(report.warnings)
        if series_row:
            collection, series = series_row["collection"], series_row["slug"]
            if series_row["collection"] != b.collection:
                warnings.append(f"series {b.series!r} belongs to collection {series_row['collection']!r} here, "
                                f"not {b.collection!r}")
        else:
            collection, series = (b.collection if collection_known else None), None
        item = b.item
        fields = {"collection": collection, "series": series, "title": item.get("title"),
                  "date": item.get("date"), "type": item.get("type"), "creator": item.get("creator"),
                  "physical": item.get("physical"), "language": item.get("language"), "tags": item.get("tags"),
                  "notes": item.get("notes")}

        with conn.transaction():
            cur = conn.cursor()
            records.create_item(cur, lib.name, b.id, b.collection, b.series, fields, source)
            for p, raw_ext, page_ext, ocr_sha in pages:
                page_id = f"{b.id}/{p.n}"
                cur.execute(
                    """INSERT INTO page (id, item, n, side, kind, original_sha256, original_ext, page_sha256,
                         page_ext, ocr_sha256, width, height, ppi, ppi_source, has_gps, quality)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (page_id, b.id, p.n, p.side, p.kind, p.raw_sha256, raw_ext, p.page_sha256, page_ext, ocr_sha,
                     p.width, p.height, p.ppi, p.ppi_source, p.has_gps, Jsonb(quality.get(str(p.n), {}))))
                records.assert_(cur, "page", page_id, "page.captured",
                                {"n": p.n, "side": p.side, "kind": p.kind, "original_sha256": p.raw_sha256,
                                 "page_sha256": p.page_sha256}, source)
                if p.quick is not None:
                    records.add_text(cur, page_id, tier=0, engine="apple-vision", plain=_quick_text(p.quick),
                                     source=source)
            cur.execute(
                """INSERT INTO capture (item, station, source, device, operator, created_at, slip_sha256, slip_ext,
                     warnings, manifest) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (b.id, m["station"]["name"], m["source"], Jsonb(m.get("device", {})), m.get("operator"),
                 b.created_at, slip_sha, slip_ext, Jsonb(warnings), Jsonb(m)))
        moved = _move(bundle_dir, lib.intake / INGESTED) if move else None
        return IntakeResult(bundle_dir, "ingested", item=b.id, reason="\n".join(warnings), moved_to=moved)
