# SPDX-License-Identifier: GPL-3.0-or-later
"""Capture bundles: write → read → validate, and every way a bundle must be refused."""
import json
import os
import random
import shutil
from pathlib import Path

import pytest
from fixtures.make_bundles import STATION, draw_page, example_items, make_bundle, make_fixture_bundles

from athena.bundle import (BundleError, PageInput, is_separator_payload, read_bundle, schema, validate,
                           write_bundle, SEPARATOR_PAYLOAD)
from athena.bundle import ulid


@pytest.fixture
def bundles(tmp_path):
    return make_fixture_bundles(tmp_path / "intake", tmp_path / "work")


@pytest.fixture
def bundle(bundles):
    return bundles[0]  # the two-page newsletter


def manifest(b: Path) -> dict:
    return json.loads((b / "manifest.json").read_text())


def rewrite(b: Path, m: dict) -> None:
    (b / "manifest.json").write_text(json.dumps(m))


# --- round trip ------------------------------------------------------------------------------------------------

def test_fixture_bundles_validate(bundles):
    for b in bundles:
        rep = validate(b, collections={"archive"}, series={"newsletters", "clippings", "photographs"},
                       types={"newsletter", "clipping", "photograph"})
        assert rep.ok and not rep.warnings, str(rep)


def test_read_bundle_exposes_pages_and_date(bundle):
    b = read_bundle(bundle)
    assert b.id == bundle.name and ulid.is_ulid(b.id)
    assert (b.collection, b.series) == ("archive", "newsletters")
    assert b.date.earliest.month == 3 and b.date.latest.month == 5   # spring 1962
    assert b.created_at.tzinfo is not None
    p1, p2 = b.pages
    assert (p1.n, p1.side, p2.side) == (1, "recto", "verso")
    assert p1.raw.exists() and p1.page.exists() and p1.quick.exists()
    assert len(p1.raw_sha256) == 64 and p1.raw_sha256 != p1.page_sha256


def test_raw_files_are_byte_identical_to_the_source(tmp_path):
    spec = example_items()[1]
    b = make_bundle(tmp_path / "out", spec, tmp_path / "work")
    assert (b / "raw/001.jpg").read_bytes() == (tmp_path / "work/IMG_0001.jpg").read_bytes()


def test_undated_item_is_fine(tmp_path):
    spec = dict(example_items()[1])
    spec["item"] = {k: v for k, v in spec["item"].items() if k != "date"}
    b = make_bundle(tmp_path / "out", spec, tmp_path / "work")
    assert validate(b).ok and read_bundle(b).date is None


def test_minimal_bundle_without_optional_files(tmp_path):
    b = make_bundle(tmp_path / "out", example_items()[1], tmp_path / "work", with_quick=False, slip=False)
    m = manifest(b)
    assert "slip" not in m and "quick" not in m["pages"][0]
    assert validate(b).ok


def test_random_bundles_all_validate(tmp_path):
    rng = random.Random(7)
    sides, kinds = ["recto", "verso", "spread"], ["text", "photo", "handwriting", "mixed", "blank"]
    for i in range(20):
        n_pages = rng.randint(1, 5)
        spec = {"series": rng.choice(["minutes", "programs", "yearbooks"]), "source": rng.choice(["camera", "import"]),
                "item": {"title": f"Item {i}", "date": rng.choice(["1951", "196X", "1970-21", "1980/1985", "1975~"])},
                "pages": [(rng.choice(sides), rng.choice(kinds), [f"line {j}"]) for j in range(n_pages)]}
        b = make_bundle(tmp_path / "out", spec, tmp_path / f"work{i}", with_quick=rng.random() < 0.5,
                        slip=rng.random() < 0.5)
        assert validate(b).ok, str(validate(b))
        assert len(read_bundle(b).pages) == n_pages


def test_schema_is_valid_json_schema():
    import jsonschema
    jsonschema.Draft202012Validator.check_schema(schema())


# --- writer ----------------------------------------------------------------------------------------------------

def test_writer_never_overwrites(tmp_path, bundle):
    spec = example_items()[1]
    with pytest.raises(FileExistsError):
        write_bundle(bundle.parent, bundle_id=bundle.name, station=STATION, source="camera",
                     describe={"collection": "archive", "series": "x", "item": {}},
                     pages=[PageInput(raw=bundle / "raw/001.jpg", page=bundle / "pages/001.jpg", width=1, height=1)])


def test_writer_cleans_up_when_the_bundle_would_not_validate(tmp_path):
    raw = tmp_path / "IMG_1.jpg"
    draw_page(raw, ["x"])
    with pytest.raises(BundleError, match="would not validate"):
        write_bundle(tmp_path / "out", station=STATION, source="camera",
                     describe={"collection": "archive", "series": "x", "item": {"date": "spring 1962"}},
                     pages=[PageInput(raw=raw, page=raw, width=10, height=10)])
    assert list((tmp_path / "out").iterdir()) == []   # no .partial left behind


def test_writer_refuses_unknown_file_types(tmp_path):
    bad = tmp_path / "scan.bmp"
    bad.write_bytes(b"BM")
    with pytest.raises(BundleError, match="extension"):
        write_bundle(tmp_path / "out", station=STATION, source="camera",
                     describe={"collection": "archive", "series": "x", "item": {}},
                     pages=[PageInput(raw=bad, page=bad, width=1, height=1)])


def test_ulids_are_unique_and_sortable():
    ids = [ulid.new(now_ms=1_700_000_000_000 + i) for i in range(50)]
    assert ids == sorted(ids) and len(set(ids)) == 50 and all(ulid.is_ulid(i) for i in ids)


# --- refusals --------------------------------------------------------------------------------------------------

def test_missing_file(bundle):
    (bundle / "pages/002.jpg").unlink()
    rep = validate(bundle)
    assert not rep.ok and any("missing file: pages/002.jpg" in e for e in rep.errors)


def test_changed_byte(bundle):
    p = bundle / "raw/001.jpg"
    data = bytearray(p.read_bytes())
    data[len(data) // 2] ^= 0xFF
    p.write_bytes(bytes(data))
    assert any("sha256 doesn't match" in e for e in validate(bundle).errors)


def test_changed_size(bundle):
    with open(bundle / "pages/001.jpg", "ab") as f:
        f.write(b"\0")
    assert any("size" in e for e in validate(bundle).errors)


def test_bad_date(bundle):
    m = manifest(bundle)
    m["describe"]["item"]["date"] = "spring 1962"
    rewrite(bundle, m)
    assert any("item date" in e for e in validate(bundle).errors)


def test_pages_out_of_sequence(bundle):
    m = manifest(bundle)
    m["pages"][1]["n"] = 3
    rewrite(bundle, m)
    assert any("numbered 1..2" in e for e in validate(bundle).errors)


def test_page_file_with_the_wrong_number(bundle):
    m = manifest(bundle)
    m["pages"][0]["page"], m["pages"][1]["page"] = m["pages"][1]["page"], m["pages"][0]["page"]
    rewrite(bundle, m)
    assert any("wrong number" in e for e in validate(bundle).errors)


def test_extra_file(bundle):
    (bundle / "pages/003.jpg").write_bytes(b"stray")
    assert any("unexpected file: pages/003.jpg" in e for e in validate(bundle).errors)


def test_os_debris_is_ignored(bundle):
    (bundle / ".DS_Store").write_bytes(b"\0")
    (bundle / "raw/._001.jpg").write_bytes(b"\0")
    assert validate(bundle).ok


def test_partial_directory(bundle):
    partial = bundle.with_name(bundle.name + ".partial")
    bundle.rename(partial)
    rep = validate(partial)
    assert not rep.ok and "incomplete" in rep.errors[0]


def test_directory_name_must_match_id(bundle):
    renamed = bundle.with_name(ulid.new())
    bundle.rename(renamed)
    assert any("bundle id" in e for e in validate(renamed).errors)


def test_symlinks_are_refused(bundle, tmp_path):
    outside = tmp_path / "secret.txt"
    outside.write_text("not part of the bundle")
    target = bundle / "pages/002.jpg"
    target.unlink()
    os.symlink(outside, target)
    assert any("symbolic link" in e for e in validate(bundle).errors)


def test_unknown_manifest_keys_are_refused(bundle):
    m = manifest(bundle)
    m["visibility"] = "public"    # visibility is set in the library, never in a bundle
    rewrite(bundle, m)
    assert any("visibility" in e for e in validate(bundle).errors)


def test_unreferenced_checksum(bundle):
    m = manifest(bundle)
    del m["slip"]
    rewrite(bundle, m)
    assert any("slip.jpg is in files but nothing" in e for e in validate(bundle).errors)


@pytest.mark.parametrize("field, value", [
    ("created_at", "2026-12-08T14:03:11"),       # no UTC offset
    ("id", "not-a-ulid"),
    ("source", "fax"),
])
def test_bad_top_level_fields(bundle, field, value):
    m = manifest(bundle)
    m[field] = value
    rewrite(bundle, m)
    assert not validate(bundle).ok


def test_bad_json(bundle):
    (bundle / "manifest.json").write_text("{not json")
    assert "not valid JSON" in validate(bundle).errors[0]


def test_no_manifest(tmp_path):
    (tmp_path / "x").mkdir()
    assert validate(tmp_path / "x").errors == ["no manifest.json"]


def test_unknown_series_is_a_warning_not_an_error(bundle):
    rep = validate(bundle, series={"yearbooks"})
    assert rep.ok and any("unknown series 'newsletters'" in w for w in rep.warnings)


# --- separator contract ----------------------------------------------------------------------------------------

def test_separator_payload():
    assert is_separator_payload(SEPARATOR_PAYLOAD) and is_separator_payload(f" {SEPARATOR_PAYLOAD}\n")
    assert not is_separator_payload("ATHENA-ITEM-BREAK/2") and not is_separator_payload(None)
