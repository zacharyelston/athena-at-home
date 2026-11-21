# SPDX-License-Identifier: GPL-3.0-or-later
"""Reading a capture bundle: its manifest, as plain data plus a few typed views. See docs/capture-bundle.md."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from importlib import resources
from pathlib import Path

from . import edtf

BUNDLE_VERSION = 1
MANIFEST = "manifest.json"
PARTIAL_SUFFIX = ".partial"
# Files an operating system may drop into a folder; never part of a bundle, ignored when validating.
OS_DEBRIS = (".DS_Store", "Thumbs.db", "desktop.ini")


@lru_cache(maxsize=1)
def schema() -> dict:
    return json.loads(resources.files(__package__).joinpath("capture-bundle-v1.schema.json").read_text("utf-8"))


@dataclass(frozen=True)
class Page:
    n: int
    side: str
    kind: str
    raw: Path            # absolute path of the master
    page: Path           # absolute path of the processed page image
    quick: Path | None
    raw_sha256: str
    page_sha256: str
    width: int
    height: int
    ppi: float | None
    ppi_source: str
    has_gps: bool


@dataclass(frozen=True)
class Bundle:
    path: Path
    manifest: dict

    @property
    def id(self) -> str:
        return self.manifest["id"]

    @property
    def created_at(self) -> datetime:
        return datetime.fromisoformat(self.manifest["created_at"])

    @property
    def collection(self) -> str:
        return self.manifest["describe"]["collection"]

    @property
    def series(self) -> str:
        return self.manifest["describe"]["series"]

    @property
    def item(self) -> dict:
        return self.manifest["describe"]["item"]

    @property
    def date(self) -> edtf.EdtfDate | None:
        """The item's date, or None when undated. Raises EdtfError on a bad date (validate first)."""
        text = self.item.get("date")
        return edtf.parse(text) if text else None

    @property
    def files(self) -> dict[str, dict]:
        return self.manifest["files"]

    @property
    def pages(self) -> list[Page]:
        out = []
        for p in self.manifest["pages"]:
            out.append(Page(
                n=p["n"], side=p["side"], kind=p["kind"],
                raw=self.path / p["raw"], page=self.path / p["page"],
                quick=self.path / p["quick"] if p.get("quick") else None,
                raw_sha256=self.files[p["raw"]]["sha256"], page_sha256=self.files[p["page"]]["sha256"],
                width=p["width"], height=p["height"], ppi=p["ppi"], ppi_source=p["ppi_source"],
                has_gps=p.get("has_gps", False),
            ))
        return out


def read_bundle(path: str | Path) -> Bundle:
    """Load a bundle's manifest without checking files or checksums (``validate`` does that)."""
    path = Path(path)
    with open(path / MANIFEST, encoding="utf-8") as f:
        manifest = json.load(f)
    if not isinstance(manifest, dict):
        raise ValueError(f"{path / MANIFEST}: not a JSON object")
    return Bundle(path, manifest)
