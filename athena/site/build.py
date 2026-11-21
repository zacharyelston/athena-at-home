# SPDX-License-Identifier: GPL-3.0-or-later
"""Build a static site from one feed directory.

    site/
      index.html             title, about text, browse by series and by decade, featured items
      series/<slug>/         one page per series, with thumbnails
      decade/<1960s>/        one page per decade (and "undated")
      item/<id>/             every page image of the item with its text, the description, rights, citation
      search/                Pagefind's search box (the index is built from this build's item pages only)
      about/                 what this is, how it was made, how to ask for a removal or a correction
      images/<id>/           the feed's images, unchanged
      pagefind/              the search index and its code
      static/                CSS, the viewer (OpenSeadragon, vendored), a little JavaScript
      404.html, robots.txt, sitemap.xml (only when indexing is allowed), site.json

The site holds exactly what the feed holds: it is built into a fresh directory and swapped in whole, so nothing
from an earlier build or another feed can linger. Pages load nothing from other sites, set no cookies, and carry a
Content-Security-Policy that makes the browser enforce that.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path
from urllib.parse import quote

from .config import SiteConfig

BUILDER = "athena-site/1"
MARKER = "site.json"
FEED_MARKER = "feed.json"
FEED_VERSION = 1
OPAQUE = re.compile(r"^[0-9a-f]{8,64}$")
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
IMAGE_NAMES = ("thumb", "web", "zoom")
OTHER = "other"                    # the listing for items in no series
# What a page may load: itself. 'wasm-unsafe-eval' lets Pagefind compile its search code; nothing here allows
# another host, so the browser refuses any request to one even if a script or a record tried.
CSP = ("default-src 'self'; img-src 'self' data: blob:; script-src 'self' 'wasm-unsafe-eval'; "
       "style-src 'self' 'unsafe-inline'; object-src 'none'; base-uri 'none'; form-action 'self'")
# Pagefind writes these too; the site uses its default UI only.
PAGEFIND_UNUSED = ("pagefind-modular-ui.js", "pagefind-modular-ui.css", "pagefind-component-ui.js",
                   "pagefind-component-ui.css", "pagefind-highlight.js")
TEXT_LABELS = {"checked": "Text of this page, checked by a person",
               "machine-ocr": "Text of this page, read by software and not corrected",
               "quick-ocr": "Text of this page, read by software and not corrected"}


class SiteError(Exception):
    pass


@dataclass
class SiteResult:
    out: Path
    feed: str
    items: int = 0
    pages: int = 0
    html: int = 0
    files: int = 0
    bytes: int = 0
    search: bool = False
    pagefind: str | None = None
    indexable: bool = False
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------------------------------- the feed

def _read_json(path: Path):
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as e:
        raise SiteError(f"can't read {path}: {e}") from None


def _decade(year) -> tuple[str, str]:
    """(slug, label) for a year."""
    if not isinstance(year, int):
        return "undated", "Undated"
    d = year // 10 * 10
    return f"{d}s", f"{d}s"


def load_feed(feed_dir: str | Path) -> tuple[dict, list[dict]]:
    """Read a feed directory: (feed.json, items ready for the templates). Raises SiteError on anything that isn't a
    well-formed version-1 feed, because ids and paths from it become paths in the site."""
    feed_dir = Path(feed_dir)
    if not (feed_dir / FEED_MARKER).is_file():
        raise SiteError(f"{feed_dir} is not a feed directory (no {FEED_MARKER})")
    meta = _read_json(feed_dir / FEED_MARKER)
    if meta.get("feed_version") != FEED_VERSION:
        raise SiteError(f"feed version {meta.get('feed_version')!r} isn't one this site builder reads")
    series_order = {s["slug"]: n for n, s in enumerate(meta.get("series", []))}

    items = []
    for path in sorted((feed_dir / "items").glob("*.json")) if (feed_dir / "items").is_dir() else []:
        doc = _read_json(path)
        item_id = doc.get("id")
        if not isinstance(item_id, str) or not OPAQUE.match(item_id) or item_id != path.stem:
            raise SiteError(f"{path.name}: bad item id")
        series = doc.get("series") or None
        if series and not SLUG.match(str(series.get("slug", ""))):
            raise SiteError(f"{path.name}: bad series slug")
        pages = []
        for p in doc.get("pages", []):
            pid = p.get("id")
            if not isinstance(pid, str) or not OPAQUE.match(pid):
                raise SiteError(f"{path.name}: bad page id")
            full = _read_json(feed_dir / "pages" / f"{pid}.json")
            if full.get("item") != item_id:
                raise SiteError(f"pages/{pid}.json doesn't belong to item {item_id}")
            images = {}
            for name in IMAGE_NAMES:
                img = (full.get("images") or {}).get(name)
                if img is None:
                    continue
                rel = f"images/{pid}/{name}.jpg"
                if img.get("path") != rel or not (feed_dir / rel).is_file():
                    raise SiteError(f"pages/{pid}.json: image {name} is missing or misplaced")
                images[name] = {"path": rel, "width": int(img["width"]), "height": int(img["height"])}
            if "thumb" not in images or "web" not in images:
                raise SiteError(f"pages/{pid}.json: a page needs thumb and web images")
            status = full.get("text_status")
            text = full.get("text") if status in TEXT_LABELS else None
            pages.append({"id": pid, "n": int(full["n"]), "images": images,
                          "largest": images.get("zoom") or images["web"],
                          "text": text if isinstance(text, str) and text.strip() else None,
                          "text_label": TEXT_LABELS.get(status, ""), "redacted": bool(full.get("redacted"))})
        if not pages:
            continue                                   # an item with nothing to show
        pages.sort(key=lambda p: p["n"])
        date = doc.get("date") or {}
        year = date.get("earliest_year")
        decade_slug, decade_label = _decade(year)
        title = doc.get("title") or "Untitled"
        items.append({
            "id": item_id, "url": f"item/{item_id}/", "title": title,
            "date": date.get("display") or "undated", "year": year if isinstance(year, int) else None,
            "decade": decade_slug, "decade_label": decade_label,
            "series": series["slug"] if series else OTHER,
            "series_title": (series.get("title") or series["slug"]) if series else "Other items",
            "type": doc.get("type") or "", "creator": doc.get("creator") or "",
            "tags": [str(t) for t in doc.get("tags") or []], "language": doc.get("language") or "",
            "notes": doc.get("notes") or "", "rights": doc.get("rights") or "",
            "pages": pages, "cover": pages[0]["images"]["thumb"],
            "sort": (year if isinstance(year, int) else 99999, str(date.get("edtf") or ""), title.lower(), item_id),
            "series_sort": series_order.get(series["slug"], 9999) if series else 99999,
        })
    items.sort(key=lambda i: i["sort"])
    return meta, items


# ------------------------------------------------------------------------------------------------------ helpers

def _link_or_copy(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(src, dest)
    except OSError:
        shutil.copyfile(src, dest)


def _copy_static(dest: Path) -> str:
    """Copy the package's static files; return a short hash of them for cache-busting query strings."""
    h = hashlib.sha256()
    root = resources.files("athena.site") / "static"

    def walk(node, rel: Path):
        for child in sorted(node.iterdir(), key=lambda c: c.name):
            if child.is_dir():
                walk(child, rel / child.name)
            elif not child.name.startswith(".") and not child.name.endswith(".md"):
                data = child.read_bytes()
                h.update(child.name.encode() + b"\0" + data)
                (dest / rel).mkdir(parents=True, exist_ok=True)
                (dest / rel / child.name).write_bytes(data)
    walk(root, Path())
    return h.hexdigest()[:10]


def _copy_logo(logo: Path, static: Path) -> str:
    """Put the logo into static/ and return its file name. Raster logos are re-encoded from pixels, so a logo can't
    carry its author's name or a location in metadata."""
    if not logo.is_file():
        raise SiteError(f"logo file not found: {logo}")
    if logo.suffix.lower() == ".svg":
        shutil.copyfile(logo, static / "logo.svg")
        return "logo.svg"
    try:
        from PIL import Image
    except ImportError:
        raise SiteError("a PNG, JPEG or WebP logo needs Pillow installed; or use an SVG") from None
    with Image.open(logo) as im:
        im = im.convert("RGBA")
        Image.frombytes("RGBA", im.size, im.tobytes()).save(static / "logo.png", "PNG", optimize=True)
    return "logo.png"


def pagefind_version() -> str | None:
    try:
        out = subprocess.run([sys.executable, "-m", "pagefind", "--version"], capture_output=True, text=True,
                             timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout.strip().removeprefix("pagefind").strip() if out.returncode == 0 and out.stdout.strip() else None


def _run_pagefind(site: Path) -> None:
    res = subprocess.run([sys.executable, "-m", "pagefind", "--site", str(site), "--glob", "item/**/*.html"],
                         capture_output=True, text=True)
    if res.returncode != 0 or not (site / "pagefind" / "pagefind.js").is_file():
        raise SiteError(f"pagefind failed:\n{res.stdout[-2000:]}\n{res.stderr[-2000:]}")
    for name in PAGEFIND_UNUSED:
        (site / "pagefind" / name).unlink(missing_ok=True)


def _citation(item: dict, cfg: SiteConfig) -> str:
    parts = [f"{item['title']}, {item['date']}."]
    if item["series"] != OTHER:
        parts.append(f"{item['series_title']}.")
    parts.append(f"{cfg.title}.")
    if cfg.base_url:
        parts.append(cfg.base_url + item["url"])
    return " ".join(parts)


def _removal_href(item: dict, cfg: SiteConfig) -> str:
    if not cfg.contact:
        return ""
    if not cfg.contact_href.startswith("mailto:"):
        return cfg.contact_href
    subject = f"Removal or correction request: item {item['id']}"
    body = f"About: {item['title']}\n" + (f"{cfg.base_url}{item['url']}\n" if cfg.base_url else "")
    return f"{cfg.contact_href}?subject={quote(subject)}&body={quote(body)}"


def _swap(building: Path, out: Path) -> None:
    old = out.with_name(out.name + ".old")
    if old.exists():
        shutil.rmtree(old)
    if out.exists():
        os.rename(out, old)
    os.rename(building, out)
    shutil.rmtree(old, ignore_errors=True)


# -------------------------------------------------------------------------------------------------------- build

def build(feed_dir: str | Path, cfg: SiteConfig, out: str | Path, search: bool = True) -> SiteResult:
    """Build the site for ``cfg`` from the feed at ``feed_dir`` into ``out`` (replaced as a whole)."""
    try:
        from jinja2 import Environment, PackageLoader, StrictUndefined
    except ImportError:
        raise SiteError('the site builder needs Jinja2: install the "site" extra') from None

    feed_dir, out = Path(feed_dir), Path(out)
    meta, items = load_feed(feed_dir)
    if meta.get("feed") != cfg.feed:
        raise SiteError(f"[site.{cfg.name}] is for feed {cfg.feed!r}, but {feed_dir} holds feed {meta.get('feed')!r}")
    if out.exists() and any(out.iterdir()) and not (out / MARKER).is_file():
        raise SiteError(f"{out} exists and isn't a site this tool built; refusing to replace it")
    if out.resolve() == feed_dir.resolve() or feed_dir.resolve() in out.resolve().parents:
        raise SiteError("the site can't be built inside its feed directory")
    result = SiteResult(out, cfg.feed)

    # Search engines only ever see a site that carries the public tier alone, isn't a preview, and opted in.
    tiers = list(meta.get("tiers") or [])
    result.indexable = indexable = bool(cfg.index and tiers == ["public"] and not cfg.preview)
    if cfg.index and not indexable:
        result.warnings.append("index = true ignored: only a public-tier site that isn't a preview can be indexed")

    version = pagefind_version() if search else None
    if search and version is None:
        raise SiteError('Pagefind isn\'t installed (install the "site" extra), or build with --no-search')
    use_search = bool(search and items)

    building = out.with_name(out.name + ".building")
    if building.exists():
        shutil.rmtree(building)
    building.mkdir(parents=True)

    static_hash = _copy_static(building / "static")
    logo = _copy_logo(cfg.logo, building / "static") if cfg.logo else None
    c = cfg.colors
    (building / "static" / "theme.css").write_text(
        f":root {{\n  --accent: {c['accent']};\n  --bg: {c['background']};\n  --text: {c['text']};\n}}\n", "utf-8")

    for item in items:
        for page in item["pages"]:
            for img in page["images"].values():
                _link_or_copy(feed_dir / img["path"], building / img["path"])
        result.pages += len(item["pages"])
    result.items = len(items)

    # Groupings.
    series: dict[str, dict] = {}
    for s in meta.get("series", []):
        if SLUG.match(str(s.get("slug", ""))):
            series[s["slug"]] = {"slug": s["slug"], "title": s.get("title") or s["slug"],
                                 "description": s.get("description") or "", "items": []}
    decades: dict[str, dict] = {}
    for item in items:
        series.setdefault(item["series"], {"slug": item["series"], "title": item["series_title"],
                                           "description": "", "items": []})["items"].append(item)
        decades.setdefault(item["decade"], {"slug": item["decade"], "title": item["decade_label"],
                                            "items": []})["items"].append(item)
    series_list = [s for s in series.values() if s["items"]]
    series_list.sort(key=lambda s: (s["items"][0]["series_sort"], s["title"]))
    decade_list = sorted(decades.values(), key=lambda d: (d["slug"] == "undated", d["slug"]))
    by_id = {i["id"]: i for i in items}
    featured = [by_id[f] for f in cfg.featured if f in by_id]
    for f in cfg.featured:
        if f not in by_id:
            result.warnings.append(f"featured item {f} isn't in this feed")

    env = Environment(loader=PackageLoader("athena.site", "templates"), autoescape=True,
                      undefined=StrictUndefined, trim_blocks=True, lstrip_blocks=True)
    common = {"site": cfg, "csp": CSP, "indexable": indexable, "search": use_search, "logo": logo, "v": static_hash,
              "counts": {"items": result.items, "pages": result.pages}, "series_list": series_list,
              "decade_list": decade_list}

    def page(template: str, rel: str, root: str | None = None, **ctx) -> None:
        dest = building / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        depth = len(Path(rel).parts) - 1
        html = env.get_template(template).render(root="../" * depth if root is None else root, path=rel,
                                                 **common, **ctx)
        dest.write_text(html, "utf-8")
        result.html += 1

    page("home.html", "index.html", featured=featured)
    for s in series_list:
        page("listing.html", f"series/{s['slug']}/index.html", heading=s["title"], kind="Series",
             description=s["description"], items=s["items"])
    for d in decade_list:
        page("listing.html", f"decade/{d['slug']}/index.html", heading=d["title"], kind="Decade", description="",
             items=d["items"])
    for item in items:
        viewer = {"title": item["title"], "pages": [
            {"n": p["n"], "levels": [{"url": p["images"][n]["path"], "width": p["images"][n]["width"],
                                      "height": p["images"][n]["height"]} for n in IMAGE_NAMES if n in p["images"]]}
            for p in item["pages"]]}
        page("item.html", f"item/{item['id']}/index.html", item=item, viewer=viewer,
             citation=_citation(item, cfg), removal_href=_removal_href(item, cfg))
    page("about.html", "about/index.html")
    if use_search:
        page("search.html", "search/index.html")
    # The 404 page is served at any address, so its links can't be relative to itself.
    site_root = "/" + cfg.base_url.split("://", 1)[1].partition("/")[2] if cfg.base_url else "/"
    page("404.html", "404.html", root=site_root)

    if indexable:
        robots = "User-agent: *\nAllow: /\n"
        if cfg.base_url:
            robots += f"Sitemap: {cfg.base_url}sitemap.xml\n"
            urls = [""] + [f"series/{s['slug']}/" for s in series_list] + [i["url"] for i in items] + ["about/"]
            (building / "sitemap.xml").write_text(
                '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
                + "".join(f"<url><loc>{cfg.base_url}{u}</loc></url>\n" for u in urls) + "</urlset>\n", "utf-8")
    else:
        robots = "User-agent: *\nDisallow: /\n"
    (building / "robots.txt").write_text(robots, "utf-8")

    if use_search:
        _run_pagefind(building)
        result.search, result.pagefind = True, version

    (building / MARKER).write_text(json.dumps({
        "builder": BUILDER, "site": cfg.name, "feed": cfg.feed, "tiers": tiers, "indexable": indexable,
        "preview": cfg.preview, "search": result.search, "pagefind": result.pagefind, "root_path": site_root,
        "feed_built_at": meta.get("built_at"),
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "counts": {"items": result.items, "pages": result.pages}}, indent=1, sort_keys=True) + "\n", "utf-8")

    for f in building.rglob("*"):
        if f.is_file():
            result.files += 1
            result.bytes += f.stat().st_size
    _swap(building, out)
    return result
