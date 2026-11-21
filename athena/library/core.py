# SPDX-License-Identifier: GPL-3.0-or-later
"""A library instance: its configuration, file store and database, in one object."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import psycopg

from . import db
from .config import LibraryConfig
from .store import FileStore

INGESTED = "ingested"
REJECTED = "rejected"


@dataclass
class Library:
    cfg: LibraryConfig
    store: FileStore = field(init=False)

    def __post_init__(self):
        self.store = FileStore(self.cfg.files)

    @property
    def name(self) -> str:
        return self.cfg.name

    @property
    def intake(self) -> Path:
        return self.cfg.intake

    def connect(self) -> psycopg.Connection:
        return db.connect(self.cfg.database_url)

    def setup(self) -> list[str]:
        """Create directories, apply migrations and seed the arrangement. Safe to run at every start."""
        for d in (self.cfg.files, self.cfg.intake, self.cfg.intake / INGESTED, self.cfg.intake / REJECTED):
            d.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            applied = db.migrate(conn)
            db.seed(conn, self.cfg)
        return applied
