#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Guard against committing real records, captures, databases or secrets.

    scripts/check_no_records.py            check all tracked and new (not ignored) files
    scripts/check_no_records.py FILE...    check these files (for a pre-commit hook)

Fails on:
  - images, PDFs, raw camera files and databases outside the synthetic-fixture paths;
  - any image, anywhere, whose EXIF has GPS or a camera make or model (a real capture; synthetic images have none);
  - any file over 2 MB;
  - files inside data directories (captures/, originals/, scans/, intake/);
  - secrets: NATS creds and keys, private keys, .env files.
Exit 1 with one line per problem.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path, PurePosixPath

MAX_BYTES = 2 * 1024 * 1024
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".heic", ".heif", ".dng", ".raw",
             ".cr2", ".nef", ".arw"}
DOC_EXT = {".pdf"}
DB_EXT = {".sqlite", ".sqlite3", ".db"}
SECRET_EXT = {".creds", ".nk", ".pem", ".key", ".p12", ".pfx"}
SECRET_NAMES = {".env"}
DATA_DIRS = {"captures", "originals", "scans", "intake"}
# Synthetic images may live here (generated fixtures, screenshots of fixture data for the guides).
FIXTURE_PREFIXES = ("tests/fixtures/", "docs/guides/img/")
EXIF_GPS_IFD, EXIF_MAKE, EXIF_MODEL = 0x8825, 0x010F, 0x0110


def exif_problem(path: Path) -> str | None:
    """'GPS' or 'camera make/model' if the image carries them; None if clean; a message if it can't be read."""
    try:
        from PIL import Image
    except ImportError:
        return "can't inspect EXIF: install Pillow"
    try:
        with Image.open(path) as im:
            exif = im.getexif()
    except Exception as e:  # unreadable image formats (HEIC without a plugin, DNG) can't be shown to be synthetic
        return f"can't inspect EXIF ({type(e).__name__}); real camera formats aren't allowed"
    found = []
    if exif.get(EXIF_GPS_IFD) is not None or exif.get_ifd(EXIF_GPS_IFD):
        found.append("GPS location")
    if exif.get(EXIF_MAKE) or exif.get(EXIF_MODEL):
        found.append("camera make/model")
    return " and ".join(found) or None


def problems(root: Path, rel: str) -> list[str]:
    path = root / rel
    p = PurePosixPath(rel)
    ext = p.suffix.lower()
    out = []
    if p.name in SECRET_NAMES or ext in SECRET_EXT:
        if not p.name.endswith((".example", ".sample")):
            out.append("looks like a secret (credentials, key or .env); keep it out of git")
    if DATA_DIRS.intersection(part.lower() for part in p.parts[:-1]) and not rel.startswith(FIXTURE_PREFIXES):
        out.append(f"inside a data directory ({'/'.join(sorted(DATA_DIRS))}); real records never go in git")
    fixture = rel.startswith(FIXTURE_PREFIXES)
    if ext in IMAGE_EXT | DOC_EXT | DB_EXT and not fixture:
        out.append(f"{ext} file outside {' or '.join(FIXTURE_PREFIXES)}; only synthetic fixtures may be committed")
    if ext in IMAGE_EXT and path.is_file():
        found = exif_problem(path)
        if found:
            out.append(f"image metadata shows a real capture: {found}")
    if path.is_file() and path.stat().st_size > MAX_BYTES:
        out.append(f"{path.stat().st_size / 1e6:.1f} MB is over the {MAX_BYTES // (1024 * 1024)} MB limit")
    return out


def repo_files(root: Path) -> list[str]:
    res = subprocess.run(["git", "ls-files", "-co", "--exclude-standard"], cwd=root, capture_output=True,
                         text=True, check=True)
    return [f for f in res.stdout.splitlines() if f]


def check(root: Path, paths: list[str]) -> list[str]:
    report = []
    for rel in paths:
        if (root / rel).is_file():
            report += [f"{rel}: {p}" for p in problems(root, rel)]
    return report


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    root = Path.cwd()
    report = check(root, argv or repo_files(root))
    for line in report:
        print(line)
    if report:
        print(f"\n{len(report)} problem(s). Real records, captures and secrets stay on the library's encrypted disk; "
              "tests use synthetic fixtures.", file=sys.stderr)
    return 1 if report else 0


if __name__ == "__main__":
    sys.exit(main())
