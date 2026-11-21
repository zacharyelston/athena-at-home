# SPDX-License-Identifier: GPL-3.0-or-later
"""Build a feed: the filtered, redacted copy of a library that one audience may see (portal.md, "Feeds").

    feed/
      feed.json              feed name, tiers, when and how it was built, counts, the series it carries
      items/<id>.json        the description fields the feed allows, rights statement, page list
      pages/<id>.json        page facts and its text (redacted), or why there is none
      images/<id>/           thumb.jpg, web.jpg and, when the page is large enough, zoom.jpg
      log.jsonl              one line per item: opaque id, tier, page count

A site never reads the library; it reads this directory. So this module fails closed: an item or page whose audience
can't be decided, or whose redactions aren't valid, is left out, and text is withheld whenever a redaction hasn't
been checked against the page's current text. Ids in a feed are opaque hashes, originals are never copied, and
every image is re-encoded from pixels with redactions burned in (athena.publish.images).
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

from athena.bundle import edtf
from athena.library import records, redaction
from athena.library.core import Library
from athena.visibility import Level, Tier, effective_item, effective_page
from athena.visibility import library as vlib

from . import images

FEED_VERSION = 1
BUILDER = "athena-publish/1"       # bump to invalidate the derivative cache when the image pipeline changes
MARKER = "feed.json"


class PublishError(Exception):
    pass


@dataclass
class BuildResult:
    feed: str
    out: Path
    items: int = 0
    pages: int = 0
    images: int = 0
    rendered: int = 0                          # images made this run (the rest came from the cache)
    shared: list[str] = field(default_factory=list)      # library item ids that entered the feed
    unshared: list[str] = field(default_factory=list)    # ... and that left it
    skipped: list[tuple[str, str]] = field(default_factory=list)   # (library id, reason); never written to the feed


def opaque_id(library: str, feed: str, kind: str, record_id: str) -> str:
    """A stable id for a record in a feed that reveals nothing about the library's own ids, counts or order."""
    return hashlib.sha256(f"{library}\0{feed}\0{kind}\0{record_id}".encode()).hexdigest()[:20]


def _spec(lib: Library) -> images.ImageSpec:
    p = lib.cfg.publish
    return images.ImageSpec(p.thumb, p.web, p.zoom, p.max_fraction, p.jpeg_quality, p.redaction_margin)


def _cache_dir(lib: Library, page_sha: str, regions, spec: images.ImageSpec) -> Path:
    key = hashlib.sha256(json.dumps([BUILDER, page_sha, regions, spec.key()]).encode()).hexdigest()
    return lib.cfg.files / "derivatives" / "publish" / key[:2] / key


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


def _date(row: dict) -> dict | None:
    if not row["date_edtf"]:
        return None
    return {"edtf": row["date_edtf"], "display": edtf.display(row["date_edtf"]),
            "earliest_year": row["date_earliest"].year if row["date_earliest"] else None,
            "latest_year": row["date_latest"].year if row["date_latest"] else None}


def _item_fields(row: dict, allowed) -> dict:
    out = {}
    for f in allowed:
        if f == "date":
            out["date"] = _date(row)
        elif f == "tags":
            out["tags"] = list(row["tags"] or [])
        elif f == "physical":
            out["physical"] = row["physical"] or {}
        else:
            out[f] = row[f] or ("Untitled" if f == "title" else None)
    return out


def _text_for(page: dict, text_row: dict | None, entries, skip_kinds) -> tuple[str | None, str]:
    """(text, status) for a page: status is checked | machine-ocr | quick-ocr | image-only | withheld."""
    if page["kind"] in skip_kinds or text_row is None or not text_row["plain"].strip():
        return None, "image-only"
    text, status = redaction.redact_text(text_row["plain"], text_row["id"], entries)
    if text is None:
        return None, "withheld"
    return text, {2: "checked", 1: "machine-ocr", 0: "quick-ocr"}[text_row["tier"]]


@dataclass
class Selection:
    """What a feed carries right now: [(item id, decision, [(page row, redaction entries, regions)])]."""
    items: list
    item_rows: dict
    texts: dict
    skipped: list[tuple[str, str]]


def plan_feed(lib: Library, conn, feed, today: date) -> Selection:
    """Decide what ``feed`` carries, without writing anything (also the dry run behind "what's public" views).
    Anything that can't be decided, has invalid redactions or has no image is left out and listed in ``skipped``."""
    cfg = lib.cfg
    skipped: list[tuple[str, str]] = []
    decisions = {}
    for it in vlib.load_items(conn):
        try:
            d = effective_item(list(it.levels), it.facts, cfg.policies, today, it.page_settings)
        except Exception as e:  # noqa: BLE001 - fail closed on anything
            skipped.append((it.id, f"visibility could not be decided: {type(e).__name__}"))
            continue
        if d.tier in feed.tiers:
            decisions[it.id] = d
    ids = sorted(decisions)
    item_rows = {r["id"]: r for r in conn.execute(
        """SELECT i.*, s.title AS series_title FROM item i LEFT JOIN series s ON s.slug = i.series
           WHERE i.id = ANY(%s)""", (ids,))}
    page_rows: dict[str, list[dict]] = {}
    for r in conn.execute("SELECT * FROM page WHERE item = ANY(%s) ORDER BY item, n", (ids,)):
        page_rows.setdefault(r["item"], []).append(r)
    texts = {r["page"]: r for r in conn.execute(
        """SELECT t.id, t.page, t.tier, t.plain FROM page_text_current t JOIN page p ON p.id = t.page
           WHERE p.item = ANY(%s)""", (ids,))}
    items = []
    for item_id in ids:
        d = decisions[item_id]
        pages = []
        for p in page_rows.get(item_id, []):
            pd = effective_page(d, Level("page", p["id"], Tier.parse(p["visibility"])))
            if pd.tier not in feed.tiers:
                continue
            try:
                entries = redaction.validate(p["redaction"] or [])
            except redaction.RedactionError as e:
                skipped.append((p["id"], f"invalid redaction, page left out: {e}"))
                continue
            if not lib.store.path_for(p["page_sha256"], p["page_ext"]).is_file():
                skipped.append((p["id"], "page image missing from the store, page left out"))
                continue
            pages.append((p, entries, [[r.x, r.y, r.w, r.h] for r in redaction.regions(entries)]))
        if pages:
            items.append((item_id, d, pages))
        else:
            skipped.append((item_id, "no page of this item is in the feed's tiers"))
    return Selection(items, item_rows, texts, skipped)


def get_feed(lib: Library, feed_name: str):
    feed = next((f for f in lib.cfg.feeds if f.name == feed_name), None)
    if feed is None:
        raise PublishError(f"no feed named {feed_name!r} in the config (feeds: {[f.name for f in lib.cfg.feeds]})")
    return feed


def _render_job(args):
    src, regions, spec, dest = args
    return dest, images.render_to_dir(src, regions, spec, dest)


def build(lib: Library, feed_name: str, out: str | Path, today: date | None = None, jobs: int = 1) -> BuildResult:
    """Build ``feed_name`` into ``out`` (replaced as a whole, so nothing unshared can linger)."""
    cfg = lib.cfg
    feed = get_feed(lib, feed_name)
    today = today or date.today()
    out = Path(out)
    if out.exists() and any(out.iterdir()) and not (out / MARKER).is_file():
        raise PublishError(f"{out} exists and isn't a feed directory; refusing to replace it")
    result = BuildResult(feed_name, out)
    spec = _spec(lib)
    oid = lambda kind, rid: opaque_id(lib.name, feed_name, kind, rid)   # noqa: E731

    with lib.connect() as conn:
        # 1–2. Which items and pages, with what redactions; queue the images that aren't cached yet.
        selection = plan_feed(lib, conn, feed, today)
        result.skipped += selection.skipped
        item_rows, texts = selection.item_rows, selection.texts
        plan, render_jobs = [], {}
        for item_id, d, planned in selection.items:
            pages = []
            for p, entries, regions in planned:
                src = lib.store.path_for(p["page_sha256"], p["page_ext"])
                cache = _cache_dir(lib, p["page_sha256"], regions, spec)
                if not (cache / "sizes.json").is_file():
                    render_jobs[str(cache)] = (str(src), regions, spec, f"{cache}.tmp-{os.getpid()}")
                pages.append((p, entries, cache))
            plan.append((item_id, d, pages))

        # 3. Render what's missing (redaction burned in before any resize), into the cache.
        todo = list(render_jobs.items())
        runner = ProcessPoolExecutor(max_workers=jobs) if jobs > 1 and len(todo) > 1 else None
        try:
            done = runner.map(_render_job, [j for _, j in todo]) if runner else map(_render_job, [j for _, j in todo])
            for (cache, _), (tmp, sizes) in zip(todo, done):
                _write_json(Path(tmp) / "sizes.json", sizes)
                Path(cache).parent.mkdir(parents=True, exist_ok=True)
                try:
                    os.rename(tmp, cache)
                except OSError:               # another build got there first; ours is identical
                    shutil.rmtree(tmp, ignore_errors=True)
                result.rendered += len(sizes)
        finally:
            if runner:
                runner.shutdown()

        # 4. Write the feed into a fresh directory.
        building = out.with_name(out.name + ".building")
        if building.exists():
            shutil.rmtree(building)
        building.mkdir(parents=True)
        series_counts: dict[str, int] = {}
        log_lines = []
        for item_id, d, pages in plan:
            row = item_rows[item_id]
            item_oid = oid("item", item_id)
            page_list = []
            for p, entries, cache in pages:
                page_oid = oid("page", p["id"])
                sizes = json.loads((cache / "sizes.json").read_text("utf-8"))
                img = {}
                for name, (w, h) in sizes.items():
                    dest = building / "images" / page_oid / f"{name}.jpg"
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(cache / f"{name}.jpg", dest)
                    img[name] = {"path": f"images/{page_oid}/{name}.jpg", "width": w, "height": h}
                    result.images += 1
                text, status = _text_for(p, texts.get(p["id"]), entries, cfg.ocr_skip_kinds)
                _write_json(building / "pages" / f"{page_oid}.json",
                            {"id": page_oid, "item": item_oid, "n": p["n"], "side": p["side"], "kind": p["kind"],
                             "images": img, "text": text, "text_status": status,
                             "redacted": bool(entries and any(e["type"] != "text_checked" for e in entries))})
                page_list.append({"id": page_oid, "n": p["n"], "side": p["side"], "kind": p["kind"],
                                  "images": img, "text_status": status})
                result.pages += 1
            series_cfg = next((s for s in cfg.series if s.slug == row["series"]), None)
            rights = ((row["rights"] or {}).get("statement") or (series_cfg.rights if series_cfg else "")
                      or cfg.publish.rights_default)
            doc = {"id": item_oid, "pages": page_list, "rights": rights,
                   "series": ({"slug": row["series"], "title": row["series_title"]} if row["series"] else None)}
            doc.update(_item_fields(row, cfg.publish.fields))
            _write_json(building / "items" / f"{item_oid}.json", doc)
            if row["series"]:
                series_counts[row["series"]] = series_counts.get(row["series"], 0) + 1
            log_lines.append(json.dumps({"item": item_oid, "tier": str(d.tier), "pages": len(page_list)}))
            result.items += 1

        series_meta = []
        for s in sorted((s for s in cfg.series if s.slug in series_counts), key=lambda s: (s.sort, s.slug)):
            series_meta.append({"slug": s.slug, "title": s.title, "description": s.description,
                                "items": series_counts[s.slug]})
        uses_cutoff = any(p.enabled and p.when.get("published_after_pd_cutoff") for p in cfg.policies)
        _write_json(building / MARKER, {
            "feed_version": FEED_VERSION, "feed": feed_name, "tiers": sorted(str(t) for t in feed.tiers),
            "library": lib.name, "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "decided_as_of": today.isoformat(), "builder": BUILDER, "zoom": "pyramid",
            "pd_cutoff_year": today.year - 96 if uses_cutoff else None,
            "counts": {"items": result.items, "pages": result.pages, "images": result.images},
            "series": series_meta})
        (building / "log.jsonl").write_text("".join(line + "\n" for line in log_lines), encoding="utf-8")

        # 5. Swap it into place, whole.
        old = out.with_name(out.name + ".old")
        if old.exists():
            shutil.rmtree(old)
        if out.exists():
            os.rename(out, old)
        os.rename(building, out)
        shutil.rmtree(old, ignore_errors=True)

        # 6. The share log: what entered and left this feed since the last build, and why.
        with conn.transaction():
            cur = conn.cursor()
            previous = {r["subject_id"] for r in cur.execute(
                """SELECT subject_id FROM (
                     SELECT DISTINCT ON (subject_id) subject_id, predicate FROM assertion
                     WHERE subject_type = 'item' AND predicate IN ('feed.shared', 'feed.unshared')
                       AND value->>'feed' = %s ORDER BY subject_id, id DESC) latest
                   WHERE predicate = 'feed.shared'""", (feed_name,))}
            now = {item_id for item_id, _, _ in plan}
            for item_id, d, _ in plan:
                if item_id not in previous:
                    records.assert_(cur, "item", item_id, "feed.shared",
                                    {"feed": feed_name, "id": oid("item", item_id), "tier": str(d.tier),
                                     "why": d.explain()}, "publisher")
                    result.shared.append(item_id)
            for item_id in sorted(previous - now):
                records.assert_(cur, "item", item_id, "feed.unshared", {"feed": feed_name}, "publisher")
                result.unshared.append(item_id)
            records.emit(cur, lib.name, "feed.built", feed=feed_name, items=result.items, pages=result.pages,
                         shared=len(result.shared), unshared=len(result.unshared))
    return result
