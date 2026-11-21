# SPDX-License-Identifier: GPL-3.0-or-later
"""OCR for library pages through Sieve.

``run_via_page`` sends each page that has no OCR text yet through Sieve's ``ocr.page`` service, one page at a time,
and can be stopped and restarted at any point (it only picks pages still missing tier-1 text). It's the path until
the durable ``ocr.jobs`` queue (D09) lands, and stays as the tool for small re-runs. Results are stored with the
engine as their source; a page Sieve refuses is recorded in ``ocr_failure`` for the review queue.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from . import records
from .core import Library


class OcrClient(Protocol):
    def ocr(self, image: bytes, **kw) -> dict: ...


@dataclass
class OcrRun:
    done: int = 0
    failed: int = 0
    items_ocr_done: list[str] = field(default_factory=list)


def pages_needing_ocr(conn, skip_kinds, limit: int | None = None) -> list[dict]:
    sql = """SELECT p.id, p.item, p.ocr_sha256 FROM page p
             WHERE p.ocr_sha256 IS NOT NULL AND NOT (p.kind = ANY(%s))
               AND NOT EXISTS (SELECT 1 FROM text t WHERE t.page = p.id AND t.tier >= 1)
               AND NOT EXISTS (SELECT 1 FROM ocr_failure f WHERE f.page = p.id AND f.resolved_at IS NULL)
             ORDER BY p.item, p.n"""
    if limit:
        sql += f" LIMIT {int(limit)}"
    return conn.execute(sql, (list(skip_kinds),)).fetchall()


def store_reply(cur, page_id: str, reply: dict) -> int:
    """Store Sieve's reply for a page: the tier-1 text, and tier-0 text too if the page has none yet."""
    model = reply.get("model", "unknown")
    source = f"ocr:{model}"
    if reply.get("tier0") and not cur.execute("SELECT 1 FROM text WHERE page = %s AND tier = 0",
                                              (page_id,)).fetchone():
        records.add_text(cur, page_id, tier=0, engine="apple-vision", plain=reply["tier0"].get("text", ""),
                         source=f"ocr:{reply.get('worker', 'sieve')}")
    markdown = reply.get("text", "")
    return records.add_text(cur, page_id, tier=1, engine=model, engine_version=reply.get("worker", ""),
                            plain=markdown, markdown=markdown, tiles=reply.get("tiles"),
                            looped=bool(reply.get("looped")), hit_cap=bool(reply.get("hit_cap")), source=source)


def mark_items_done(cur, library: str) -> list[str]:
    """Items still ``new`` whose every OCR-able page has tier-1 text become ``ocr_done``."""
    rows = cur.execute(
        """SELECT i.id FROM item i WHERE i.status = 'new'
             AND NOT EXISTS (SELECT 1 FROM page p WHERE p.item = i.id AND p.ocr_sha256 IS NOT NULL
                               AND NOT EXISTS (SELECT 1 FROM text t WHERE t.page = p.id AND t.tier >= 1))""").fetchall()
    done = []
    for r in rows:
        records.set_item(cur, library, r["id"], {"status": "ocr_done"}, "system:ocr", event="item.ocr_done")
        done.append(r["id"])
    return done


def run_via_page(lib: Library, client: OcrClient, limit: int | None = None, tiles="auto") -> OcrRun:
    from sieve.client import SieveError

    run = OcrRun()
    with lib.connect() as conn:
        for page in pages_needing_ocr(conn, lib.cfg.ocr_skip_kinds, limit):
            data = lib.store.derivative_path("ocr", page["ocr_sha256"], "jpg").read_bytes()
            try:
                reply = client.ocr(data, tiles=tiles)
            except SieveError as e:
                with conn.transaction():
                    conn.execute("INSERT INTO ocr_failure (page, reason, source) VALUES (%s, %s, %s)",
                                 (page["id"], str(e)[:2000], "ocr:sieve"))
                run.failed += 1
                continue
            with conn.transaction():
                store_reply(conn.cursor(), page["id"], reply)
            run.done += 1
        with conn.transaction():
            run.items_ocr_done = mark_items_done(conn.cursor(), lib.name)
    return run
