# SPDX-License-Identifier: GPL-3.0-or-later
"""Site settings: the ``[site.<name>]`` sections of a library's config file. Everything a visitor reads that isn't a
record comes from here (title, about text, contact, colours, logo); nothing about any deployment is in the code.

    [site.public]
    feed = "public"                    # the feed this site is built from (default: the section name)
    title = "Riverside Garden Society Archive"
    tagline = "Newsletters, minutes and photographs since 1931"
    about = '''One or more paragraphs for the home page. A blank line starts a new paragraph.'''
    about_more = '''Paragraphs for the "About this archive" page.'''
    contact = "archive@example.org"    # an email address or a URL: where to ask for a removal or a correction
    base_url = "https://archive.example.org/"   # optional: used for citations, the sitemap and the 404 page
    index = false                      # may search engines index it? Off unless switched on, and only ever
                                       # honoured for a feed that carries the public tier alone
    preview = false                    # show a "preview" banner (and never allow indexing)
    lang = "en"
    logo = "logo.svg"                  # optional, relative to the config file
    featured = ["3f9a…"]               # optional: item ids as they appear in the site's addresses

    [site.public.colors]               # optional; contrast against the background is checked (WCAG AA, 4.5:1)
    accent = "#2c5f2d"
    background = "#fbfaf7"
    text = "#1f2421"
"""
from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
COLOR = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
LANG = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")
LOGO_EXT = {".svg", ".png", ".jpg", ".jpeg", ".webp"}
DEFAULT_COLORS = {"accent": "#2c5f2d", "background": "#fbfaf7", "text": "#1f2421"}
MIN_CONTRAST = 4.5
KEYS = {"feed", "title", "tagline", "about", "about_more", "contact", "base_url", "index", "preview", "lang",
        "logo", "featured", "colors"}


class SiteConfigError(ValueError):
    pass


@dataclass(frozen=True)
class SiteConfig:
    name: str
    feed: str
    title: str
    tagline: str = ""
    about: tuple[str, ...] = ()            # paragraphs
    about_more: tuple[str, ...] = ()
    contact: str = ""
    base_url: str = ""                     # "" or an absolute URL ending in "/"
    index: bool = False
    preview: bool = False
    lang: str = "en"
    logo: Path | None = None
    featured: tuple[str, ...] = ()
    colors: dict = field(default_factory=lambda: dict(DEFAULT_COLORS))

    @property
    def contact_href(self) -> str:
        if not self.contact:
            return ""
        return self.contact if self.contact.startswith(("https://", "http://")) else f"mailto:{self.contact}"


def _paragraphs(text) -> tuple[str, ...]:
    if not isinstance(text, str):
        raise SiteConfigError("about text must be a string")
    return tuple(" ".join(p.split()) for p in re.split(r"\n\s*\n", text) if p.strip())


def _luminance(color: str) -> float:
    h = color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    chan = []
    for i in (0, 2, 4):
        c = int(h[i:i + 2], 16) / 255
        chan.append(c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4)
    return 0.2126 * chan[0] + 0.7152 * chan[1] + 0.0722 * chan[2]


def contrast(a: str, b: str) -> float:
    """WCAG contrast ratio between two #rgb or #rrggbb colours (1 to 21)."""
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def from_dict(name: str, d: dict, base_dir: Path | None = None) -> SiteConfig:
    where = f"[site.{name}]"
    if not NAME.match(name):
        raise SiteConfigError(f"{where}: the site name must be a slug (lowercase letters, digits, hyphens)")
    if not isinstance(d, dict):
        raise SiteConfigError(f"{where} must be a table")
    unknown = set(d) - KEYS
    if unknown:
        raise SiteConfigError(f"{where}: unknown keys {sorted(unknown)}")
    title = d.get("title")
    if not isinstance(title, str) or not title.strip():
        raise SiteConfigError(f"{where}: title is required")
    feed = d.get("feed", name)
    if not isinstance(feed, str) or not NAME.match(feed):
        raise SiteConfigError(f"{where}: feed must be a feed name")
    for key in ("index", "preview"):
        if not isinstance(d.get(key, False), bool):
            raise SiteConfigError(f"{where}: {key} must be true or false")

    contact = str(d.get("contact", "")).strip()
    if contact and not (contact.startswith(("https://", "http://")) or re.fullmatch(r"[^@\s<>\"']+@[^@\s<>\"']+", contact)):
        raise SiteConfigError(f"{where}: contact must be an email address or an http(s) URL")
    base_url = str(d.get("base_url", "")).strip()
    if base_url:
        if not re.fullmatch(r"https?://[^\s<>\"']+", base_url):
            raise SiteConfigError(f"{where}: base_url must be an http(s) URL")
        base_url = base_url.rstrip("/") + "/"
    lang = str(d.get("lang", "en"))
    if not LANG.match(lang):
        raise SiteConfigError(f"{where}: lang must be a language tag such as \"en\"")

    colors = dict(DEFAULT_COLORS)
    given = d.get("colors", {})
    if not isinstance(given, dict) or set(given) - set(DEFAULT_COLORS):
        raise SiteConfigError(f"{where}: colors may set {sorted(DEFAULT_COLORS)}")
    for key, value in given.items():
        if not isinstance(value, str) or not COLOR.match(value):
            raise SiteConfigError(f"{where}: colors.{key} must look like #2c5f2d")
        colors[key] = value
    for key in ("text", "accent"):
        ratio = contrast(colors[key], colors["background"])
        if ratio < MIN_CONTRAST:
            raise SiteConfigError(f"{where}: colors.{key} on the background has contrast {ratio:.1f}:1; "
                                  f"text needs at least {MIN_CONTRAST}:1 to be readable")

    logo = None
    if d.get("logo"):
        logo = Path(str(d["logo"])).expanduser()
        if not logo.is_absolute() and base_dir is not None:
            logo = base_dir / logo
        if logo.suffix.lower() not in LOGO_EXT:
            raise SiteConfigError(f"{where}: logo must be one of {sorted(LOGO_EXT)}")
    featured = d.get("featured", [])
    if not isinstance(featured, list) or not all(isinstance(f, str) and re.fullmatch(r"[0-9a-f]{8,64}", f)
                                                 for f in featured):
        raise SiteConfigError(f"{where}: featured is a list of item ids as they appear in the site's addresses")

    return SiteConfig(name=name, feed=feed, title=title.strip(), tagline=str(d.get("tagline", "")).strip(),
                      about=_paragraphs(d.get("about", "")), about_more=_paragraphs(d.get("about_more", "")),
                      contact=contact, base_url=base_url, index=d.get("index", False),
                      preview=d.get("preview", False), lang=lang, logo=logo, featured=tuple(featured),
                      colors=colors)


def sites_from_dict(raw, base_dir: Path | None = None) -> dict[str, SiteConfig]:
    """All ``[site.*]`` sections of a parsed config file."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise SiteConfigError("[site] holds one table per site, such as [site.public]")
    return {name: from_dict(name, d, base_dir) for name, d in raw.items()}


def load_site(path: str | Path, name: str) -> SiteConfig:
    """The ``[site.<name>]`` section of the config file at ``path``. Needs no database and no environment."""
    path = Path(path)
    try:
        with open(path, "rb") as f:
            raw = tomllib.load(f)
    except FileNotFoundError:
        raise SiteConfigError(f"no config file at {path}") from None
    except tomllib.TOMLDecodeError as e:
        raise SiteConfigError(f"{path}: {e}") from None
    sites = raw.get("site") or {}
    if name not in sites:
        raise SiteConfigError(f"{path} has no [site.{name}] section (sites: {sorted(sites)})")
    return from_dict(name, sites[name], path.parent)
