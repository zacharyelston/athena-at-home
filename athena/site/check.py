# SPDX-License-Identifier: GPL-3.0-or-later
"""Checking a built site before it is copied to a host.

``check_site`` looks at the site directory alone, and, when given the feed it was built from, at both:

- every page carries the Content-Security-Policy that confines it to its own host, and nothing but an ordinary link
  (``<a href>``) points anywhere else: no script, stylesheet, image, font, frame or form;
- every local link, image, script and stylesheet resolves, and every ``#fragment`` exists;
- a site that mustn't be indexed says so on every page and in robots.txt;
- accessibility basics a static check can see: a language, one main heading, alt text, labelled fields;
- no image carries metadata, and no forbidden string appears anywhere, including inside Pagefind's compressed
  index (the publisher's scanner decompresses it);
- with the feed: every image is byte-for-byte a feed image, and every item page is an item of that feed. The feed
  is what ``athena publish check`` verified against the library, so this ties the site to that verification.
"""
from __future__ import annotations

import filecmp
import json
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .build import CSP, FEED_MARKER, MARKER

URL_ATTRS = {"src", "href", "action", "poster", "data", "srcset", "formaction", "background", "cite", "manifest"}
CSS_URL = re.compile(r"""url\(\s*['"]?([^'")]+)['"]?\s*\)|@import\s+['"]([^'"]+)['"]""")
NOINDEX = re.compile(r"\bnoindex\b")


class _Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.refs: list[tuple[str, str, str]] = []      # (tag, attribute, value)
        self.ids: set[str] = set()
        self.csp: list[str] = []
        self.robots: list[str] = []
        self.lang: str | None = None
        self.h1 = 0
        self.problems: list[str] = []
        self.labels: set[str] = set()
        self.fields: list[dict] = []
        self._script: dict | None = None

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        if "id" in a:
            self.ids.add(a["id"])
        if tag == "html":
            self.lang = a.get("lang")
        elif tag == "h1":
            self.h1 += 1
        elif tag == "meta":
            if a.get("http-equiv", "").lower() == "content-security-policy":
                self.csp.append(a.get("content", ""))
            if a.get("name", "").lower() == "robots":
                self.robots.append(a.get("content", ""))
            if a.get("http-equiv", "").lower() == "refresh":
                self.problems.append("a meta refresh")
        elif tag == "img" and "alt" not in a:
            self.problems.append(f"an image without alt text ({a.get('src', '')})")
        elif tag == "label" and a.get("for"):
            self.labels.add(a["for"])
        elif tag in ("input", "select", "textarea") and a.get("type") not in ("hidden", "submit", "button"):
            self.fields.append(a)
        elif tag == "script":
            self._script = a
            if not a.get("src") and a.get("type") != "application/json":
                self.problems.append("an inline script")
        elif tag in ("iframe", "object", "embed", "base"):
            self.problems.append(f"a <{tag}> element")
        for k, v in a.items():
            if k in URL_ATTRS and v:
                for part in (v.split(",") if k == "srcset" else [v]):
                    self.refs.append((tag, k, part.strip().split(" ")[0]))
            elif k.startswith("on"):
                self.problems.append(f"an inline event handler ({k})")
            elif k == "style" and "url(" in v:
                self.problems.append("an inline style that loads something")

    handle_startendtag = handle_starttag


def _resolve(site: Path, page_dir: Path, url: str) -> Path | None:
    """The file a local URL points at, or None when it leaves the site."""
    path = unquote(urlsplit(url).path)
    target = (site / path.lstrip("/")) if path.startswith("/") else (page_dir / path)
    target = Path(*target.parts)
    try:
        resolved = target.resolve()
        resolved.relative_to(site.resolve())
    except ValueError:
        return None
    return resolved / "index.html" if resolved.is_dir() else resolved


def _is_external(url: str) -> bool:
    return bool(urlsplit(url).scheme) or url.startswith("//")


def check_site(site: str | Path, feed_dir: str | Path | None = None, forbidden=()) -> list[str]:
    """Problems found in the site at ``site`` (empty = clean)."""
    site = Path(site)
    if not (site / MARKER).is_file():
        return [f"{site} is not a site directory (no {MARKER})"]
    marker = json.loads((site / MARKER).read_text("utf-8"))
    problems: list[str] = []
    pages: dict[Path, _Page] = {}
    html_files = sorted(site.rglob("*.html"))
    for f in html_files:
        p = _Page()
        p.feed(f.read_text("utf-8"))
        pages[f.resolve()] = p

    for f in html_files:
        rel = f.relative_to(site).as_posix()
        p = pages[f.resolve()]
        problems += [f"{rel}: has {what}" for what in p.problems]
        if p.csp != [CSP]:
            problems.append(f"{rel}: doesn't carry the expected Content-Security-Policy")
        if not p.lang:
            problems.append(f"{rel}: the page has no language")
        if p.h1 != 1:
            problems.append(f"{rel}: has {p.h1} main headings, expected one")
        for a in p.fields:
            if a.get("id") not in p.labels and not a.get("aria-label"):
                problems.append(f"{rel}: a form field without a label")
        if not marker.get("indexable") and not any(NOINDEX.search(r) for r in p.robots):
            problems.append(f"{rel}: must not be indexed, but has no noindex")
        for tag, attr, url in p.refs:
            if url.startswith("#"):
                if url[1:] and unquote(url[1:]) not in p.ids:
                    problems.append(f"{rel}: link to {url}, which isn't on the page")
                continue
            if _is_external(url):
                scheme = urlsplit(url).scheme
                if tag == "a" and attr == "href" and scheme in ("http", "https", "mailto"):
                    continue                    # an ordinary link: nothing is loaded until someone follows it
                if scheme == "data" and tag == "img":
                    continue
                problems.append(f"{rel}: <{tag} {attr}> points outside the site: {url[:80]}")
                continue
            if rel == "404.html":
                # Served at any address, so its links start at the site's path on its host (site.json: root_path).
                root_path = marker.get("root_path") or "/"
                if not url.startswith(root_path):
                    problems.append(f"{rel}: link {url} doesn't start at the site's root {root_path}")
                    continue
                target = _resolve(site, site, url[len(root_path):] or ".")
            else:
                target = _resolve(site, f.parent, url)
            if target is None or not target.is_file():
                problems.append(f"{rel}: broken link {url}")
            elif urlsplit(url).fragment and target.suffix == ".html":
                other = pages.get(target)
                if other is not None and unquote(urlsplit(url).fragment) not in other.ids:
                    problems.append(f"{rel}: link to {url}, but that page has no such part")

    for f in sorted(site.rglob("*.css")):
        rel = f.relative_to(site).as_posix()
        for m in CSS_URL.finditer(f.read_text("utf-8", errors="replace")):
            url = (m.group(1) or m.group(2)).strip()
            if url.startswith("data:") or url.startswith("#"):
                continue
            if _is_external(url):
                problems.append(f"{rel}: loads {url[:80]} from outside the site")
            else:
                target = _resolve(site, f.parent, url)
                if target is None or not target.is_file():
                    problems.append(f"{rel}: broken url({url})")

    robots = (site / "robots.txt").read_text("utf-8") if (site / "robots.txt").is_file() else ""
    if not marker.get("indexable") and not re.search(r"(?m)^Disallow:\s*/\s*$", robots):
        problems.append("robots.txt: the site must not be indexed, but robots.txt doesn't disallow everything")
    if marker.get("indexable") and marker.get("tiers") != ["public"]:
        problems.append(f"{MARKER}: a site carrying {marker.get('tiers')} is marked indexable")

    if feed_dir is not None:
        problems += _against_feed(site, Path(feed_dir), marker)

    from athena.publish.leakcheck import check_images, scan_for_strings
    problems += check_images(site)
    problems += scan_for_strings(site, forbidden)
    return problems


def _against_feed(site: Path, feed_dir: Path, marker: dict) -> list[str]:
    if not (feed_dir / FEED_MARKER).is_file():
        return [f"{feed_dir} is not a feed directory (no {FEED_MARKER})"]
    feed = json.loads((feed_dir / FEED_MARKER).read_text("utf-8"))
    problems = []
    if feed.get("feed") != marker.get("feed") or feed.get("tiers") != marker.get("tiers"):
        problems.append(f"the site was built from feed {marker.get('feed')!r} {marker.get('tiers')}, "
                        f"not from {feed.get('feed')!r} {feed.get('tiers')}")
    if feed.get("built_at") != marker.get("feed_built_at"):
        problems.append("the feed has been rebuilt since this site was built; build the site again")
    for f in sorted((site / "images").rglob("*")) if (site / "images").is_dir() else []:
        if not f.is_file():
            continue
        rel = f.relative_to(site).as_posix()
        src = feed_dir / rel
        if not src.is_file():
            problems.append(f"{rel}: an image that isn't in the feed")
        elif not filecmp.cmp(f, src, shallow=False):
            problems.append(f"{rel}: differs from the feed's image")
    for d in sorted((site / "item").iterdir()) if (site / "item").is_dir() else []:
        if not (feed_dir / "items" / f"{d.name}.json").is_file():
            problems.append(f"item/{d.name}/: an item that isn't in the feed")
    return problems
