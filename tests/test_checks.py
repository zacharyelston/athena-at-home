# SPDX-License-Identifier: GPL-3.0-or-later
"""The repository guards: SPDX headers, and no real records, captures, databases or secrets."""
import importlib.util
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


spdx = load("check_spdx")
records = load("check_no_records")
HEADER = "# SPDX-License-Identifier: GPL-3.0-or-later\n"


def write(root: Path, rel: str, data: bytes | str = b"x") -> str:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data.encode() if isinstance(data, str) else data)
    return rel


def jpeg(root: Path, rel: str, tags: dict | None = None, gps: bool = False) -> str:
    """A small JPEG, optionally with camera EXIF tags and a GPS latitude, as a phone would write."""
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    exif = Image.Exif()
    for k, v in (tags or {}).items():
        exif[k] = v
    if gps:
        exif.get_ifd(records.EXIF_GPS_IFD)[2] = (41.0, 16.0, 0.0)  # GPSLatitude
    Image.new("RGB", (64, 64), "white").save(p, "JPEG", exif=exif)
    assert bool(Image.open(p).getexif().get_ifd(records.EXIF_GPS_IFD)) == gps
    return rel


# --- SPDX -----------------------------------------------------------------------------------------------------

def test_spdx_passes_a_file_with_the_header(tmp_path):
    rel = write(tmp_path, "pkg/mod.py", HEADER + "x = 1\n")
    assert spdx.check(tmp_path, [rel]) == []


def test_spdx_allows_a_shebang_first(tmp_path):
    rel = write(tmp_path, "run.sh", "#!/bin/sh\n" + HEADER)
    assert spdx.check(tmp_path, [rel]) == []


@pytest.mark.parametrize("rel", ["pkg/mod.py", "deploy/x.sh", ".github/workflows/ci.yml"])
def test_spdx_flags_a_missing_header(tmp_path, rel):
    write(tmp_path, rel, "print('hi')\n")
    [problem] = spdx.check(tmp_path, [rel])
    assert "missing" in problem


def test_spdx_ignores_other_files(tmp_path):
    rel = write(tmp_path, "README.md", "# no header needed\n")
    assert spdx.check(tmp_path, [rel]) == []


def test_spdx_requires_derived_from_to_name_a_source(tmp_path):
    good = write(tmp_path, "a.py", HEADER + "# Derived from the author's myvect store.py.\n")
    bad = write(tmp_path, "b.py", HEADER + "# Derived from:\n")
    assert spdx.check(tmp_path, [good]) == []
    [problem] = spdx.check(tmp_path, [bad])
    assert "names no source" in problem


def test_spdx_main_exit_code(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    write(tmp_path, "a.py", "x = 1\n")
    assert spdx.main(["a.py"]) == 1
    write(tmp_path, "b.py", HEADER)
    assert spdx.main(["b.py"]) == 0


# --- no real records ------------------------------------------------------------------------------------------

def test_clean_repo_files_pass(tmp_path):
    rels = [write(tmp_path, "docs/x.md", "text"), jpeg(tmp_path, "tests/fixtures/page.jpg")]
    assert records.check(tmp_path, rels) == []


def test_image_outside_fixture_paths_fails(tmp_path):
    rel = jpeg(tmp_path, "docs/page.jpg")
    [problem] = records.check(tmp_path, [rel])
    assert "outside" in problem


def test_gps_exif_fails_even_in_fixtures(tmp_path):
    rel = jpeg(tmp_path, "tests/fixtures/page.jpg", gps=True)
    problems = records.check(tmp_path, [rel])
    assert any("GPS" in p for p in problems), problems


def test_camera_make_and_model_fail(tmp_path):
    rel = jpeg(tmp_path, "tests/fixtures/page.jpg", {records.EXIF_MAKE: "Apple", records.EXIF_MODEL: "iPhone"})
    problems = records.check(tmp_path, [rel])
    assert any("make/model" in p for p in problems), problems


def test_unreadable_camera_format_fails(tmp_path):
    rel = write(tmp_path, "tests/fixtures/IMG_0001.HEIC", b"\x00\x00\x00\x18ftypheic")
    assert any("can't inspect" in p for p in records.check(tmp_path, [rel]))


def test_large_file_fails(tmp_path):
    rel = write(tmp_path, "data.bin", b"\0" * (3 * 1024 * 1024))
    [problem] = records.check(tmp_path, [rel])
    assert "over the 2 MB limit" in problem


@pytest.mark.parametrize("rel", ["library.sqlite", "x/garden.db", "scan.pdf"])
def test_databases_and_pdfs_fail(tmp_path, rel):
    write(tmp_path, rel)
    assert records.check(tmp_path, [rel])


@pytest.mark.parametrize("rel", ["captures/notes.txt", "srv/originals/ab/abc.txt", "x/scans/list.md"])
def test_data_directories_fail(tmp_path, rel):
    write(tmp_path, rel)
    assert any("data directory" in p for p in records.check(tmp_path, [rel]))


def test_code_named_like_data_is_fine(tmp_path):
    rels = [write(tmp_path, "athena/library/originals.py", HEADER), write(tmp_path, "athena/library/x.py", HEADER)]
    assert records.check(tmp_path, rels) == []


@pytest.mark.parametrize("rel", ["sieve.creds", "deploy/nats/ca.pem", "op.nk", ".env"])
def test_secrets_fail(tmp_path, rel):
    write(tmp_path, rel)
    assert any("secret" in p for p in records.check(tmp_path, [rel]))


def test_example_secrets_files_are_fine(tmp_path):
    rel = write(tmp_path, "deploy/nats/.env.example", "OPERATOR=example\n")
    assert records.check(tmp_path, [rel]) == []
