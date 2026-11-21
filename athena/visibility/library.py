# SPDX-License-Identifier: GPL-3.0-or-later
"""Visibility on a library's PostgreSQL database: load the hierarchy, decide, change settings with audit.

Every change is an assertion (predicate ``visibility``, source ``person:<login>``) written in the same transaction as
the column it sets, plus a ``visibility.changed`` event. A change that **widens** a node's audience needs a reason
and goes into the ``visibility_review`` queue until someone confirms it.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from psycopg.types.json import Jsonb

from athena.library import records
from athena.library.config import LibraryConfig

from .core import (Decision, ItemFacts, ItemInput, Level, Policy, Preview, Tier, diff, effective_item,
                   effective_page, feeds_for)

SUBJECTS = {"collection": "collection", "series": "series", "item": "item", "page": "page"}
KEY = {"collection": "slug", "series": "slug", "item": "id", "page": "id"}

ITEMS_SQL = """
SELECT i.id, i.visibility AS item_vis, i.status, i.type, i.tags, i.date_latest,
       s.slug AS series, s.visibility AS series_vis,
       c.slug AS collection, c.visibility AS collection_vis,
       coalesce(array_agg(p.visibility::text ORDER BY p.n) FILTER (WHERE p.id IS NOT NULL), '{}') AS page_vis
FROM item i
LEFT JOIN series s ON s.slug = i.series
LEFT JOIN collection c ON c.slug = coalesce(s.collection, i.collection)
LEFT JOIN page p ON p.item = i.id
/*where*/
GROUP BY i.id, s.slug, c.slug
ORDER BY i.id
"""


def _item_input(r: dict) -> ItemInput:
    levels = []
    if r["collection"]:
        levels.append(Level("collection", r["collection"], Tier.parse(r["collection_vis"])))
    if r["series"]:
        levels.append(Level("series", r["series"], Tier.parse(r["series_vis"])))
    levels.append(Level("item", r["id"], Tier.parse(r["item_vis"])))
    facts = ItemFacts(status=r["status"], type=r["type"], tags=tuple(r["tags"] or ()), series=r["series"],
                      latest=r["date_latest"])
    return ItemInput(r["id"], tuple(levels), facts, tuple(Tier.parse(v) for v in r["page_vis"]))


def load_items(conn, item_ids: list[str] | None = None) -> list[ItemInput]:
    if item_ids is None:
        rows = conn.execute(ITEMS_SQL).fetchall()
    else:
        rows = conn.execute(ITEMS_SQL.replace("/*where*/", "WHERE i.id = ANY(%s)"), (list(item_ids),)).fetchall()
    return [_item_input(r) for r in rows]


@dataclass
class ItemDecision:
    item: Decision
    pages: dict[str, Decision]
    feeds: list[str]


def decide_item(conn, cfg: LibraryConfig, item_id: str, today: date | None = None) -> ItemDecision:
    today = today or date.today()
    found = load_items(conn, [item_id])
    if not found:
        raise KeyError(f"no item {item_id}")
    it = found[0]
    d = effective_item(list(it.levels), it.facts, cfg.policies, today, it.page_settings)
    pages = {}
    for r in conn.execute("SELECT id, visibility FROM page WHERE item = %s ORDER BY n", (item_id,)):
        pages[r["id"]] = effective_page(d, Level("page", r["id"], Tier.parse(r["visibility"])))
    return ItemDecision(d, pages, feeds_for(d.tier, cfg.feeds))


def _inherited_before(cur, subject_type: str, subject_id: str) -> Tier:
    """The tier a node had from its own setting or its parents' (before policies), to detect widening."""
    if subject_type == "collection":
        r = cur.execute("SELECT visibility FROM collection WHERE slug = %s", (subject_id,)).fetchone()
        return Tier.parse(r["visibility"])
    if subject_type == "series":
        r = cur.execute("""SELECT s.visibility s, c.visibility c FROM series s JOIN collection c ON c.slug = s.collection
                           WHERE s.slug = %s""", (subject_id,)).fetchone()
        return Tier.parse(r["s"] or r["c"])
    if subject_type == "item":
        r = cur.execute("""SELECT i.visibility i, s.visibility s, c.visibility c FROM item i
                           LEFT JOIN series s ON s.slug = i.series
                           LEFT JOIN collection c ON c.slug = coalesce(s.collection, i.collection)
                           WHERE i.id = %s""", (subject_id,)).fetchone()
        return Tier.parse(r["i"] or r["s"] or r["c"] or "private")
    r = cur.execute("SELECT item, visibility FROM page WHERE id = %s", (subject_id,)).fetchone()
    if r["visibility"]:
        return Tier.parse(r["visibility"])
    return _inherited_before(cur, "item", r["item"])


def set_visibility(conn, library: str, subject_type: str, subject_id: str, tier: "Tier | str | None", who: str,
                   why: str = "") -> dict:
    """Set (or, with ``None``, clear to inherit) a node's visibility. Returns {"widened", "assertion", "review"}."""
    if subject_type not in SUBJECTS:
        raise ValueError(f"can't set visibility on {subject_type!r}")
    tier = Tier.parse(tier)
    if tier is None and subject_type == "collection":
        raise ValueError("a collection always has a setting (private by default); it has nothing to inherit")
    if not who:
        raise ValueError("say who is making the change")
    with conn.transaction():
        cur = conn.cursor()
        key = KEY[subject_type]
        if not cur.execute(f"SELECT 1 FROM {subject_type} WHERE {key} = %s", (subject_id,)).fetchone():
            raise KeyError(f"no {subject_type} {subject_id}")
        before = _inherited_before(cur, subject_type, subject_id)
        cur.execute(f"UPDATE {subject_type} SET visibility = %s WHERE {key} = %s",
                    (str(tier) if tier is not None else None, subject_id))
        after = _inherited_before(cur, subject_type, subject_id)
        widened = after > before
        if widened and not why.strip():
            raise ValueError(f"widening {subject_type} {subject_id} from {before} to {after} needs a reason")
        aid = records.assert_(cur, subject_type, subject_id, "visibility",
                              {"tier": str(tier) if tier is not None else None, "before": str(before),
                               "after": str(after), "widened": widened, "why": why}, f"person:{who}")
        review = None
        if widened:
            review = cur.execute("""INSERT INTO visibility_review (assertion, subject_type, subject_id)
                                    VALUES (%s, %s, %s) RETURNING id""", (aid, subject_type, subject_id)).fetchone()["id"]
        records.emit(cur, library, "visibility.changed", subject=f"{subject_type}:{subject_id}")
    return {"widened": widened, "assertion": aid, "review": review, "before": before, "after": after}


def open_reviews(conn) -> list[dict]:
    return conn.execute(
        """SELECT r.id, r.subject_type, r.subject_id, r.created_at, a.source, a.value
           FROM visibility_review r JOIN assertion a ON a.id = r.assertion
           WHERE r.confirmed_at IS NULL ORDER BY r.id""").fetchall()


def confirm_review(conn, review_id: int, who: str) -> None:
    with conn.transaction():
        r = conn.execute("""UPDATE visibility_review SET confirmed_at = now(), confirmed_by = %s
                            WHERE id = %s AND confirmed_at IS NULL RETURNING subject_type, subject_id""",
                         (who, review_id)).fetchone()
        if r is None:
            raise KeyError(f"no open review {review_id}")
        records.assert_(conn.cursor(), r["subject_type"], r["subject_id"], "visibility.confirmed",
                        {"review": review_id}, f"person:{who}")


def sync_policies(conn, cfg: LibraryConfig) -> int:
    """Mirror the config's policies into the ``policy`` table (the config is the truth). Returns rows changed."""
    changed = 0
    with conn.transaction():
        cur = conn.cursor()
        current = {r["name"]: r for r in cur.execute("SELECT * FROM policy")}
        wanted = {p.name: p for p in cfg.policies}
        for p in cfg.policies:
            row = {"kind": p.kind, "cap": str(p.cap), "config": p.when, "enabled": p.enabled}
            old = current.get(p.name)
            if old and {k: old[k] for k in row} == row:
                continue
            cur.execute("""INSERT INTO policy (name, kind, cap, config, enabled) VALUES (%s, %s, %s, %s, %s)
                           ON CONFLICT (name) DO UPDATE SET kind = EXCLUDED.kind, cap = EXCLUDED.cap,
                             config = EXCLUDED.config, enabled = EXCLUDED.enabled""",
                        (p.name, p.kind, str(p.cap), Jsonb(p.when), p.enabled))
            records.assert_(cur, "policy", p.name, "policy.defined", row, "config")
            changed += 1
        for name, old in current.items():
            if name not in wanted and old["enabled"]:
                cur.execute("UPDATE policy SET enabled = false WHERE name = %s", (name,))
                records.assert_(cur, "policy", name, "policy.removed", {}, "config")
                changed += 1
    return changed


def preview_policies(conn, before: list[Policy], after: list[Policy], today: date | None = None) -> Preview:
    return diff(load_items(conn), before, after, today or date.today())
