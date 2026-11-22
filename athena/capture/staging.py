# SPDX-License-Identifier: GPL-3.0-or-later
"""The staging directory: where imported bundles wait, can still be described, and leave for the library.

    <staging>/<bundle-id>/     a complete, valid bundle that hasn't been sent yet
    <staging>/imported.jsonl   which source files each bundle was made from (so a second import adds nothing)
    <staging>/sent.jsonl       which bundles have gone to the library

A bundle's files never change after import. Its description (and each page's side and kind) may be completed here;
once it's sent it belongs to the library, and later corrections are made there.
"""
from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

import jsonschema

from athena.bundle import edtf, read_bundle, schema, validate
from athena.bundle.manifest import MANIFEST, PARTIAL_SUFFIX
from athena.bundle.ulid import is_ulid

IMPORTED, SENT, WORK = "imported.jsonl", "sent.jsonl", ".work"


def staged(staging: Path) -> list[Path]:
    """Bundles waiting in staging, oldest first."""
    if not staging.is_dir():
        return []
    return sorted(p for p in staging.iterdir() if p.is_dir() and is_ulid(p.name))


def _lines(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    out = []
    for line in path.read_text("utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue                          # a line cut short by a crash: the bundle it named was never finished
    return out


def _append(path: Path, entry: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())


class Ledger:
    """Which source files have already become bundles. An entry counts only while its bundle still exists (in
    staging, or sent), so discarding a staged bundle makes its files importable again."""

    def __init__(self, staging: Path | None):
        self.staging = staging
        self.pages: dict[str, str] = {}       # sha256 of a source file -> bundle id
        self.separators: dict[str, str] = {}
        if staging is None:
            return
        alive = {p.name for p in staged(staging)} | {e.get("bundle") for e in _lines(staging / SENT)}
        for e in _lines(staging / IMPORTED):
            if e.get("bundle") in alive:
                self.pages.update({sha: e["bundle"] for sha in e.get("raws", [])})
                if e.get("separator"):
                    self.separators[e["separator"]] = e["bundle"]

    def record(self, bundle: str, raws: list[str], separator: str | None, session: str, files: list[str]) -> None:
        _append(self.staging / IMPORTED, {"bundle": bundle, "raws": raws, "separator": separator, "session": session,
                                          "files": files, "at": _now()})
        self.pages.update({sha: bundle for sha in raws})
        if separator:
            self.separators[separator] = bundle


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --- describing ---------------------------------------------------------------------------------------------------

class DescribeError(ValueError):
    pass


def tidy_item(item: dict) -> dict:
    """Drop empty fields, so a manifest says nothing rather than "" about what nobody filled in."""
    out = {}
    for key, value in item.items():
        if isinstance(value, str):
            value = value.strip()
        if isinstance(value, dict):
            value = {k: v.strip() for k, v in value.items() if isinstance(v, str) and v.strip()}
        if value not in ("", None, [], {}):
            out[key] = value
    return out


def redescribe(bundle_dir: Path, *, collection: str, series: str, item: dict,
               pages: dict[int, dict] | None = None) -> dict:
    """Replace a staged bundle's description, and optionally pages' ``side`` and ``kind``. Nothing else changes: the
    files and their checksums stay as imported. Raises DescribeError with readable messages if it wouldn't be valid."""
    bundle_dir = Path(bundle_dir)
    manifest = json.loads((bundle_dir / MANIFEST).read_text("utf-8"))
    manifest["describe"] = {"collection": collection, "series": series, "item": tidy_item(item)}
    for p in manifest["pages"]:
        for key, value in ((pages or {}).get(p["n"]) or {}).items():
            if key in ("side", "kind"):
                p[key] = value
    problems = [f"{'/'.join(str(x) for x in e.absolute_path) or 'bundle'}: {e.message}"
                for e in jsonschema.Draft202012Validator(schema()).iter_errors(manifest)]
    date = manifest["describe"]["item"].get("date")
    if date is not None and not problems:
        try:
            edtf.parse(date)
        except edtf.EdtfError as e:
            problems.append(f"date: {e}")
    if problems:
        raise DescribeError("; ".join(problems))
    tmp = bundle_dir / f"{MANIFEST}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=1, ensure_ascii=False)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, bundle_dir / MANIFEST)
    return manifest


# --- sending and discarding ---------------------------------------------------------------------------------------

def send(staging: Path, intake: Path, ids: list[str] | None = None, *, series=None, collections=None,
         types=None) -> list[tuple[str, str, str]]:
    """Move staged bundles into the library's intake directory. Returns (bundle id, status, detail) for each, where
    status is ``sent``, ``invalid`` (left in staging) or ``exists`` (the library already has one with that id).

    A bundle appears in intake complete or not at all: on the same disk it's renamed into place; onto another disk
    it's copied under a ``.partial`` name the library ignores, checked against its checksums, then renamed."""
    out = []
    wanted = set(ids) if ids else None
    bundles = [b for b in staged(staging) if wanted is None or b.name in wanted]
    for missing in sorted((wanted or set()) - {b.name for b in bundles}):
        out.append((missing, "invalid", "no such bundle in staging"))
    intake.mkdir(parents=True, exist_ok=True)
    for b in bundles:
        report = validate(b, series=series, collections=collections, types=types)
        if not report.ok:
            out.append((b.name, "invalid", "; ".join(report.errors)))
            continue
        dest = intake / b.name
        if dest.exists() or (intake / "ingested" / b.name).exists():
            out.append((b.name, "exists", "the library's intake already has this bundle"))
            continue
        try:
            os.rename(b, dest)
        except OSError:                       # another disk, or a network share
            partial = intake / f"{b.name}{PARTIAL_SUFFIX}"
            shutil.rmtree(partial, ignore_errors=True)
            shutil.copytree(b, partial)
            copied = validate(partial, allow_partial=True)
            if not copied.ok:
                shutil.rmtree(partial, ignore_errors=True)
                out.append((b.name, "invalid", "the copy didn't match: " + "; ".join(copied.errors)))
                continue
            os.rename(partial, dest)
            shutil.rmtree(b)
        _append(staging / SENT, {"bundle": b.name, "at": _now()})
        out.append((b.name, "sent", "; ".join(report.warnings)))
    return out


def discard(staging: Path, bundle_id: str) -> bool:
    """Delete a staged bundle (never a sent one), so its source files can be imported again."""
    target = staging / bundle_id
    if not is_ulid(bundle_id) or not target.is_dir():
        return False
    shutil.rmtree(target)
    return True


def summary(bundle_dir: Path) -> dict:
    """What the describe form and ``athena capture staged`` show about one staged bundle."""
    b = read_bundle(bundle_dir)
    quality = {}
    if b.manifest.get("quality"):
        try:
            quality = json.loads((bundle_dir / b.manifest["quality"]).read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            quality = {}
    flags = sum(1 for q in quality.values() if isinstance(q, dict) and q.get("flags"))   # pages to shoot again
    return {"id": b.id, "series": b.series, "collection": b.collection, "item": b.item,
            "pages": len(b.manifest["pages"]), "flags": flags, "slip": b.manifest.get("slip"),
            "created_at": b.manifest["created_at"], "quality": quality}
