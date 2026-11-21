# SPDX-License-Identifier: GPL-3.0-or-later
"""The file store: content-addressed, write-once originals, and regenerable derivatives.

    <files>/originals/<sha256[:2]>/<sha256>.<ext>       never edited, never deleted by software
    <files>/derivatives/<kind>/<sha256[:2]>/<sha256>.<ext>

A small interface (put, path_for, exists, open) so an S3-compatible store can replace the filesystem on a
multi-node cluster later (ADR 0001). There is deliberately no delete or overwrite operation.
"""
from __future__ import annotations

import hashlib
import os
import re
import tempfile
from pathlib import Path

_EXT = re.compile(r"^[a-z0-9]{1,8}$")
_SHA = re.compile(r"^[0-9a-f]{64}$")


class StoreError(Exception):
    pass


class FileStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)

    # -- paths -----------------------------------------------------------------------------------------------
    def _path(self, area: str, sha256: str, ext: str) -> Path:
        if not _SHA.match(sha256):
            raise StoreError(f"not a sha256: {sha256!r}")
        if not _EXT.match(ext):
            raise StoreError(f"bad extension: {ext!r}")
        return self.root / area / sha256[:2] / f"{sha256}.{ext}"

    def path_for(self, sha256: str, ext: str) -> Path:
        return self._path("originals", sha256, ext)

    def derivative_path(self, kind: str, sha256: str, ext: str) -> Path:
        if not _EXT.match(kind):
            raise StoreError(f"bad derivative kind: {kind!r}")
        return self._path(f"derivatives/{kind}", sha256, ext)

    def exists(self, sha256: str, ext: str) -> bool:
        return self.path_for(sha256, ext).is_file()

    def open(self, sha256: str, ext: str):
        return open(self.path_for(sha256, ext), "rb")

    # -- writing ---------------------------------------------------------------------------------------------
    def put(self, src: str | Path, ext: str, expect_sha256: str | None = None) -> str:
        """Copy ``src`` into the originals, write-once. Returns its sha256. Storing the same content twice is a
        no-op; a hash mismatch with ``expect_sha256`` stores nothing and raises."""
        return self._put(Path(src), "originals", ext, expect_sha256, read_only=True)

    def put_derivative(self, kind: str, data: bytes, ext: str) -> str:
        """Store regenerable derived bytes (e.g. an OCR-sized JPEG) by their own sha256."""
        sha = hashlib.sha256(data).hexdigest()
        dest = self.derivative_path(kind, sha, ext)
        if not dest.exists():
            self._atomic_write(dest, lambda f: f.write(data), read_only=False)
        return sha

    def _put(self, src: Path, area: str, ext: str, expect: str | None, read_only: bool) -> str:
        h = hashlib.sha256()
        dest_dir = self.root / area
        dest_dir.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=dest_dir, prefix=".incoming-")
        try:
            with os.fdopen(fd, "wb") as out, open(src, "rb") as inp:
                for chunk in iter(lambda: inp.read(1 << 20), b""):
                    h.update(chunk)
                    out.write(chunk)
                out.flush()
                os.fsync(out.fileno())
            sha = h.hexdigest()
            if expect is not None and sha != expect:
                raise StoreError(f"{src}: sha256 {sha} doesn't match the expected {expect}")
            dest = self._path(area, sha, ext)
            dest.parent.mkdir(parents=True, exist_ok=True)
            if read_only:
                os.chmod(tmp, 0o444)
            try:
                os.link(tmp, dest)          # atomic and never overwrites: fails if dest exists
            except FileExistsError:
                pass                        # same content already stored
            _fsync_dir(dest.parent)
            return sha
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def _atomic_write(self, dest: Path, write, read_only: bool) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=dest.parent, prefix=".incoming-")
        try:
            with os.fdopen(fd, "wb") as out:
                write(out)
                out.flush()
                os.fsync(out.fileno())
            if read_only:
                os.chmod(tmp, 0o444)
            try:
                os.link(tmp, dest)
            except FileExistsError:
                pass
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)


def _fsync_dir(path: Path) -> None:
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)
