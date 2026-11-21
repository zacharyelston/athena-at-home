# SPDX-License-Identifier: GPL-3.0-or-later
"""The synthetic test set renders on this platform (macOS fonts or the Linux fallbacks) with consistent ground truth."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "phase0"))
gen = pytest.importorskip("gen_testset", reason="needs opencv-python-headless and numpy (dev extra)")


@pytest.mark.parametrize("name", sorted(gen.DOCS))
def test_every_key_field_is_in_the_ground_truth(name):
    page = gen.DOCS[name]()
    text = "\n".join(page.lines)
    assert page.lines and page.fields
    missing = [f for f in page.fields if f not in text]
    assert not missing, f"{name}: fields not in ground truth: {missing}"
    assert page.img.size == (gen.W, gen.H)


def test_main_writes_to_a_chosen_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(gen, "DPIS", [100])
    monkeypatch.setattr(gen, "DOCS", {"receipt": gen.DOCS["receipt"]})
    gen.main(out=str(tmp_path))
    d = tmp_path / "receipt"
    assert (d / "clean_100.png").exists() and (d / "camera_100.jpg").exists()
    assert json.loads((d / "fields.json").read_text())


def test_missing_font_says_how_to_fix_it(monkeypatch):
    monkeypatch.setattr(gen, "FONTS", {("sans", False): [("/nonexistent/font.ttf", 0)]})
    with pytest.raises(FileNotFoundError, match="fonts-liberation"):
        gen.font_path("sans")
