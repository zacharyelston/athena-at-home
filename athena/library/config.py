# SPDX-License-Identifier: GPL-3.0-or-later
"""A library instance's configuration: one TOML file plus environment variables, nothing else.

A library is a **named instance** with its own database, data directory, intake directory and NATS account. Nothing
here defaults to a household's or a club's name: the name comes from the config file, and every path and address
from the config file or the environment (environment wins, so a container or a launchd plist can override).

    ATHENA_CONFIG            path to library.toml (default ./library.toml)
    ATHENA_DATABASE_URL      PostgreSQL connection string (libpq URL or key=value form)
    ATHENA_FILES             data directory: originals/ and derivatives/
    ATHENA_INTAKE            directory capture stations drop bundles into
    ATHENA_NATS_URL          site NATS (optional; without it, events stay in the outbox)
    ATHENA_NATS_CREDS        nsc .creds file for this library's account
    ATHENA_NATS_TLS_HOSTNAME
    ATHENA_SIEVE_SUBJECT     ocr.page by default
"""
from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from athena.visibility.core import Feed, Policy, PolicyError

SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Collection:
    slug: str
    title: str
    description: str = ""
    sort: int = 0


@dataclass(frozen=True)
class Series:
    slug: str
    collection: str
    title: str
    description: str = ""
    sort: int = 0


@dataclass(frozen=True)
class LibraryConfig:
    name: str
    database_url: str
    files: Path
    intake: Path
    collections: tuple[Collection, ...] = ()
    series: tuple[Series, ...] = ()
    types: tuple[str, ...] = ()
    nats_url: str | None = None
    nats_creds: str | None = None
    nats_tls_hostname: str | None = None
    sieve_subject: str = "ocr.page"
    ocr_skip_kinds: tuple[str, ...] = ("photo", "blank")
    ocr_derivative_ppi: int = 200
    ocr_max_edge: int = 2400       # cap for pages whose ppi is unknown (Letter at ~200 ppi is 1700 × 2200)
    policies: tuple[Policy, ...] = ()
    feeds: tuple[Feed, ...] = ()
    source_path: Path | None = field(default=None, compare=False)

    @property
    def subject_prefix(self) -> str:
        return f"library.{self.name}"

    def series_slugs(self) -> set[str]:
        return {s.slug for s in self.series}

    def collection_slugs(self) -> set[str]:
        return {c.slug for c in self.collections}


def _slug(value, what: str) -> str:
    if not isinstance(value, str) or not SLUG.match(value):
        raise ConfigError(f"{what} must be a slug (lowercase letters, digits, hyphens): {value!r}")
    return value


def load(path: str | Path | None = None, env: dict | None = None) -> LibraryConfig:
    env = os.environ if env is None else env
    path = Path(path or env.get("ATHENA_CONFIG") or "library.toml")
    try:
        with open(path, "rb") as f:
            raw = tomllib.load(f)
    except FileNotFoundError:
        raise ConfigError(f"no config file at {path} (set ATHENA_CONFIG)") from None
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}: {e}") from None
    return from_dict(raw, env, source_path=path)


def from_dict(raw: dict, env: dict | None = None, source_path: Path | None = None) -> LibraryConfig:
    env = os.environ if env is None else env
    lib = raw.get("library") or {}
    unknown = set(raw) - {"library", "collection", "series", "vocabulary", "ocr", "nats", "policy", "feed"}
    if unknown:
        raise ConfigError(f"unknown config sections: {sorted(unknown)}")
    name = _slug(lib.get("name"), "library.name")

    def setting(key: str, env_key: str, section: dict = lib, required: bool = False):
        value = env.get(env_key) or section.get(key)
        if required and not value:
            raise ConfigError(f"set {env_key} or {key} in the config")
        return value

    database_url = setting("database_url", "ATHENA_DATABASE_URL", required=True)
    files = Path(setting("files", "ATHENA_FILES", required=True)).expanduser()
    intake = Path(setting("intake", "ATHENA_INTAKE", required=True)).expanduser()
    nats = raw.get("nats") or {}

    collections = tuple(Collection(_slug(c.get("slug"), "collection.slug"), c.get("title") or c["slug"],
                                   c.get("description", ""), int(c.get("sort", 0)))
                        for c in raw.get("collection", []))
    known = {c.slug for c in collections}
    series = []
    for s in raw.get("series", []):
        slug = _slug(s.get("slug"), "series.slug")
        coll = _slug(s.get("collection"), f"series {slug}: collection")
        if coll not in known:
            raise ConfigError(f"series {slug!r} names unknown collection {coll!r}")
        series.append(Series(slug, coll, s.get("title") or slug, s.get("description", ""), int(s.get("sort", 0))))
    if len({s.slug for s in series}) != len(series):
        raise ConfigError("series slugs must be unique")
    types = tuple(_slug(t, "vocabulary.types entry") for t in (raw.get("vocabulary") or {}).get("types", []))
    ocr = raw.get("ocr") or {}
    try:
        policies = tuple(Policy.from_dict(p) for p in raw.get("policy", []))
        feeds = tuple(Feed.from_dict(f) for f in raw.get("feed", []))
    except PolicyError as e:
        raise ConfigError(str(e)) from None
    for what, names in (("policy", [p.name for p in policies]), ("feed", [f.name for f in feeds])):
        if len(set(names)) != len(names):
            raise ConfigError(f"{what} names must be unique")
    return LibraryConfig(
        name=name, database_url=database_url, files=files, intake=intake,
        collections=collections, series=tuple(series), types=types,
        nats_url=setting("url", "ATHENA_NATS_URL", nats), nats_creds=setting("creds", "ATHENA_NATS_CREDS", nats),
        nats_tls_hostname=setting("tls_hostname", "ATHENA_NATS_TLS_HOSTNAME", nats),
        sieve_subject=setting("sieve_subject", "ATHENA_SIEVE_SUBJECT", nats) or "ocr.page",
        ocr_skip_kinds=tuple(ocr.get("skip_kinds", ("photo", "blank"))),
        ocr_derivative_ppi=int(ocr.get("derivative_ppi", 200)),
        ocr_max_edge=int(ocr.get("max_edge", 2400)),
        policies=policies, feeds=feeds,
        source_path=source_path,
    )
