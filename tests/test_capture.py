# SPDX-License-Identifier: GPL-3.0-or-later
"""The capture tool on synthetic session folders: sheets, page finding, measures, import, staging and sending."""
from __future__ import annotations

import json
import shutil
import sys
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest

cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")

from PIL import Image  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))
import synthetic_session as ss  # noqa: E402

from athena.__main__ import main  # noqa: E402
from athena.bundle import read_bundle, validate  # noqa: E402
from athena.capture import config as capture_config  # noqa: E402
from athena.capture import images, page, session, sheets, staging  # noqa: E402
from athena.capture.config import CaptureConfig, CaptureConfigError  # noqa: E402
from athena.capture.session import Options  # noqa: E402

PAGES = [2, 3, 1, 1]                 # pages per item in ss.DEFAULT_ITEMS


def make_cfg(tmp: Path, **kw) -> CaptureConfig:
    base = dict(staging=tmp / "staging", intake=tmp / "intake", collection="archive", min_ppi=100,
                min_ppi_photo=100, quick=False)
    return CaptureConfig(**{**base, **kw})


@pytest.fixture(scope="session")
def session_dir(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("capture") / "session-1"
    ss.make_session(root, gps=True)
    return root


def gray(rgb):
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)


# --- the printed sheets -------------------------------------------------------------------------------------------

def test_sheets_pdf_and_what_is_on_them(tmp_path):
    out = sheets.write_pdf(tmp_path / "sheets.pdf", "a4", tag_mm=40)
    data = out.read_bytes()
    assert data.startswith(b"%PDF") and data.count(b"/Type /Page\n") + data.count(b"/Type /Page ") >= 2
    assert sheets.find_separator(np.array(sheets.separator_sheet("letter", 150))) is not None
    tag = sheets.find_tag(np.array(sheets.scale_sheet("letter", 150, tag_mm=40)))
    side = np.mean([np.hypot(*(tag[i] - tag[(i + 1) % 4])) for i in range(4)])
    assert side == pytest.approx(40 / 25.4 * 150, rel=0.01)       # printed at the size the config says
    with pytest.raises(ValueError):
        sheets.separator_sheet("foolscap")


def test_separator_is_found_in_a_photograph_and_pages_are_not():
    frame = ss.photograph(ss.separator_page(150), ppi=150, seed=4, tag_mm=None, tilt=-6, keystone=0.08)
    assert sheets.find_separator(gray(frame)) is not None
    assert sheets.find_separator(gray(ss.photograph(ss.text_page(1), seed=4))) is None
    upside_down = np.ascontiguousarray(np.rot90(frame, 2))
    assert sheets.find_separator(gray(upside_down)) is not None


def test_slip_is_cut_out_upright_whichever_way_the_sheet_was_photographed():
    frame = ss.photograph(ss.separator_page(150, "Spring newsletter"), ppi=150, seed=4, tag_mm=None)
    for turned in (frame, np.ascontiguousarray(np.rot90(frame, 1)), np.ascontiguousarray(np.rot90(frame, 2))):
        slip = sheets.slip_from_separator(turned, sheets.find_separator(gray(turned)))
        h, w = slip.shape[:2]
        x0, y0, x1, y1 = sheets.SLIP_BOX_MM
        assert w / h == pytest.approx((x1 - x0) / (y1 - y0), rel=0.01)
        ink = (slip[..., 2].astype(int) - slip[..., 0] > 60)       # the "handwriting" is blue
        ys, xs = np.nonzero(ink)
        assert ink.sum() > 500 and ys.mean() < h * 0.4 and xs.mean() < w * 0.6     # top left, where it was written


# --- one frame ------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("ppi, zoom, tilt, keystone", [(150, 1.0, 2.5, 0.04), (150, 0.75, -4.0, 0.02),
                                                        (240, 1.0, 5.0, 0.07), (150, 1.0, -8.0, 0.1)])
def test_ppi_from_the_scale_card_is_within_5_percent(ppi, zoom, tilt, keystone):
    frame = ss.photograph(ss.text_page(2, ppi), ppi=ppi, seed=2, zoom=zoom, tilt=tilt, keystone=keystone)
    found, _ = page.look(frame)
    assert found.cropped and found.tag is not None
    truth = found.size[0] / (ss.PAGE_IN[0] * (1 + page.GROW))      # the page is 8.5 inches wide
    measured = page.measured_ppi(found, sheets.DEFAULT_TAG_MM)
    assert measured == pytest.approx(truth, rel=0.05)
    assert measured == pytest.approx(ppi * zoom * 0.93, rel=0.06)   # the scene is drawn at 93% of the frame
    # ... and the page comes out with a Letter sheet's proportions, however the camera was tilted
    assert found.size[0] / found.size[1] == pytest.approx(ss.PAGE_IN[0] / ss.PAGE_IN[1], rel=0.015)
    assert page.square_up(frame, found).shape[:2] == (found.size[1], found.size[0])


def test_no_scale_card_no_ppi_and_a_wrong_print_scale_is_corrected():
    frame = ss.photograph(ss.text_page(2), seed=2, tag_mm=None)
    assert page.measured_ppi(page.look(frame)[0], 50) is None
    found, _ = page.look(ss.photograph(ss.text_page(2), seed=2))
    assert page.measured_ppi(found, 50 * 0.97) == pytest.approx(page.measured_ppi(found, 50) / 0.97, rel=0.001)


def test_kind_is_guessed_from_the_page():
    kinds = {}
    for name, drawn in (("text", ss.text_page(3)), ("photo", ss.photo_page(3)), ("blank", ss.blank_page(3))):
        found, small = page.look(ss.photograph(drawn, seed=3))
        assert found.cropped, name
        kinds[name] = page.measure(small).kind
    assert kinds == {"text": "text", "photo": "photo", "blank": "blank"}


def test_measures_tell_a_good_capture_from_a_bad_one():
    def m(**opts):
        return page.measure(page.look(ss.photograph(ss.text_page(5), seed=5, **opts))[1])

    good, blurred, glare, uneven = m(), m(blur=3.0), m(glare=True), m(uneven=1.1)
    assert good.sharpness > 1.2 and good.glare < 0.002 and good.evenness > 0.93 and 200 < good.brightness < 250
    assert blurred.sharpness < 0.5 and blurred.laplacian_var < 25 < good.laplacian_var     # OCRX's target agrees
    assert glare.glare > 0.05
    assert uneven.evenness < 0.75
    photo = page.measure(page.look(ss.photograph(ss.photo_page(5), seed=5))[1])
    soft_photo = page.measure(page.look(ss.photograph(ss.photo_page(5), seed=5, blur=3.0))[1])
    assert photo.sharpness > 0.1 > soft_photo.sharpness
    assert photo.evenness is None or photo.kind == "photo"         # a picture's tones are not the lighting
    blank = page.measure(page.look(ss.photograph(ss.blank_page(5), seed=5))[1])
    assert blank.sharpness is None and blank.kind == "blank" and blank.evenness > 0.93


def test_a_page_running_off_the_frame_is_not_cropped():
    found, _ = page.look(ss.photograph(ss.text_page(1), seed=1, cut_off=True))
    assert found.cut_off and not found.cropped


def test_a_scan_that_fills_the_frame_is_left_alone():
    found, _ = page.look(ss.text_page(1, 200))
    assert not found.cropped and not found.cut_off and found.size == (1700, 2200)


HISTORICAL = ("mimeo_newsletter", "typed_minutes", "news_clipping", "program_page", "handwritten_note")


def historical(name: str, ppi: int = 150):
    """A made-up page in a historical style (phase0/gen_testset.py): faint, soft-inked, yellowed or with a picture."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "phase0"))
    import gen_testset
    rgb = gen_testset.render_historical(name)[1]
    return cv2.resize(rgb, (round(8.5 * ppi), round(11 * ppi)), interpolation=cv2.INTER_AREA)


@pytest.mark.parametrize("name", HISTORICAL)
def test_old_materials_pass_when_captured_well_and_fail_when_blurred(name):
    cfg = CaptureConfig(min_ppi=100)

    def flags(frame, camera):
        found, small = page.look(frame)
        m = page.measure(small)
        metrics = {"sharpness": m.sharpness, "sharpness_min": m.sharpness_min, "soft_where": m.soft_where,
                   "glare": m.glare, "evenness": m.evenness, "brightness": m.brightness, "keystone": found.keystone,
                   "cut_off": found.cut_off}
        return found, session.judge(cfg, metrics, m.kind, 150.0, camera, found.cropped)[0]

    drawn = historical(name)
    found, problems = flags(drawn, camera=False)                   # a clean scan
    assert not found.cropped and problems == []
    found, problems = flags(ss.photograph(drawn, seed=6), camera=True)
    assert found.cropped and problems == []
    _, problems = flags(ss.photograph(drawn, seed=6, blur=3.0), camera=True)
    assert len(problems) == 1 and problems[0].startswith("blurred")
    _, problems = flags(ss.photograph(drawn, seed=6, uneven=1.1), camera=True)
    assert any(f.startswith("uneven light") for f in problems)    # (faint ink in bad light may read as soft too)


def test_a_scan_with_a_picture_on_it_is_not_cropped_to_the_picture():
    drawn = historical("program_page")                             # a large photograph in the middle of the page
    found, small = page.look(drawn)
    assert not found.cropped and found.size == (drawn.shape[1], drawn.shape[0])
    assert page.measure(small).kind in ("text", "mixed")
    on_table, _ = page.look(ss.photograph(drawn, seed=2))          # ... but the page itself is still found on a table
    assert on_table.cropped and on_table.size[0] / on_table.size[1] == pytest.approx(8.5 / 11, rel=0.02)
    dark_print, _ = page.look(ss.photograph(ss.photo_page(2), seed=2))
    assert dark_print.cropped                                      # and so is a photographic print


def test_judge_names_each_problem():
    cfg = CaptureConfig()
    ok = {"sharpness": 2.5, "sharpness_min": 2.0, "soft_where": "left", "glare": 0.0, "evenness": 0.95,
          "brightness": 230.0, "keystone": 0.02, "cut_off": False}

    def flags(kind="text", ppi=450.0, camera=True, cropped=True, **changed):
        return session.judge(cfg, {**ok, **changed}, kind, ppi, camera, cropped)

    assert flags() == ([], [])
    assert "blurred" in flags(sharpness=0.4)[0][0]
    assert "soft at the lower right" in flags(sharpness_min=0.3, soft_where="lower right")[0][0]
    assert "glare on 4%" in flags(glare=0.04)[0][0]
    assert flags(glare=0.9, camera=False)[0] == []                 # a scanner's white is not glare
    assert "uneven light" in flags(evenness=0.6)[0][0]
    assert "too dark" in flags(brightness=110.0)[0][0]
    assert "not square" in flags(keystone=0.15)[0][0]
    assert "runs off the edge" in flags(cut_off=True)[0][0]
    assert flags(ppi=310.0)[0] == ["ppi 310 < 400"]
    assert flags(kind="photo", ppi=450.0, sharpness=0.4)[0] == ["ppi 450 < 600"]
    assert flags(kind="blank", sharpness=None, sharpness_min=None) == ([], [])
    assert flags(sharpness=None, sharpness_min=None) == ([], ["too little on the page to judge sharpness"])
    assert flags(kind="mixed", sharpness_min=0.2, evenness=None) == ([], [])     # soft pictures beside sharp type
    assert "blurred" in flags(kind="photo", ppi=700.0, sharpness=0.05)[0][0]
    assert flags(ppi=None) == ([], ["resolution not measured (no scale card in the frame)"])
    assert "page edges not found" in flags(cropped=False)[1][0]


# --- checking a session ---------------------------------------------------------------------------------------------

def test_check_lists_the_blurred_and_the_glared_page_and_passes_the_rest(session_dir, tmp_path):
    s = session.survey(session_dir, make_cfg(tmp_path))
    assert (s.frames, s.separators, [len(i.pages) for i in s.items]) == (11, 4, PAGES)
    assert [p.kind for i in s.items for p in i.pages] == ["text", "text", "text", "text", "blank", "photo", "text"]
    found = {(i.number, p.n): p.flags for i, p in s.reshoots()}
    assert set(found) == {(2, 2), (4, 1)}
    assert "blurred" in found[(2, 2)][0] and "glare" in found[(4, 1)][0]
    text = session.report(s)
    assert "Re-shoot list: 2 pages" in text and "IMG_0006.jpg  item 2 page 2: blurred" in text
    assert not (tmp_path / "staging").exists()                     # checking writes nothing
    assert all(p.ppi_source == "apriltag" and p.ppi for i in s.items for p in i.pages)


def test_check_with_the_real_thresholds_flags_low_resolution(session_dir, tmp_path):
    s = session.survey(session_dir, CaptureConfig(quick=False))
    assert len(s.reshoots()) == 7 and all(any("ppi" in f for f in p.flags) for _, p in s.reshoots())


def test_several_frames_at_once_gives_the_same_answer(session_dir, tmp_path):
    one = session.as_data(session.survey(session_dir, make_cfg(tmp_path)))
    two = session.as_data(session.survey(session_dir, make_cfg(tmp_path), Options(jobs=2)))
    assert one == two


# --- importing ------------------------------------------------------------------------------------------------------

def test_import_writes_one_valid_bundle_per_item(session_dir, tmp_path):
    cfg = make_cfg(tmp_path, station="desk-1", operator="station-1")
    expected = ss.make_session(tmp_path / "again", gps=True)       # the same session, for its file names
    s = session.import_session(session_dir, cfg, Options(series="newsletters"))
    bundles = staging.staged(cfg.staging)
    assert [len(read_bundle(b).manifest["pages"]) for b in bundles] == PAGES
    assert [i.bundle for i in s.items] == [b.name for b in bundles]
    for b, item, want in zip(bundles, s.items, expected):
        report = validate(b, series={"newsletters"}, collections={"archive"})
        assert report.ok and not report.warnings, str(report)
        m = read_bundle(b).manifest
        assert m["station"] == {"name": "desk-1", "software": "athena-capture", "version": "0.1.0"}
        assert m["operator"] == "station-1" and m["source"] == "camera"
        assert m["device"] == {"make": "Synthetic", "model": "Renderer 1"}
        assert m["describe"] == {"collection": "archive", "series": "newsletters", "item": {}}
        assert m["slip"] == "slip.jpg" and m["quality"] == "quality.json"
        for p, name in zip(read_bundle(b).pages, want["pages"]):
            assert p.raw.read_bytes() == (session_dir / name).read_bytes()      # the master, byte for byte
            assert p.has_gps and p.ppi_source == "apriltag" and p.side == "recto"
            with Image.open(p.page) as im:
                assert im.size == (p.width, p.height) and not im.getexif()      # no location in the page image
            with Image.open(p.raw) as im:
                assert im.getexif().get_ifd(0x8825)                             # ... it stays in the master only
        with Image.open(b / "slip.jpg") as im:
            assert not im.getexif()
        quality = json.loads((b / "quality.json").read_text())
        assert set(quality) == {str(p["n"]) for p in m["pages"]}
    q2 = json.loads((bundles[1] / "quality.json").read_text())
    assert "blurred" in q2["2"]["flags"][0] and q2["1"]["flags"] == [] and q2["3"]["kind_guess"] == "blank"
    assert not (cfg.staging / staging.WORK).exists()
    assert not [p for p in cfg.staging.iterdir() if p.name.endswith(".partial")]

    stale = cfg.staging / staging.WORK / "interrupted"             # what a killed import would leave behind
    stale.mkdir(parents=True)
    import os
    os.utime(stale, (0, 0))
    session.import_session(session_dir, cfg)
    assert not (cfg.staging / staging.WORK).exists()


def test_importing_again_adds_nothing_and_a_grown_session_adds_only_the_new_item(session_dir, tmp_path):
    cfg = make_cfg(tmp_path)
    work = tmp_path / "session"
    shutil.copytree(session_dir, work)
    session.import_session(work, cfg)
    before = [b.name for b in staging.staged(cfg.staging)]
    again = session.import_session(work, cfg)
    assert [b.name for b in staging.staged(cfg.staging)] == before
    assert [i.status for i in again.items] == ["imported"] * 4 and [i.bundle for i in again.items] == before
    assert "already imported" in session.report(again, imported=True)

    ss.make_session(work, [{"slip": "Late find", "pages": [("text", {})]}], first=40,
                    start=ss.START + timedelta(hours=1))
    grown = session.import_session(work, cfg)
    assert [i.status for i in grown.items] == ["imported"] * 4 + ["new"]
    assert len(staging.staged(cfg.staging)) == 5


def test_a_changed_item_is_a_conflict_until_its_bundle_is_discarded(tmp_path):
    cfg = make_cfg(tmp_path)
    work = tmp_path / "session"
    ss.make_session(work, [{"pages": [("text", {}), ("text", {})]}], layout="folders")
    first = session.import_session(work, cfg).items[0]
    assert first.separator is None and read_bundle(cfg.staging / first.bundle).manifest.get("slip") is None
    ss.save_photo(work / "item-01" / "IMG_0099.jpg", ss.photograph(ss.text_page(9), seed=9),
                  ss.START + timedelta(hours=2))
    changed = session.import_session(work, cfg)
    assert changed.items[0].status == "conflict" and first.bundle in changed.items[0].detail
    assert "NOT IMPORTED" in session.report(changed) and len(staging.staged(cfg.staging)) == 1
    assert staging.discard(cfg.staging, first.bundle) and not staging.discard(cfg.staging, first.bundle)
    redone = session.import_session(work, cfg)
    assert redone.items[0].status == "new" and len(read_bundle(cfg.staging / redone.items[0].bundle).pages) == 3


def test_folders_named_after_a_series_place_their_items(tmp_path):
    cfg = make_cfg(tmp_path, collection=None, known_collections=("archive", "loans"),
                   known_series=(("newsletters", "Newsletters", "archive"), ("on-loan", "On loan", "loans")))
    work = tmp_path / "session"
    ss.make_session(work / "Newsletters", [{"pages": [("text", {})]}, {"pages": [("text", {})]}], layout="folders")
    ss.make_session(work / "on-loan", [{"slip": "x", "pages": [("text", {})]}], first=20)
    ss.make_session(work / "odds", [{"pages": [("text", {})]}], layout="folders", first=30)
    with pytest.raises(ValueError, match="no collection"):
        session.import_session(work, cfg)
    s = session.import_session(work, replace(cfg, collection="archive"))
    placed = {i.folder: (i.series, read_bundle(cfg.staging / i.bundle).collection) for i in s.items}
    assert placed == {"Newsletters/item-01": ("newsletters", "archive"), "Newsletters/item-02": ("newsletters", "archive"),
                      "on-loan": ("on-loan", "loans"), "odds/item-01": ("unsorted", "archive")}


def test_pages_follow_capture_time_unless_told_to_follow_names(tmp_path):
    work = tmp_path / "session"
    names = ["c.jpg", "a.jpg", "b.jpg"]                            # taken in this order
    for i, name in enumerate(names):
        ss.save_photo(work / name, ss.photograph(ss.text_page(i), seed=i), ss.START + timedelta(seconds=10 * i))
    cfg = make_cfg(tmp_path)
    assert [p.shot.rel for p in session.survey(work, cfg).items[0].pages] == names
    assert [p.shot.rel for p in session.survey(work, cfg, Options(order="name")).items[0].pages] == sorted(names)
    ss.save_photo(work / "d2.jpg", ss.photograph(ss.text_page(7), seed=7), None, camera=False)
    ss.save_photo(work / "d10.jpg", ss.photograph(ss.text_page(8), seed=8), None, camera=False)
    mixed = [p.shot.rel for p in session.survey(work, cfg).items[0].pages]
    assert mixed == ["a.jpg", "b.jpg", "c.jpg", "d2.jpg", "d10.jpg"]          # no time on some: by name, 2 before 10


def test_scans_keep_their_resolution_and_are_not_cropped(tmp_path):
    work = tmp_path / "session"
    ss.flatbed_scan(work / "letter" / "scan-1.tif", seed=1, dpi=300)
    ss.flatbed_scan(work / "letter" / "scan-2.png", seed=2, dpi=300)
    cfg = make_cfg(tmp_path, min_ppi=400)
    s = session.import_session(work, cfg, Options(duplex=True))
    b = read_bundle(cfg.staging / s.items[0].bundle)
    assert b.manifest["source"] == "flatbed" and "device" not in b.manifest
    assert [(p.ppi, p.ppi_source, p.width, p.height, p.side) for p in b.pages] == [
        (300.0, "scanner", 2550, 3300, "recto"), (300.0, "scanner", 2550, 3300, "verso")]
    assert [p.raw.suffix for p in b.pages] == [".tif", ".png"] and validate(b.path).ok
    assert all(p.flags == ["ppi 300 < 400"] and p.notes == [] for p in s.items[0].pages)


def test_declared_ppi_and_source_and_rotation(tmp_path):
    work = tmp_path / "session"
    frame = ss.photograph(ss.text_page(1), seed=1, tag_mm=None)
    ss.save_photo(work / "IMG_0001.jpg", np.ascontiguousarray(np.rot90(frame, 1)), ss.START)   # lying sideways
    cfg = make_cfg(tmp_path)
    plain = session.survey(work, cfg).items[0].pages[0]
    assert plain.width > plain.height and plain.ppi is None and plain.ppi_source == "unknown"
    s = session.import_session(work, cfg, Options(ppi=350, source="import", rotate=90))
    b = read_bundle(cfg.staging / s.items[0].bundle)
    p = b.pages[0]
    assert (p.ppi, p.ppi_source, b.manifest["source"]) == (350.0, "declared", "import") and p.height > p.width


def test_files_that_are_not_pages(tmp_path):
    work = tmp_path / "session"
    ss.make_session(work, [{"pages": [("text", {})]}], layout="folders")
    (work / "item-01" / "notes.txt").write_text("made-up notes")
    (work / "item-01" / ".DS_Store").write_bytes(b"x")
    (work / "item-01" / "IMG_0500.dng").write_bytes(b"not really a raw file")
    (work / ".hidden").mkdir()
    shutil.copy(work / "item-01" / "IMG_0001.jpg", work / ".hidden" / "IMG_0001.jpg")
    cfg = make_cfg(tmp_path)
    s = session.survey(work, cfg)
    assert s.frames == 1 and s.skipped == [("item-01/IMG_0500.dng", images.NOT_YET["dng"])]
    assert "skipped item-01/IMG_0500.dng" in session.report(s)

    good = (work / "item-01" / "IMG_0001.jpg").read_bytes()
    for damaged in (b"\xff\xd8 not a picture at all", good[:3000]):           # unrecognisable; cut short
        (work / "item-01" / "IMG_0600.jpg").write_bytes(damaged)
        broken = session.import_session(work, cfg)
        assert broken.items[0].status == "unreadable" and "IMG_0600.jpg" in broken.items[0].detail
        assert broken.items[0].bundle is None and broken.problems == 1 and "NOT IMPORTED" in session.report(broken)
        assert staging.staged(cfg.staging) == []                   # an item with a page missing isn't imported
    (work / "item-01" / "IMG_0600.jpg").unlink()
    assert session.import_session(work, cfg).items[0].status == "new"
    with pytest.raises(NotADirectoryError):
        session.survey(work / "nowhere", cfg)


@pytest.mark.filterwarnings("ignore:Corrupt EXIF data")
def test_a_16_bit_colour_tiff_is_read(tmp_path):
    deep = (ss.text_page(1, 100).astype(np.uint16) * 257)[:, :, ::-1]            # 16 bits a channel, as scanners offer
    assert cv2.imwrite(str(tmp_path / "deep.tif"), deep)
    rgb = images.load_rgb(tmp_path / "deep.tif")
    assert rgb.dtype == np.uint8 and rgb.shape == (1100, 850, 3)
    assert np.abs(rgb.astype(int) - ss.text_page(1, 100)).max() <= 1
    (tmp_path / "broken.tif").write_bytes(b"II*\x00 not a tiff")
    with pytest.raises(images.Unreadable):
        images.load_rgb(tmp_path / "broken.tif")


@pytest.mark.skipif(not images.HEIF, reason="needs pillow-heif")
def test_heic_from_a_phone(tmp_path):
    work = tmp_path / "session"
    made = ss.make_session(work, [{"slip": "A", "pages": [("text", {})]}], ext="heic", gps=True)
    cfg = make_cfg(tmp_path)
    s = session.import_session(work, cfg)
    b = read_bundle(cfg.staging / s.items[0].bundle)
    p = b.pages[0]
    assert p.raw.name == "001.heic" and p.raw.read_bytes() == (work / made[0]["pages"][0]).read_bytes()
    assert p.has_gps and p.page.suffix == ".jpg" and b.manifest["slip"] == "slip.jpg" and validate(b.path).ok
    assert b.manifest["device"]["make"] == "Synthetic"


@pytest.mark.macos
def test_apple_vision_lines_go_in_the_bundle(tmp_path):
    work = tmp_path / "session"
    ss.make_session(work, [{"pages": [("text", {}), ("blank", {})]}], layout="folders", ppi=200)
    cfg = make_cfg(tmp_path, quick=True)
    s = session.import_session(work, cfg)
    m = read_bundle(cfg.staging / s.items[0].bundle).manifest
    assert m["pages"][0]["quick"] == "quick/001.json" and "quick" not in m["pages"][1]      # nothing to read on a blank
    lines = json.loads((cfg.staging / m["id"] / "quick" / "001.json").read_text())
    assert "GARDEN" in " ".join(ln["text"] for ln in lines).upper() and {"x", "y", "w", "h", "confidence"} <= set(lines[0])


def test_without_apple_vision_there_is_simply_no_quick_text(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "Vision", None)               # as on Linux: the import fails
    work = tmp_path / "session"
    ss.make_session(work, [{"pages": [("text", {})]}], layout="folders")
    cfg = make_cfg(tmp_path, quick=True)
    s = session.import_session(work, cfg)
    assert "quick" not in read_bundle(cfg.staging / s.items[0].bundle).manifest["pages"][0]


# --- staging: describing, sending -----------------------------------------------------------------------------------

@pytest.fixture
def staged_cfg(session_dir, tmp_path) -> CaptureConfig:
    cfg = make_cfg(tmp_path)
    session.import_session(session_dir, cfg)
    return cfg


def test_redescribe_changes_the_description_and_nothing_else(staged_cfg):
    b = staging.staged(staged_cfg.staging)[0]
    files_before = read_bundle(b).manifest["files"]
    staging.redescribe(b, collection="archive", series="newsletters",
                       item={"title": " Spring newsletter ", "date": "1962-21", "creator": "", "tags": ["flower-show"],
                             "physical": {"container": "box 1", "format": " "}, "notes": ""},
                       pages={2: {"side": "verso", "kind": "handwriting", "ppi": 9999}})
    m = read_bundle(b).manifest
    assert m["describe"] == {"collection": "archive", "series": "newsletters",
                             "item": {"title": "Spring newsletter", "date": "1962-21", "tags": ["flower-show"],
                                      "physical": {"container": "box 1"}}}
    assert (m["pages"][1]["side"], m["pages"][1]["kind"]) == ("verso", "handwriting") and m["pages"][1]["ppi"] != 9999
    assert m["files"] == files_before and validate(b).ok
    for bad, why in (({"date": "sometime"}, "date"), ({"type": "Not A Slug"}, "type"), ({"tags": ["a", "a"]}, "tags")):
        with pytest.raises(staging.DescribeError, match=why):
            staging.redescribe(b, collection="archive", series="newsletters", item=bad)
    with pytest.raises(staging.DescribeError):
        staging.redescribe(b, collection="archive", series="Bad Series", item={})
    assert read_bundle(b).manifest == m and not (b / "manifest.json.tmp").exists()


def test_send_moves_bundles_into_intake_and_remembers_them(staged_cfg, session_dir):
    cfg = staged_cfg
    ids = [b.name for b in staging.staged(cfg.staging)]
    results = staging.send(cfg.staging, cfg.intake, [ids[0], "01ARZ3NDEKTSV4RRFFQ69G5FAV"], series={"newsletters"})
    assert results[0] == ("01ARZ3NDEKTSV4RRFFQ69G5FAV", "invalid", "no such bundle in staging")
    assert results[1][:2] == (ids[0], "sent") and "unknown series 'unsorted'" in results[1][2]
    assert validate(cfg.intake / ids[0]).ok and not (cfg.staging / ids[0]).exists()
    assert [r[1] for r in staging.send(cfg.staging, cfg.intake)] == ["sent"] * 3
    assert staging.staged(cfg.staging) == [] and sorted(p.name for p in cfg.intake.iterdir()) == ids
    again = session.import_session(session_dir, cfg)               # sent bundles still count as imported
    assert [i.status for i in again.items] == ["imported"] * 4 and staging.staged(cfg.staging) == []


def test_send_to_another_disk_copies_checks_then_renames(staged_cfg, monkeypatch):
    cfg = staged_cfg
    ids = [b.name for b in staging.staged(cfg.staging)]
    real = staging.os.rename

    def cross_device(src, dst):
        if Path(src).parent == cfg.staging:
            raise OSError(18, "Invalid cross-device link")
        return real(src, dst)

    monkeypatch.setattr(staging.os, "rename", cross_device)
    assert [r[1] for r in staging.send(cfg.staging, cfg.intake, ids[:1])] == ["sent"]
    assert validate(cfg.intake / ids[0]).ok and not (cfg.staging / ids[0]).exists()
    assert not [p for p in cfg.intake.iterdir() if p.name.endswith(".partial")]

    real_copy = shutil.copytree

    def corrupting_copy(src, dst, *args, **kw):                    # a copy that goes wrong must not reach the library
        real_copy(src, dst, *args, **kw)
        if (Path(dst) / "pages" / "001.jpg").exists():             # (copytree calls itself for sub-folders)
            (Path(dst) / "pages" / "001.jpg").write_bytes(b"damaged")

    monkeypatch.setattr(staging.shutil, "copytree", corrupting_copy, raising=True)
    bad = staging.send(cfg.staging, cfg.intake, ids[1:2])
    assert bad[0][1] == "invalid" and "didn't match" in bad[0][2]
    assert (cfg.staging / ids[1]).is_dir() and not (cfg.intake / ids[1]).exists()
    assert not [p for p in cfg.intake.iterdir() if p.name.endswith(".partial")]


def test_send_refuses_what_is_already_there_or_no_longer_valid(staged_cfg):
    cfg = staged_cfg
    a, b = staging.staged(cfg.staging)[:2]
    (cfg.intake / "ingested" / a.name).mkdir(parents=True)
    (b / "pages" / "001.jpg").write_bytes(b"changed after import")
    results = dict((r[0], r[1]) for r in staging.send(cfg.staging, cfg.intake, [a.name, b.name]))
    assert results == {a.name: "exists", b.name: "invalid"} and a.is_dir() and b.is_dir()


# --- settings -------------------------------------------------------------------------------------------------------

TOML = """
[library]
name = "demo"
database_url = "postgresql:///unused"
files = "{root}/files"
intake = "{root}/intake"

[[collection]]
slug = "archive"

[[series]]
slug = "newsletters"
collection = "archive"
title = "Newsletters"

[vocabulary]
types = ["newsletter", "photograph"]

[capture]
station = "desk-1"
staging = "{root}/staging"
min_ppi = 100
min_ppi_photo = 100
quick = false
"""


def test_settings_from_the_config_file_and_the_environment(tmp_path):
    path = tmp_path / "library.toml"
    path.write_text(TOML.format(root=tmp_path))
    cfg = capture_config.load(path, env={})
    assert (cfg.station, cfg.staging, cfg.intake) == ("desk-1", tmp_path / "staging", tmp_path / "intake")
    assert cfg.collection == "archive" and cfg.series == "unsorted" and cfg.types == ("newsletter", "photograph")
    assert cfg.series_slugs() == {"newsletters"} and cfg.collection_for("newsletters") == "archive"
    env = {"ATHENA_CAPTURE_STAGING": str(tmp_path / "s2"), "ATHENA_INTAKE": str(tmp_path / "i2"),
           "ATHENA_CONFIG": str(path)}
    cfg = capture_config.load(env=env)
    assert (cfg.staging, cfg.intake) == (tmp_path / "s2", tmp_path / "i2")
    assert capture_config.load(env={}).staging is None or Path("library.toml").is_file()      # works with no file
    with pytest.raises(CaptureConfigError, match="no config file"):
        capture_config.load(tmp_path / "missing.toml", env={})
    assert capture_config.override(cfg, station="other", collection=None).station == "other"

    from athena.library import config as library_config
    assert library_config.load(path, env={}).name == "demo"       # the library accepts the section ...
    path.write_text(TOML.format(root=tmp_path) + 'sharpness = 3\n')
    with pytest.raises(library_config.ConfigError, match="unknown keys"):
        library_config.load(path, env={})                          # ... and catches mistakes in it


@pytest.mark.parametrize("section, why", [
    ({"station": "Front Desk"}, "slug"), ({"min_ppi": "400"}, "number"), ({"max_glare": 3}, "from 0 to 1"),
    ({"jpeg_quality": 50}, "70 to 100"), ({"quick": "yes"}, "true or false"), ({"tag_mm": True}, "number"),
    ({"colour": "red"}, "unknown keys"),
])
def test_bad_settings_are_refused(section, why):
    with pytest.raises(CaptureConfigError, match=why):
        capture_config.from_dict({"capture": section}, env={})


# --- the command line -----------------------------------------------------------------------------------------------

def test_command_line_from_check_to_send(session_dir, tmp_path, capsys, monkeypatch):
    path = tmp_path / "library.toml"
    path.write_text(TOML.format(root=tmp_path))
    monkeypatch.setenv("ATHENA_CONFIG", str(path))
    monkeypatch.delenv("ATHENA_CAPTURE_STAGING", raising=False)
    monkeypatch.delenv("ATHENA_INTAKE", raising=False)

    assert main(["capture", "sheets", "--out", str(tmp_path / "sheets.pdf")]) == 0
    assert (tmp_path / "sheets.pdf").read_bytes().startswith(b"%PDF")
    capsys.readouterr()

    assert main(["capture", "check", str(session_dir), "--jobs", "1", "--json"]) == 1      # two pages to shoot again
    data = json.loads(capsys.readouterr().out)
    assert [len(i["pages"]) for i in data["items"]] == PAGES
    assert sum(bool(p["flags"]) for i in data["items"] for p in i["pages"]) == 2

    assert main(["capture", "import", str(session_dir), "--jobs", "1", "--series", "newsletters"]) == 0
    out = capsys.readouterr().out
    assert "4 new bundles" in out and "Re-shoot list: 2 pages" in out
    assert main(["capture", "import", str(session_dir), "--jobs", "1"]) == 0
    assert "0 new bundles" in capsys.readouterr().out

    assert main(["capture", "staged"]) == 0
    listed = capsys.readouterr().out.splitlines()
    assert len(listed) == 4 and "(not described yet)" in listed[0] and "1 to shoot again" in listed[1]
    first = listed[0].split()[0]
    assert main(["capture", "discard", first, "01ARZ3NDEKTSV4RRFFQ69G5FAV"]) == 1
    assert main(["capture", "send"]) == 0
    assert "3 sent" in capsys.readouterr().err
    assert main(["bundle", "validate", *map(str, sorted((tmp_path / "intake").iterdir())), "--series", "newsletters",
                 "--collections", "archive"]) == 0
    assert main(["capture", "send", first]) == 1                   # gone


def test_command_line_says_what_is_missing(tmp_path, capsys, monkeypatch):
    for name in ("ATHENA_CONFIG", "ATHENA_CAPTURE_STAGING", "ATHENA_INTAKE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "s").mkdir()
    assert main(["capture", "import", "s"]) == 2 and "no staging directory" in capsys.readouterr().err
    assert main(["capture", "import", "s", "--staging", "st"]) == 2 and "no collection" in capsys.readouterr().err
    assert main(["capture", "send", "--staging", "st"]) == 2 and "no intake directory" in capsys.readouterr().err
    assert main(["capture", "check", "nowhere"]) == 2 and "not a folder" in capsys.readouterr().err
    assert main(["capture", "check", "s"]) == 0 and "0 items" in capsys.readouterr().out
