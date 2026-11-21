# SPDX-License-Identifier: GPL-3.0-or-later
"""The publisher against the canary library: every tier, policy, page setting and redaction must hold in every
output byte, and the leak checker must catch each kind of leak when one is planted."""
import gzip
import io
import json
import shutil
from datetime import date

import pytest
from fixtures import canary
from PIL import Image

from athena.library import redaction
from athena.library.redaction import RedactionError, redact_text, set_page_redaction
from athena.publish import (PublishError, build, check_feed, check_images, diff, images, opaque_id,
                            scan_for_strings)
from athena.visibility.library import set_visibility

TODAY = date(2026, 12, 15)
NOTES = ["-NOTES"]            # notes aren't a feed field by default, for any item


@pytest.fixture
def built(pg_url, tmp_path):
    lib, ids = canary.make_canary_library(pg_url, tmp_path / "lib")
    pub = build(lib, "public", tmp_path / "feeds" / "public", today=TODAY)
    mem = build(lib, "members", tmp_path / "feeds" / "members", today=TODAY)
    return lib, ids, pub, mem


def item_doc(lib, feed, root, item_id):
    return json.loads((root / "items" / f"{opaque_id(lib.name, feed, 'item', item_id)}.json").read_text())


def page_doc(lib, feed, root, page_id):
    return json.loads((root / "pages" / f"{opaque_id(lib.name, feed, 'page', page_id)}.json").read_text())


# --- what each feed carries ------------------------------------------------------------------------------------

def test_each_feed_carries_exactly_its_tiers(built):
    lib, ids, pub, mem = built
    assert (pub.items, pub.pages) == (4, 4)          # PUB, NARROW (page 1), RED, STALE
    assert (mem.items, mem.pages) == (6, 6)          # ... plus MEM and CAP
    assert sorted(pub.shared) == sorted(ids[k] for k in ("PUB", "NARROW", "RED", "STALE"))
    for root, forbidden, required in ((pub.out, canary.FORBIDDEN_PUBLIC, canary.REQUIRED_PUBLIC),
                                      (mem.out, canary.FORBIDDEN_MEMBERS, canary.REQUIRED_MEMBERS)):
        assert scan_for_strings(root, forbidden + NOTES) == []
        for s in required:
            assert scan_for_strings(root, [s]), f"{s} should be in {root.name}"


def test_built_feeds_pass_the_full_check(built):
    lib, _, pub, mem = built
    assert check_feed(lib, "public", pub.out) == []
    assert check_feed(lib, "members", mem.out) == []


def test_library_ids_never_appear_in_a_feed(built):
    lib, ids, pub, mem = built
    assert scan_for_strings(pub.out, list(ids.values())) == []
    assert scan_for_strings(mem.out, list(ids.values())) == []


def test_feed_json_and_item_documents(built):
    lib, ids, pub, _ = built
    meta = json.loads((pub.out / "feed.json").read_text())
    assert meta["feed"] == "public" and meta["tiers"] == ["public"] and meta["decided_as_of"] == "2026-12-15"
    assert meta["counts"] == {"items": 4, "pages": 4, "images": 12}
    assert [s["slug"] for s in meta["series"]] == ["newsletters"] and meta["series"][0]["items"] == 4
    doc = item_doc(lib, "public", pub.out, ids["PUB"])
    assert doc["title"] == canary.TITLE["PUB"] and doc["date"]["display"] == "1962"
    assert doc["series"] == {"slug": "newsletters", "title": "Newsletters"}
    assert doc["rights"] == "Shared by the society for personal and research use."
    assert "notes" not in doc and "physical" not in doc
    [page] = doc["pages"]
    assert set(page["images"]) == {"thumb", "web", "zoom"} and page["text_status"] == "quick-ocr"
    log = [json.loads(line) for line in (pub.out / "log.jsonl").read_text().splitlines()]
    assert len(log) == 4 and all(set(e) == {"item", "tier", "pages"} for e in log)


def test_a_page_narrower_than_its_item_is_left_out(built):
    lib, ids, pub, mem = built
    for feed, root in (("public", pub.out), ("members", mem.out)):
        doc = item_doc(lib, feed, root, ids["NARROW"])
        assert [p["n"] for p in doc["pages"]] == [1]
        assert not (root / "pages" / f"{opaque_id(lib.name, feed, 'page', ids['NARROW'] + '/2')}.json").exists()


# --- redaction -------------------------------------------------------------------------------------------------

def test_redaction_is_burned_into_every_image_and_the_text(built):
    lib, ids, pub, _ = built
    page = page_doc(lib, "public", pub.out, f"{ids['RED']}/1")
    assert page["text"] == "Call the secretary at [redacted] today" and page["redacted"] is True
    r = canary.REGION
    for name, img in page["images"].items():
        with Image.open(pub.out / img["path"]) as im:
            w, h = im.size
            box = (int(r["x"] * w) + 3, int(r["y"] * h) + 3, int((r["x"] + r["w"]) * w) - 3,
                   int((r["y"] + r["h"]) * h) - 3)
            assert im.convert("L").crop(box).getextrema()[1] <= 64, name
            assert im.convert("L").getextrema()[1] > 200          # the rest of the page is still there


def test_text_is_withheld_when_a_region_was_never_checked_against_it(built):
    lib, ids, pub, _ = built
    page = page_doc(lib, "public", pub.out, f"{ids['STALE']}/1")
    assert page["text"] is None and page["text_status"] == "withheld"


def test_text_is_withheld_when_the_text_changes_under_a_redaction(built, tmp_path):
    lib, ids, pub, _ = built
    from athena.library import records
    with lib.connect() as conn, conn.transaction():
        records.add_text(conn.cursor(), f"{ids['RED']}/1", tier=1, engine="fake-ocr", source="ocr:fake",
                         plain="Phone the secretary on CANARY-RED-PHONE 555-0199")
    again = build(lib, "public", pub.out, today=TODAY)
    page = page_doc(lib, "public", again.out, f"{ids['RED']}/1")
    assert page["text"] is None and page["text_status"] == "withheld"
    assert scan_for_strings(again.out, canary.FORBIDDEN_PUBLIC) == []


@pytest.mark.macos
def test_apple_vision_cannot_read_what_was_redacted(built):
    """OCR every image of the redacted page: the marker under the rectangle is gone, the visible one is readable."""
    from sieve import tier0

    lib, ids, pub, _ = built
    page = page_doc(lib, "public", pub.out, f"{ids['RED']}/1")
    seen = {}
    for name in ("web", "zoom"):
        with Image.open(pub.out / page["images"][name]["path"]) as im:
            seen[name] = tier0.text_of(tier0.recognize(im)).upper().replace(" ", "")
    assert "QUARTZREDA" in seen["zoom"]                      # sanity: Vision can read this page
    for name, text in seen.items():
        assert "ZIRCON" not in text and "HIDE" not in text, name
    with Image.open(lib.store.path_for(*_page_file(lib, f"{ids['RED']}/1"))) as original:
        assert "ZIRCONHIDE" in tier0.text_of(tier0.recognize(original)).upper().replace(" ", "")


def _page_file(lib, page_id):
    with lib.connect() as conn:
        r = conn.execute("SELECT page_sha256, page_ext FROM page WHERE id = %s", (page_id,)).fetchone()
    return r["page_sha256"], r["page_ext"]


# --- metadata and originals ------------------------------------------------------------------------------------

def test_no_metadata_survives_and_no_image_is_full_size(built):
    lib, ids, pub, mem = built
    with Image.open(lib.store.path_for(*_page_file(lib, f"{ids['PUB']}/1"))) as original:
        assert original.getexif().get_ifd(0x8825), "the fixture page should carry GPS"
    assert check_images(pub.out) == [] and check_images(mem.out) == []
    for f in pub.out.rglob("*.jpg"):
        with Image.open(f) as im:
            assert max(im.size) <= 0.9 * canary.H and not im.getexif()
    sizes = page_doc(lib, "public", pub.out, f"{ids['PUB']}/1")["images"]
    assert sizes["web"]["height"] == 1600 and sizes["zoom"]["height"] == 1980 and sizes["thumb"]["height"] == 300


# --- rebuilds: cache, unsharing, share log, diff ---------------------------------------------------------------

def test_rebuild_uses_the_cache_and_rerenders_only_what_changed(built):
    lib, ids, pub, _ = built
    assert pub.rendered == 12
    again = build(lib, "public", pub.out, today=TODAY)
    assert again.rendered == 0 and again.shared == [] and again.unshared == []
    with lib.connect() as conn:
        set_page_redaction(conn, lib.name, f"{ids['PUB']}/1", [{"type": "region", "x": 0.5, "y": 0.5, "w": 0.2,
                                                                   "h": 0.1}], "archivist")
    assert build(lib, "public", pub.out, today=TODAY).rendered == 3


def test_unsharing_removes_every_file_and_is_logged(built, tmp_path):
    lib, ids, pub, _ = built
    before = tmp_path / "before"
    shutil.copytree(pub.out, before)
    with lib.connect() as conn:
        set_visibility(conn, lib.name, "item", ids["PUB"], "private", "archivist")
    after = build(lib, "public", pub.out, today=TODAY)
    assert after.unshared == [ids["PUB"]] and after.items == 3
    assert scan_for_strings(pub.out, ["CANARY-PUB"]) == []
    d = diff(before, pub.out)
    item_oid, page_oid = (opaque_id(lib.name, "public", k, v) for k, v in
                          (("item", ids["PUB"]), ("page", ids["PUB"] + "/1")))
    assert set(d["removed"]) == {f"items/{item_oid}.json", f"pages/{page_oid}.json", f"images/{page_oid}/thumb.jpg",
                                 f"images/{page_oid}/web.jpg", f"images/{page_oid}/zoom.jpg"}
    assert set(d["changed"]) == {"feed.json", "log.jsonl"} and d["added"] == []
    with lib.connect() as conn:
        log = conn.execute("""SELECT predicate, value, source FROM assertion WHERE subject_id = %s
                              AND predicate LIKE 'feed.%%' AND value->>'feed' = 'public' ORDER BY id""",
                           (ids["PUB"],)).fetchall()
        assert [r["predicate"] for r in log] == ["feed.shared", "feed.unshared"]
        assert log[0]["source"] == "publisher" and "series newsletters: public" in log[0]["value"]["why"]
        assert conn.execute("SELECT count(*) n FROM outbox WHERE payload->>'event' = 'feed.built'").fetchone()["n"] == 3
    assert check_feed(lib, "public", pub.out) == []


def test_refuses_to_replace_a_directory_that_is_not_a_feed(built, tmp_path):
    lib, *_ = built
    other = tmp_path / "documents"
    other.mkdir()
    (other / "keep.txt").write_text("not a feed")
    with pytest.raises(PublishError, match="isn't a feed directory"):
        build(lib, "public", other, today=TODAY)
    assert (other / "keep.txt").exists()
    with pytest.raises(PublishError, match="no feed named"):
        build(lib, "everyone", tmp_path / "x", today=TODAY)


def test_a_page_with_invalid_redactions_is_left_out(built):
    lib, ids, pub, _ = built
    with lib.connect() as conn:
        conn.execute("""UPDATE page SET redaction = '[{"type": "region", "x": 2}]' WHERE id = %s""",
                     (f"{ids['PUB']}/1",))
    again = build(lib, "public", pub.out, today=TODAY)
    assert again.items == 3 and any("invalid redaction" in why for _, why in again.skipped)
    assert scan_for_strings(pub.out, ["CANARY-PUB"]) == []


# --- the leak checker catches planted leaks --------------------------------------------------------------------

def test_string_scan_finds_leaks_in_text_json_gzip_and_paths(tmp_path):
    (tmp_path / "a.txt").write_text("nothing here")
    assert scan_for_strings(tmp_path, ["Secret-One"]) == []
    (tmp_path / "plain.html").write_text("<p>secret-one</p>")
    (tmp_path / "escaped.json").write_text(json.dumps({"t": "sécret-two"}))         # \\u00e9 in the file
    (tmp_path / "index.pf_fragment").write_bytes(gzip.compress(b"... Secret-Three ..."))
    (tmp_path / "secret-four").mkdir()
    (tmp_path / "secret-four" / "x.txt").write_text("")
    found = scan_for_strings(tmp_path, ["Secret-One", "sécret-two", "secret-three", "Secret-Four"])
    assert any(p.startswith("plain.html") for p in found)
    assert any(p.startswith("escaped.json") and "unescaped" in p for p in found)
    assert any(p.startswith("index.pf_fragment (gzip)") for p in found)
    assert any("path contains" in p for p in found)


def test_check_feed_catches_each_kind_of_leak(built, tmp_path):
    lib, ids, pub, mem = built
    # an item, page and image from the members feed copied into the public one
    cap_item = opaque_id(lib.name, "members", "item", ids["CAP"])
    cap_page = opaque_id(lib.name, "members", "page", ids["CAP"] + "/1")
    shutil.copy(mem.out / "items" / f"{cap_item}.json", pub.out / "items")
    shutil.copy(mem.out / "pages" / f"{cap_page}.json", pub.out / "pages")
    shutil.copytree(mem.out / "images" / cap_page, pub.out / "images" / cap_page)
    # a byte-for-byte original, and an image with EXIF
    original = lib.store.path_for(*_page_file(lib, f"{ids['PUB']}/1"))
    pub_page = opaque_id(lib.name, "public", "page", ids["PUB"] + "/1")
    shutil.copy(original, pub.out / "images" / pub_page / "web.jpg")
    # the redacted page's image replaced by an unredacted render
    red_page = opaque_id(lib.name, "public", "page", ids["RED"] + "/1")
    with Image.open(lib.store.path_for(*_page_file(lib, f"{ids['RED']}/1"))) as im:
        im.resize((1236, 1600)).save(pub.out / "images" / red_page / "web.jpg", "JPEG")
    # redacted text put back
    pj = pub.out / "pages" / f"{red_page}.json"
    doc = json.loads(pj.read_text())
    doc["text"] = f"Call the secretary at {canary.HIDDEN_TEXT} today"
    pj.write_text(json.dumps(doc))
    (pub.out / "notes.txt").write_text("stray")

    problems = "\n".join(check_feed(lib, "public", pub.out))
    for expected in (f"items/{cap_item}.json: an item the feed must not carry",
                     f"pages/{cap_page}.json: a page the feed must not carry",
                     f"images/{cap_page}/web.jpg: an image of a page the feed must not carry",
                     f"images/{pub_page}/web.jpg: is a byte-for-byte copy of an original",
                     f"images/{pub_page}/web.jpg: (1700, 2200) is as large as the page image",
                     f"images/{pub_page}/web.jpg: carries EXIF",
                     f"images/{red_page}/web.jpg: a redacted region is not black",
                     f"pages/{red_page}.json: redacted text is still present",
                     "notes.txt: unexpected file"):
        assert expected in problems, expected


# --- redaction rules (no database) -----------------------------------------------------------------------------

def test_redact_text_cases():
    text = "Call 555-0199 or 555-0142 now"
    spans = [{"type": "text", "text_id": 7, "start": 5, "end": 13}, {"type": "text", "text_id": 7, "start": 17,
                                                                     "end": 25}]
    assert redact_text(text, 7, []) == (text, "ok")
    assert redact_text(text, 7, spans) == ("Call [redacted] or [redacted] now", "ok")
    overlap = spans + [{"type": "text", "text_id": 7, "start": 10, "end": 20}]
    assert redact_text(text, 7, overlap)[0] == "Call [redacted] now"
    region = [{"type": "region", "x": 0, "y": 0, "w": 0.5, "h": 0.5}]
    assert redact_text(text, 7, region)[0] is None                              # region, text never checked
    assert redact_text(text, 7, region + [{"type": "text_checked", "text_id": 7}]) == (text, "ok")
    assert redact_text(text, 8, spans)[0] is None                               # spans made against another text
    assert redact_text("short", 7, spans)[0] is None                            # span past the end


@pytest.mark.parametrize("entries, match", [
    ("nope", "are a list"),
    ([{"type": "blur"}], "type must be"),
    ([{"type": "region", "x": 0.9, "y": 0, "w": 0.5, "h": 0.1}], "inside the page"),
    ([{"type": "region", "x": 0, "y": 0, "w": 0, "h": 0.1}], "inside the page"),
    ([{"type": "region", "x": "a"}], "needs numbers"),
    ([{"type": "text", "text_id": 1, "start": 5, "end": 5}], "start < end"),
    ([{"type": "text_checked"}], "needs text_id"),
])
def test_redaction_validation(entries, match):
    with pytest.raises(RedactionError, match=match):
        redaction.validate(entries)


def test_redactions_must_point_at_the_pages_own_text(built):
    lib, ids, *_ = built
    with lib.connect() as conn:
        other = conn.execute("SELECT id FROM text WHERE page = %s", (f"{ids['MEM']}/1",)).fetchone()["id"]
        with pytest.raises(RedactionError, match="don't belong"):
            set_page_redaction(conn, lib.name, f"{ids['PUB']}/1",
                               [{"type": "text", "text_id": other, "start": 0, "end": 4}], "archivist")
        with pytest.raises(KeyError):
            set_page_redaction(conn, lib.name, "nope/1", [], "archivist")


# --- image pipeline (no database) ------------------------------------------------------------------------------

SPEC = images.ImageSpec(thumb=300, web=1600, zoom=3200, max_fraction=0.9, jpeg_quality=85, redaction_margin=0.004)


def test_target_sizes_never_upscale_or_reach_full_size():
    assert images.target_sizes((6000, 8000), SPEC) == {"thumb": (225, 300), "web": (1200, 1600), "zoom": (2400, 3200)}
    small = images.target_sizes((850, 1100), SPEC)
    assert small == {"thumb": (232, 300), "web": (765, 990)}          # zoom would equal web, so it's left out
    assert all(max(s) < 1100 for s in small.values())


def test_render_strips_exif_icc_and_applies_orientation(tmp_path):
    from PIL import ImageCms

    src = tmp_path / "rotated.jpg"
    im = Image.new("RGB", (400, 200), "white")
    exif = Image.Exif()
    exif[0x0112] = 6                                               # orientation: rotate 90° to display
    exif[0x010F] = "Apple"
    exif.get_ifd(0x8825)[2] = (41.0, 16.0, 0.0)
    icc = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    im.save(src, "JPEG", exif=exif, icc_profile=icc)
    out = images.render(src, [], SPEC)
    for name, (data, w, h) in out.items():
        with Image.open(io.BytesIO(data)) as o:
            assert o.size == (w, h) and h > w                      # displayed upright
            assert not o.getexif() and "icc_profile" not in o.info
        assert b"Exif" not in data and b"ICC_PROFILE" not in data and b"Apple" not in data


def test_burn_covers_the_region_plus_a_margin():
    im = Image.new("L", (1000, 1000), 255)
    out = images.burn(im, [[0.4, 0.4, 0.2, 0.2]], margin=0.01)
    assert out.getpixel((500, 500)) == 0 and out.getpixel((392, 392)) == 0      # 10 px margin + 1
    assert out.getpixel((380, 380)) == 255


# --- command line ----------------------------------------------------------------------------------------------

def test_cli_build_check_diff(built, tmp_path, monkeypatch, capsys):
    from athena.__main__ import main

    lib, ids, pub, _ = built
    cfg = tmp_path / "library.toml"
    cfg.write_text("""[library]
name = "canary"
[[collection]]
slug = "archive"
title = "The archive"
[[series]]
slug = "newsletters"
collection = "archive"
[[series]]
slug = "minutes"
collection = "archive"
[[series]]
slug = "directories"
collection = "archive"
[[series]]
slug = "letters"
collection = "archive"
[[policy]]
name = "unreviewed-private"
kind = "workflow"
cap = "private"
when = { status_not = "reviewed" }
[[policy]]
name = "directories-members"
kind = "content"
cap = "members"
when = { type_in = ["directory"] }
[[feed]]
name = "public"
tiers = ["public"]
""")
    for k, v in (("ATHENA_CONFIG", cfg), ("ATHENA_DATABASE_URL", lib.cfg.database_url),
                 ("ATHENA_FILES", lib.cfg.files), ("ATHENA_INTAKE", lib.cfg.intake)):
        monkeypatch.setenv(k, str(v))
    out = tmp_path / "cli-public"
    forbid = tmp_path / "forbid.txt"
    forbid.write_text("\n".join(canary.FORBIDDEN_PUBLIC))
    assert main(["publish", "build", "public", "--out", str(out), "--today", "2026-12-15", "--jobs", "2"]) == 0
    assert main(["publish", "check", "public", str(out), "--forbid-file", str(forbid)]) == 0
    forbid.write_text("CANARY-PUB-TITLE")
    assert main(["publish", "check", "public", str(out), "--forbid-file", str(forbid)]) == 1
    assert main(["publish", "diff", str(pub.out), str(out), "--json"]) == 0
    captured = capsys.readouterr()
    assert "feed public: 4 items, 4 pages, 12 images" in captured.out and "clean" in captured.err
    assert "contains 'CANARY-PUB-TITLE'" in captured.out
