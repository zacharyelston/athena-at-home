# SPDX-License-Identifier: GPL-3.0-or-later
"""Full-text search over item titles and descriptions and the current text of every page (PostgreSQL ``tsvector``),
with trigram matching on titles for near misses. Returns items, best first."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Hit:
    item: str
    title: str
    date: str | None
    series: str | None
    rank: float
    pages: list[int]


SEARCH_SQL = """
WITH q AS (SELECT websearch_to_tsquery('english', %(q)s) AS q),
text_hits AS (
    SELECT p.item, p.n, ts_rank(t.search, q.q) AS rank
    FROM q, page_text_current t JOIN page p ON p.id = t.page
    WHERE t.search @@ q.q
),
item_hits AS (
    SELECT i.id AS item, ts_rank(i.search, q.q) * 2 AS rank
    FROM q, item i WHERE i.search @@ q.q
    UNION ALL
    SELECT i.id, similarity(i.title, %(q)s) FROM item i
    WHERE %(fuzzy)s AND i.title %% %(q)s
),
scores AS (
    SELECT item, max(rank) AS rank, array_agg(n ORDER BY n) FILTER (WHERE n IS NOT NULL) AS pages
    FROM (SELECT item, rank, n FROM text_hits UNION ALL SELECT item, rank, NULL FROM item_hits) all_hits
    GROUP BY item
)
SELECT i.id, i.title, i.date_edtf, i.series, s.rank, coalesce(s.pages, '{}') AS pages
FROM scores s JOIN item i ON i.id = s.item
ORDER BY s.rank DESC, i.id
LIMIT %(limit)s
"""


def search(conn, query: str, limit: int = 20, fuzzy: bool = True) -> list[Hit]:
    if not query.strip():
        return []
    rows = conn.execute(SEARCH_SQL, {"q": query, "limit": limit, "fuzzy": fuzzy}).fetchall()
    return [Hit(r["id"], r["title"], r["date_edtf"], r["series"], float(r["rank"]), list(r["pages"])) for r in rows]
