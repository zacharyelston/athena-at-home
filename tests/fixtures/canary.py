# SPDX-License-Identifier: GPL-3.0-or-later
"""A canary library for leak tests: made-up items at every tier, each carrying unique marker strings in its title, its
text and its pixels, so a test can prove where each one does and doesn't end up. Built at test time, never committed.

    PUB      public and reviewed                         → public and members feeds
    MEM      members series                              → members feed only
    PRIV     no setting anywhere                         → no feed
    CAP      a directory in a public series; a policy caps directories to members → members feed only
    NEW      public series but not yet reviewed; the workflow policy caps it to private → no feed
    NARROW   public, two pages, page 2 set private       → page 1 in both feeds, page 2 in none
    RED      public, a phone number hidden by a region and a text span → in both feeds, number in neither
    STALE    public, a region redaction whose text was never checked → image redacted, text withheld
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from athena.bundle import PageInput, write_bundle
from athena.library import Library, intake, records
from athena.library import config as config_mod
from athena.library.redaction import set_page_redaction
from athena.visibility.library import set_visibility

W, H = 1700, 2200
STATION = {"name": "test-desk", "software": "athena-tests", "version": "0"}
CREATED = datetime(2026, 12, 8, 14, 3, 11, tzinfo=timezone.utc)
SECRET_LINE_Y = 900            # where the to-be-redacted line is drawn
REGION = {"type": "region", "x": 0.03, "y": 0.39, "w": 0.94, "h": 0.06}   # covers the line at SECRET_LINE_Y

CONFIG = {
    "library": {"name": "canary"},
    "collection": [{"slug": "archive", "title": "The archive"}],
    "series": [
        {"slug": "newsletters", "collection": "archive", "title": "Newsletters", "sort": 1,
         "rights": "Shared by the society for personal and research use."},
        {"slug": "minutes", "collection": "archive", "title": "Minutes", "sort": 2},
        {"slug": "directories", "collection": "archive", "title": "Directories", "sort": 3},
        {"slug": "letters", "collection": "archive", "title": "Letters", "sort": 4},
    ],
    "vocabulary": {"types": ["newsletter", "minutes", "directory", "letter"]},
    "policy": [
        {"name": "unreviewed-private", "kind": "workflow", "cap": "private", "when": {"status_not": "reviewed"}},
        {"name": "directories-members", "kind": "content", "cap": "members", "when": {"type_in": ["directory"]}},
    ],
    "feed": [{"name": "public", "tiers": ["public"]}, {"name": "members", "tiers": ["public", "members"]}],
    "publish": {"rights_default": "Rights not evaluated."},
}

# key → (series, type, pages); each page is (pixel words, text). Markers are unique per item and per place.
ITEMS = {
    "PUB": ("newsletters", "newsletter", [("QUARTZPUBL", "Spring flower show CANARY-PUB-TEXT")]),
    "MEM": ("minutes", "minutes", [("QUARTZMEMB", "Board minutes CANARY-MEM-TEXT")]),
    "PRIV": ("letters", "letter", [("QUARTZPRIV", "A private letter CANARY-PRIV-TEXT")]),
    "CAP": ("directories", "directory", [("QUARTZCAPD", "Member list CANARY-CAP-TEXT")]),
    "NEW": ("newsletters", "newsletter", [("QUARTZNEWW", "Not yet reviewed CANARY-NEW-TEXT")]),
    "NARROW": ("newsletters", "newsletter", [("QUARTZNARA", "Front page CANARY-NARROW-ONE"),
                                             ("QUARTZNARB", "Back page CANARY-NARROW-TWO")]),
    "RED": ("newsletters", "newsletter", [("QUARTZREDA", "Call the secretary at CANARY-RED-PHONE 555-0199 today")]),
    "STALE": ("newsletters", "newsletter", [("QUARTZSTAL", "Treasurer's note CANARY-STALE-TEXT")]),
}
TITLE = {k: f"Item CANARY-{k}-TITLE" for k in ITEMS}
HIDDEN_PIXELS = {"RED": "ZIRCONHIDE", "STALE": "ZIRCONSTAL"}       # drawn where the region redaction goes
HIDDEN_TEXT = "CANARY-RED-PHONE 555-0199"

# What must never appear in each feed, and what must.
FORBIDDEN_PUBLIC = ["CANARY-MEM", "CANARY-PRIV", "CANARY-CAP", "CANARY-NEW", "CANARY-NARROW-TWO",
                    "CANARY-RED-PHONE", "555-0199", "CANARY-STALE-TEXT"]
FORBIDDEN_MEMBERS = ["CANARY-PRIV", "CANARY-NEW", "CANARY-NARROW-TWO", "CANARY-RED-PHONE", "555-0199",
                     "CANARY-STALE-TEXT"]
REQUIRED_PUBLIC = ["CANARY-PUB-TITLE", "CANARY-PUB-TEXT", "CANARY-NARROW-ONE", "CANARY-RED-TITLE",
                   "CANARY-STALE-TITLE"]
REQUIRED_MEMBERS = REQUIRED_PUBLIC + ["CANARY-MEM-TITLE", "CANARY-MEM-TEXT", "CANARY-CAP-TEXT"]


def _font(size: int):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:                       # very old Pillow
        return ImageFont.load_default()


def draw_canary_page(path: Path, word: str, hidden: str | None = None, gps: bool = False) -> None:
    """A page with a large readable marker word and, optionally, a second word on the line a region will cover."""
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
    d.text((120, 300), word, fill="black", font=_font(110))
    d.text((120, 520), "RIVERSIDE GARDEN SOCIETY", fill="black", font=_font(60))
    if hidden:
        d.text((120, SECRET_LINE_Y), hidden, fill="black", font=_font(90))
    exif = Image.Exif()
    if gps:
        exif[0x010F] = "Apple"                                   # Make
        exif.get_ifd(0x8825)[2] = (41.0, 16.0, 0.0)              # GPSLatitude
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "JPEG", quality=92, exif=exif)


def make_canary_library(pg_url: str, root: Path, name: str = "canary") -> tuple[Library, dict[str, str]]:
    """Build the library. Returns it and {key: item id}."""
    raw = json.loads(json.dumps(CONFIG))
    raw["library"]["name"] = name
    cfg = config_mod.from_dict(raw, env={"ATHENA_DATABASE_URL": pg_url, "ATHENA_FILES": str(root / "files"),
                                         "ATHENA_INTAKE": str(root / "intake")})
    lib = Library(cfg)
    lib.setup()
    ids = {}
    for key, (series, kind, pages) in ITEMS.items():
        inputs = []
        for n, (word, text) in enumerate(pages, start=1):
            src = root / "work" / key / f"{n}.jpg"
            draw_canary_page(src, word, hidden=HIDDEN_PIXELS.get(key), gps=(key == "PUB"))
            inputs.append(PageInput(raw=src, page=src, width=W, height=H, ppi=200.0, ppi_source="declared",
                                    has_gps=(key == "PUB"), quick=[{"text": text}]))
        b = write_bundle(root / "bundles", station=STATION, source="camera", created_at=CREATED,
                         describe={"collection": "archive", "series": series,
                                   "item": {"title": TITLE[key], "date": "1962", "type": kind,
                                            "notes": f"CANARY-{key}-NOTES kept in the library only"}},
                         pages=inputs)
        result = intake.ingest(lib, b)
        assert result.status == "ingested", result.reason
        ids[key] = result.item

    with lib.connect() as conn:
        for key, item_id in ids.items():
            if key != "NEW":
                with conn.transaction():
                    records.set_item(conn.cursor(), lib.name, item_id, {"status": "reviewed"}, "person:archivist")
        for series, tier in (("newsletters", "public"), ("directories", "public"), ("minutes", "members")):
            set_visibility(conn, lib.name, "series", series, tier, "archivist", "the board's choice (test)")
        set_visibility(conn, lib.name, "page", f"{ids['NARROW']}/2", "private", "archivist")

        red_page = f"{ids['RED']}/1"
        t = conn.execute("SELECT id, plain FROM page_text_current WHERE page = %s", (red_page,)).fetchone()
        start = t["plain"].index(HIDDEN_TEXT)
        set_page_redaction(conn, lib.name, red_page,
                           [REGION, {"type": "text", "text_id": t["id"], "start": start,
                                     "end": start + len(HIDDEN_TEXT)}], "archivist", "phone number")
        set_page_redaction(conn, lib.name, f"{ids['STALE']}/1", [REGION], "archivist", "figure covered")
    return lib, ids
