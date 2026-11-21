# SPDX-License-Identifier: GPL-3.0-or-later
"""The library: write-once originals, PostgreSQL records with provenance, intake, OCR, search and events.

A library is a named instance: its own database, data directory, intake directory, config file and NATS account
(ADR 0001). See docs/epics/garden-club-archive/D10-library-core.md.
"""
from .config import LibraryConfig, load
from .core import Library

__all__ = ["Library", "LibraryConfig", "load"]
