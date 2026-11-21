# SPDX-License-Identifier: GPL-3.0-or-later
"""Changing records: every change is an assertion (who said what, from what source) written in the same
transaction as the current-state row it changes, plus an outbox event for ``library.events``.

Callers pass a cursor inside a transaction; nothing here commits.
"""
from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb

from athena.bundle import edtf

# Item fields a source may assert, and the column each one sets.
ITEM_FIELDS = ("collection", "series", "title", "date", "type", "creator", "physical", "language", "tags", "notes",
               "extra", "rights", "status", "visibility")
JSON_COLUMNS = {"physical", "extra", "rights"}


def assert_(cur, subject_type: str, subject_id: str, predicate: str, value: Any, source: str) -> int:
    row = cur.execute(
        """INSERT INTO assertion (subject_type, subject_id, predicate, value, source)
           VALUES (%s, %s, %s, %s, %s) RETURNING id""",
        (subject_type, subject_id, predicate, Jsonb(value), source)).fetchone()
    return row["id"]


def emit(cur, library: str, event: str, **ids: Any) -> int:
    """Queue an event for library.events. Payloads carry ids only, never content."""
    payload = {"event": event, "library": library, **ids}
    row = cur.execute("INSERT INTO outbox (subject, payload) VALUES (%s, %s) RETURNING id",
                      (f"library.{library}.{event}", Jsonb(payload))).fetchone()
    return row["id"]


def _columns(field: str, value: Any) -> dict[str, Any]:
    """The item columns one asserted field sets."""
    if field == "date":
        if not value:
            return {"date_edtf": None, "date_earliest": None, "date_latest": None}
        d = edtf.parse(value)
        return {"date_edtf": value, "date_earliest": d.earliest, "date_latest": d.latest}
    if field in JSON_COLUMNS:
        return {field: Jsonb(value or {})}
    if field == "tags":
        return {"tags": list(value or [])}
    return {field: value}


def _current(cur, item_id: str) -> dict | None:
    return cur.execute("SELECT * FROM item WHERE id = %s", (item_id,)).fetchone()


def _value_of(row: dict, field: str) -> Any:
    if field == "date":
        return row["date_edtf"]
    return row[field]


def set_item(cur, library: str, item_id: str, fields: dict[str, Any], source: str,
             event: str = "item.updated") -> list[str]:
    """Assert new values for an item's fields; update only what changed. Returns the changed field names."""
    unknown = set(fields) - set(ITEM_FIELDS)
    if unknown:
        raise ValueError(f"unknown item fields: {sorted(unknown)}")
    row = _current(cur, item_id)
    if row is None:
        raise KeyError(f"no item {item_id}")
    changed, cols = [], {}
    for field, value in fields.items():
        if _value_of(row, field) == value:
            continue
        cols.update(_columns(field, value))  # validates (e.g. EDTF) before anything is written
        changed.append(field)
    if not changed:
        return []
    for field in changed:
        assert_(cur, "item", item_id, f"item.{field}", fields[field], source)
    sets = ", ".join(f"{c} = %s" for c in cols)
    cur.execute(f"UPDATE item SET {sets}, updated_at = now() WHERE id = %s", (*cols.values(), item_id))
    emit(cur, library, event, item=item_id, fields=changed)
    return changed


def create_item(cur, library: str, item_id: str, claimed_collection: str, claimed_series: str,
                fields: dict[str, Any], source: str) -> None:
    """Create an item (private by default) and assert its initial fields."""
    cur.execute("INSERT INTO item (id, claimed_collection, claimed_series) VALUES (%s, %s, %s)",
                (item_id, claimed_collection, claimed_series))
    assert_(cur, "item", item_id, "item.created",
            {"claimed_collection": claimed_collection, "claimed_series": claimed_series}, source)
    set_item(cur, library, item_id, {k: v for k, v in fields.items() if v not in (None, "", [], {})}, source,
             event="item.described")
    emit(cur, library, "item.created", item=item_id)


def add_text(cur, page_id: str, *, tier: int, engine: str, plain: str, source: str, markdown: str = "",
             engine_version: str = "", tiles: int | None = None, looped: bool = False,
             hit_cap: bool = False) -> int:
    """Add a text row (append-only) and assert it. The content lives in ``text``; the assertion points at it."""
    row = cur.execute(
        """INSERT INTO text (page, tier, engine, engine_version, tiles, markdown, plain, looped, hit_cap, source)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
        (page_id, tier, engine, engine_version, tiles, markdown, plain, looped, hit_cap, source)).fetchone()
    assert_(cur, "page", page_id, "page.text", {"text_id": row["id"], "tier": tier, "engine": engine}, source)
    return row["id"]


def rebuild_item_fields(cur, item_id: str) -> dict[str, Any]:
    """Current values of an item's fields, rebuilt from its assertions alone (latest assertion per field)."""
    rows = cur.execute(
        """SELECT DISTINCT ON (predicate) predicate, value FROM assertion
           WHERE subject_type = 'item' AND subject_id = %s AND predicate LIKE 'item.%%'
             AND predicate <> 'item.created'
           ORDER BY predicate, id DESC""", (item_id,)).fetchall()
    return {r["predicate"].removeprefix("item."): r["value"] for r in rows if r["value"] not in (None, "", [], {})}


def item_fields(cur, item_id: str) -> dict[str, Any]:
    """Current values of an item's fields, from the item table, in the same shape as ``rebuild_item_fields``."""
    row = _current(cur, item_id)
    if row is None:
        raise KeyError(f"no item {item_id}")
    defaults = {"title": "", "creator": "", "notes": "", "physical": {}, "extra": {}, "rights": {}, "tags": [],
                "status": "new"}
    out = {}
    for field in ITEM_FIELDS:
        value = _value_of(row, field)
        if value is None or value == defaults.get(field):
            continue
        out[field] = list(value) if field == "tags" else value
    return out
