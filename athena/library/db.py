# SPDX-License-Identifier: GPL-3.0-or-later
"""PostgreSQL connection, migrations, and seeding the arrangement from the library's config."""
from __future__ import annotations

from importlib import resources

import psycopg
from psycopg.rows import dict_row

from . import records
from .config import LibraryConfig

MIGRATIONS = "athena.library.migrations"


def connect(dsn: str) -> psycopg.Connection:
    return psycopg.connect(dsn, row_factory=dict_row)


def migration_files() -> list[tuple[str, str]]:
    files = sorted(f for f in resources.files(MIGRATIONS).iterdir() if f.name.endswith(".sql"))
    return [(f.name.removesuffix(".sql"), f.read_text("utf-8")) for f in files]


def migrate(conn: psycopg.Connection) -> list[str]:
    """Apply migrations not yet applied, each in its own transaction. Returns the versions applied."""
    applied = []
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(hashtext('athena-migrate'))")
        conn.execute("""CREATE TABLE IF NOT EXISTS schema_migrations (
                            version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())""")
        done = {r["version"] for r in conn.execute("SELECT version FROM schema_migrations")}
        for version, sql in migration_files():
            if version in done:
                continue
            conn.execute(sql)
            conn.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (version,))
            applied.append(version)
    return applied


def seed(conn: psycopg.Connection, cfg: LibraryConfig) -> int:
    """Create or update collections and series from the config. Never touches visibility (private by default).
    Returns how many rows changed; each change is an assertion with source ``config``."""
    changed = 0
    with conn.transaction():
        cur = conn.cursor()
        for c in cfg.collections:
            row = cur.execute("SELECT title, description, sort FROM collection WHERE slug = %s", (c.slug,)).fetchone()
            new = {"title": c.title, "description": c.description, "sort": c.sort}
            if row == new:
                continue
            cur.execute("""INSERT INTO collection (slug, title, description, sort) VALUES (%s, %s, %s, %s)
                           ON CONFLICT (slug) DO UPDATE SET title = EXCLUDED.title,
                             description = EXCLUDED.description, sort = EXCLUDED.sort""",
                        (c.slug, c.title, c.description, c.sort))
            records.assert_(cur, "collection", c.slug, "collection.defined", new, "config")
            changed += 1
        for s in cfg.series:
            row = cur.execute("SELECT collection, title, description, sort FROM series WHERE slug = %s",
                              (s.slug,)).fetchone()
            new = {"collection": s.collection, "title": s.title, "description": s.description, "sort": s.sort}
            if row == new:
                continue
            cur.execute("""INSERT INTO series (slug, collection, title, description, sort) VALUES (%s, %s, %s, %s, %s)
                           ON CONFLICT (slug) DO UPDATE SET collection = EXCLUDED.collection, title = EXCLUDED.title,
                             description = EXCLUDED.description, sort = EXCLUDED.sort""",
                        (s.slug, s.collection, s.title, s.description, s.sort))
            records.assert_(cur, "series", s.slug, "series.defined", new, "config")
            changed += 1
    return changed
