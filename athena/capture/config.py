# SPDX-License-Identifier: GPL-3.0-or-later
"""A capture station's settings: the ``[capture]`` section of the library's config file, plus the environment.

A station may sit on the library's own machine or on another one, so this reads only what capture needs and never
asks for the database. Every setting has a default except where bundles go; nothing here names a household or a
club.

    ATHENA_CONFIG            path to the config file (default ./library.toml; optional for capture)
    ATHENA_CAPTURE_STAGING   where imported bundles wait until they're sent
    ATHENA_INTAKE            the library's intake directory, where ``athena capture send`` puts them
"""
from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, fields, replace
from pathlib import Path

SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
UNSORTED = "unsorted"


class CaptureConfigError(ValueError):
    pass


@dataclass(frozen=True)
class CaptureConfig:
    station: str = "capture"            # a slug naming the station, never a person
    operator: str | None = None         # the station login, a slug
    staging: Path | None = None
    intake: Path | None = None
    collection: str | None = None       # default: the library's only collection
    series: str = UNSORTED              # for items no folder name or option places
    # What the printed scale card measures (see `athena capture sheets`).
    tag_mm: float = 50.0
    print_scale: float = 1.0            # if the 100 mm ruler on the sheet measures 97 mm, set 0.97
    # Re-shoot thresholds. Glare, evenness and brightness start from the targets tuned in the author's OCRX; the
    # rest are starting points. A pilot on real material should set them all.
    min_ppi: float = 400.0
    min_ppi_photo: float = 600.0
    min_sharpness: float = 1.0
    min_sharpness_photo: float = 0.15
    max_glare: float = 0.01
    min_brightness: float = 140.0
    min_evenness: float = 0.75
    max_keystone: float = 0.08
    jpeg_quality: int = 95
    quick: bool = True                  # Apple Vision text on a Mac, for instant search (tier 0)
    # From the library's sections of the same file, for folder names and the describe form.
    known_collections: tuple[str, ...] = ()
    known_series: tuple[tuple[str, str, str], ...] = ()     # (slug, title, collection)
    types: tuple[str, ...] = ()

    @property
    def tag_inches(self) -> float:
        return self.tag_mm * self.print_scale / 25.4

    def series_slugs(self) -> set[str]:
        return {s[0] for s in self.known_series}

    def collection_for(self, series: str) -> str | None:
        for slug, _, collection in self.known_series:
            if slug == series:
                return collection
        return self.collection


_OWN = {"station", "operator", "staging", "collection", "series", "tag_mm", "print_scale", "min_ppi",
        "min_ppi_photo", "min_sharpness", "min_sharpness_photo", "max_glare", "min_brightness", "min_evenness", "max_keystone",
        "jpeg_quality", "quick"}
_NUMBERS = {"tag_mm": (5, 300), "print_scale": (0.5, 1.5), "min_ppi": (0, 4800), "min_ppi_photo": (0, 4800),
            "min_sharpness": (0, 100), "min_sharpness_photo": (0, 100), "max_glare": (0, 1), "min_brightness": (0, 255), "min_evenness": (0, 1),
            "max_keystone": (0, 1)}


def _slug(value, what: str) -> str:
    if not isinstance(value, str) or not SLUG.match(value):
        raise CaptureConfigError(f"{what} must be a slug (lowercase letters, digits, hyphens): {value!r}")
    return value


def section_from_dict(d: dict) -> dict:
    """Check a ``[capture]`` table and return its settings as keyword arguments for CaptureConfig."""
    if not isinstance(d, dict):
        raise CaptureConfigError("[capture] must be a table")
    unknown = set(d) - _OWN
    if unknown:
        raise CaptureConfigError(f"[capture]: unknown keys {sorted(unknown)}")
    out: dict = {}
    for key in ("station", "operator", "collection", "series"):
        if d.get(key) is not None:
            out[key] = _slug(d[key], f"[capture] {key}")
    if d.get("staging"):
        out["staging"] = Path(str(d["staging"])).expanduser()
    for key, (lo, hi) in _NUMBERS.items():
        if key in d:
            v = d[key]
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not lo <= v <= hi:
                raise CaptureConfigError(f"[capture] {key} must be a number from {lo} to {hi}: {v!r}")
            out[key] = float(v)
    if "jpeg_quality" in d:
        q = d["jpeg_quality"]
        if isinstance(q, bool) or not isinstance(q, int) or not 70 <= q <= 100:
            raise CaptureConfigError(f"[capture] jpeg_quality must be a whole number from 70 to 100: {q!r}")
        out["jpeg_quality"] = q
    if "quick" in d:
        if not isinstance(d["quick"], bool):
            raise CaptureConfigError("[capture] quick must be true or false")
        out["quick"] = d["quick"]
    return out


def from_dict(raw: dict, env: dict | None = None) -> CaptureConfig:
    env = os.environ if env is None else env
    kw = section_from_dict(raw.get("capture") or {})
    try:
        collections = tuple(_slug(c.get("slug"), "collection.slug") for c in raw.get("collection", []))
        series = tuple((_slug(s.get("slug"), "series.slug"), str(s.get("title") or s["slug"]),
                        _slug(s.get("collection"), "series.collection")) for s in raw.get("series", []))
        types = tuple(_slug(t, "vocabulary.types entry") for t in (raw.get("vocabulary") or {}).get("types", []))
    except AttributeError:
        raise CaptureConfigError("[[collection]] and [[series]] must be tables") from None
    library = raw.get("library") or {}
    intake = env.get("ATHENA_INTAKE") or library.get("intake")
    staging = env.get("ATHENA_CAPTURE_STAGING")
    cfg = CaptureConfig(**kw, known_collections=collections, known_series=series, types=types)
    if staging:
        cfg = replace(cfg, staging=Path(staging).expanduser())
    if intake:
        cfg = replace(cfg, intake=Path(str(intake)).expanduser())
    if cfg.collection is None and len(collections) == 1:
        cfg = replace(cfg, collection=collections[0])
    return cfg


def load(path: str | Path | None = None, env: dict | None = None) -> CaptureConfig:
    """Read the config file if there is one. Capture works without it (defaults, options and the environment)."""
    env = os.environ if env is None else env
    explicit = path or env.get("ATHENA_CONFIG")
    p = Path(explicit or "library.toml")
    if not p.is_file():
        if explicit:
            raise CaptureConfigError(f"no config file at {p}")
        return from_dict({}, env)
    try:
        with open(p, "rb") as f:
            raw = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise CaptureConfigError(f"{p}: {e}") from None
    return from_dict(raw, env)


def override(cfg: CaptureConfig, **changes) -> CaptureConfig:
    """``cfg`` with the given settings replaced; None means "leave as it is"."""
    names = {f.name for f in fields(CaptureConfig)}
    return replace(cfg, **{k: v for k, v in changes.items() if v is not None and k in names})
