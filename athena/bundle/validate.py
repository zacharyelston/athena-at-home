# SPDX-License-Identifier: GPL-3.0-or-later
"""Checking a capture bundle before the library takes it: schema, files, checksums, page sequence, dates.

Errors make a bundle unacceptable. Warnings (an unknown series, say) don't: the library files such an item under
"unsorted" for someone to look at.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import jsonschema

from . import edtf
from .manifest import MANIFEST, OS_DEBRIS, PARTIAL_SUFFIX, schema

_NUMBERED = re.compile(r"^(raw|pages|quick)/(\d{3,})\.[a-z]+$")


@dataclass
class Report:
    path: Path
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def __str__(self) -> str:
        if self.ok and not self.warnings:
            return f"{self.path}: ok"
        lines = [f"{self.path}: {'ok with warnings' if self.ok else 'INVALID'}"]
        lines += [f"  error: {e}" for e in self.errors]
        lines += [f"  warning: {w}" for w in self.warnings]
        return "\n".join(lines)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _is_debris(rel: str) -> bool:
    name = rel.rsplit("/", 1)[-1]
    return name in OS_DEBRIS or name.startswith("._")  # ._* : macOS AppleDouble files on non-Mac disks


def _files_on_disk(root: Path) -> set[str]:
    return {p.relative_to(root).as_posix() for p in root.rglob("*")
            if p.is_file() and not _is_debris(p.relative_to(root).as_posix())}


def validate(path: str | Path, *, allow_partial: bool = False, collections=None, series=None,
             types=None) -> Report:
    """Validate the bundle directory at ``path``.

    ``collections``, ``series`` and ``types``: the slugs the receiving library knows (from its config); a slug not
    among them is a warning. ``allow_partial`` lets the writer check a bundle before its final rename.
    """
    root = Path(path)
    rep = Report(root)
    if not root.is_dir():
        rep.errors.append("not a directory")
        return rep
    if root.name.endswith(PARTIAL_SUFFIX) and not allow_partial:
        rep.errors.append(f"incomplete bundle: '{PARTIAL_SUFFIX}' directories are still being written")
        return rep
    try:
        with open(root / MANIFEST, encoding="utf-8") as f:
            m = json.load(f)
    except FileNotFoundError:
        rep.errors.append(f"no {MANIFEST}")
        return rep
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        rep.errors.append(f"{MANIFEST} is not valid JSON: {e}")
        return rep

    for err in sorted(jsonschema.Draft202012Validator(schema()).iter_errors(m), key=lambda e: list(e.path)):
        where = "/".join(str(p) for p in err.absolute_path) or "(top)"
        rep.errors.append(f"{MANIFEST} {where}: {err.message}")
    if rep.errors:
        return rep  # the checks below assume the shape is right

    expected_name = m["id"] + (PARTIAL_SUFFIX if root.name.endswith(PARTIAL_SUFFIX) else "")
    if root.name != expected_name:
        rep.errors.append(f"directory is named {root.name!r} but the bundle id is {m['id']!r}")

    try:
        created = datetime.fromisoformat(m["created_at"])
        if created.tzinfo is None:
            rep.errors.append("created_at has no UTC offset")
    except ValueError:
        rep.errors.append(f"created_at is not a valid timestamp: {m['created_at']!r}")

    date = m["describe"]["item"].get("date")
    if date is not None:
        try:
            edtf.parse(date)
        except edtf.EdtfError as e:
            rep.errors.append(f"item date: {e}")

    _check_pages(m, rep)
    _check_files(root, m, rep)

    for kind, known, value in (("collection", collections, m["describe"]["collection"]),
                               ("series", series, m["describe"]["series"]),
                               ("type", types, m["describe"]["item"].get("type"))):
        if known is not None and value is not None and value not in known:
            rep.warnings.append(f"unknown {kind} {value!r}; the library will file the item under unsorted")
    return rep


def _check_pages(m: dict, rep: Report) -> None:
    pages = m["pages"]
    numbers = [p["n"] for p in pages]
    if numbers != list(range(1, len(pages) + 1)):
        rep.errors.append(f"pages must be numbered 1..{len(pages)} in order, got {numbers}")
    for p in pages:
        for key in ("raw", "page", "quick"):
            rel = p.get(key)
            if rel is None:
                continue
            num = _NUMBERED.match(rel)
            if num and int(num.group(2)) != p["n"]:
                rep.errors.append(f"page {p['n']}: {key} file {rel!r} has the wrong number")


def _check_files(root: Path, m: dict, rep: Report) -> None:
    listed = m["files"]
    referenced = {p[k] for p in m["pages"] for k in ("raw", "page", "quick") if p.get(k)}
    referenced |= {m[k] for k in ("slip", "quality") if m.get(k)}
    for rel in sorted(referenced - listed.keys()):
        rep.errors.append(f"{rel} is referenced but has no checksum in files")
    for rel in sorted(listed.keys() - referenced):
        rep.errors.append(f"{rel} is in files but nothing in the manifest refers to it")

    links = sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_symlink())
    for rel in links:
        rep.errors.append(f"{rel} is a symbolic link; a bundle holds real files only")
    if links:
        return  # don't hash through links to files outside the bundle

    on_disk = _files_on_disk(root) - {MANIFEST}
    for rel in sorted(listed.keys() - on_disk):
        rep.errors.append(f"missing file: {rel}")
    for rel in sorted(on_disk - listed.keys()):
        rep.errors.append(f"unexpected file: {rel}")

    for rel in sorted(listed.keys() & on_disk):
        f = root / rel
        want = listed[rel]
        size = f.stat().st_size
        if size != want["bytes"]:
            rep.errors.append(f"{rel}: size {size} bytes, manifest says {want['bytes']}")
        elif sha256_file(f) != want["sha256"]:
            rep.errors.append(f"{rel}: sha256 doesn't match the manifest (file changed or corrupted)")
        elif rel.endswith(".json"):
            try:
                data = json.loads(f.read_text("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                rep.errors.append(f"{rel}: not valid JSON")
                continue
            if not isinstance(data, (dict, list)):
                rep.errors.append(f"{rel}: expected a JSON object or array")
