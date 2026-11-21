# SPDX-License-Identifier: GPL-3.0-or-later
"""Writing a capture bundle, atomically: everything goes into ``<id>.partial/``, is checked, then renamed to
``<id>/``. A bundle is immutable once written; later changes to its description are made in the library."""
from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import ulid
from .manifest import BUNDLE_VERSION, MANIFEST, PARTIAL_SUFFIX
from .validate import sha256_file, validate

RAW_EXT = {"heic", "heif", "jpg", "jpeg", "png", "tif", "tiff", "dng"}
PAGE_EXT = {"jpg", "png"}
SLIP_EXT = {"jpg", "jpeg", "png", "heic"}


class BundleError(Exception):
    pass


@dataclass
class PageInput:
    """One page to put in a bundle. ``raw`` is copied byte for byte; ``page`` is the processed image."""
    raw: Path
    page: Path
    width: int
    height: int
    side: str = "recto"
    kind: str = "text"
    ppi: float | None = None
    ppi_source: str = "unknown"
    has_gps: bool = False
    quick: dict | list | None = None   # Apple Vision tier-0 lines, if the station has them


def _ext(path: Path, allowed: set[str], what: str) -> str:
    ext = path.suffix.lower().lstrip(".")
    if ext not in allowed:
        raise BundleError(f"{what} {path.name!r}: extension must be one of {sorted(allowed)}")
    return ext


def _fsync(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_json(path: Path, data) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, ensure_ascii=False)
        f.write("\n")


def write_bundle(parent: str | Path, *, station: dict, source: str, describe: dict, pages: list[PageInput],
                 device: dict | None = None, operator: str | None = None, quality: dict | None = None,
                 slip: Path | None = None, bundle_id: str | None = None,
                 created_at: datetime | None = None) -> Path:
    """Write a bundle under ``parent`` and return its directory. Raises BundleError if it wouldn't validate."""
    if not pages:
        raise BundleError("a bundle needs at least one page")
    parent = Path(parent)
    bid = bundle_id or ulid.new()
    final = parent / bid
    tmp = parent / f"{bid}{PARTIAL_SUFFIX}"
    if final.exists():
        raise FileExistsError(f"bundle {bid} already exists; bundles are never overwritten")
    parent.mkdir(parents=True, exist_ok=True)
    tmp.mkdir()  # fails if another writer has the same id in progress
    try:
        manifest = _fill(tmp, bid, station=station, source=source, describe=describe, pages=pages, device=device,
                         operator=operator, quality=quality, slip=slip,
                         created_at=created_at or datetime.now(timezone.utc).astimezone())
        _write_json(tmp / MANIFEST, manifest)
        for p in tmp.rglob("*"):
            if p.is_file():
                _fsync(p)
        report = validate(tmp, allow_partial=True)
        if not report.ok:
            raise BundleError("bundle would not validate:\n" + "\n".join(report.errors))
        os.rename(tmp, final)
        _fsync(parent)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return final


def _fill(tmp: Path, bid: str, *, station, source, describe, pages, device, operator, quality, slip,
          created_at) -> dict:
    files: dict[str, dict] = {}

    def add(src: Path | None, rel: str, data=None) -> str:
        dest = tmp / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if data is not None:
            _write_json(dest, data)
        else:
            shutil.copyfile(src, dest)
        files[rel] = {"sha256": sha256_file(dest), "bytes": dest.stat().st_size}
        return rel

    out_pages = []
    for n, p in enumerate(pages, start=1):
        entry = {
            "n": n, "side": p.side, "kind": p.kind,
            "raw": add(p.raw, f"raw/{n:03d}.{_ext(Path(p.raw), RAW_EXT, 'raw file')}"),
            "page": add(p.page, f"pages/{n:03d}.{_ext(Path(p.page), PAGE_EXT, 'page image')}"),
            "width": p.width, "height": p.height, "ppi": p.ppi, "ppi_source": p.ppi_source,
            "has_gps": p.has_gps,
        }
        if p.quick is not None:
            entry["quick"] = add(None, f"quick/{n:03d}.json", p.quick)
        out_pages.append(entry)

    manifest = {
        "bundle_version": BUNDLE_VERSION,
        "id": bid,
        "created_at": created_at.isoformat(timespec="seconds"),
        "station": station,
        "source": source,
    }
    if device:
        manifest["device"] = device
    if operator:
        manifest["operator"] = operator
    manifest["describe"] = describe
    if slip is not None:
        manifest["slip"] = add(Path(slip), f"slip.{_ext(Path(slip), SLIP_EXT, 'slip photo')}")
    if quality is not None:
        manifest["quality"] = add(None, "quality.json", quality)
    manifest["pages"] = out_pages
    manifest["files"] = dict(sorted(files.items()))
    return manifest
