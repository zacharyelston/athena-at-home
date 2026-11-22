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


# --- historical styles (the capture pilot's synthetic baseline) --------------------------------------------------

@pytest.mark.parametrize("name", sorted(gen.HISTORICAL))
def test_historical_pages_have_ground_truth_and_look_their_part(name):
    import numpy as np

    layout, rgb, material = gen.render_historical(name)
    squeezed = "".join("".join(layout.lines).split())
    assert layout.lines and len(layout.fields) >= 4
    missing = [f for f in layout.fields if "".join(f.split()) not in squeezed]
    assert not missing, f"{name}: key items not in the ground truth: {missing}"
    assert rgb.shape == (gen.H, gen.W, 3) and rgb.dtype == np.uint8
    paper = np.median(rgb.reshape(-1, 3), axis=0)
    ink = rgb.reshape(-1, 3)[rgb.sum(axis=2).ravel() < 0.55 * paper.sum()]
    assert 180 < paper.mean() < 250 and len(ink) > 2000            # mostly paper, with something written on it
    if material == "mimeograph":
        assert ink[:, 2].mean() > ink[:, 1].mean() + 25             # purple ink
    if material == "newsprint":
        assert paper[0] - paper[2] > 40                             # yellowed
    again = gen.render_historical(name)[1]
    assert (again == rgb).all()                                    # the same page every time


def test_main_writes_the_historical_pages_too(tmp_path, monkeypatch):
    monkeypatch.setattr(gen, "DPIS", [100])
    monkeypatch.setattr(gen, "DOCS", {})
    monkeypatch.setattr(gen, "HISTORICAL", {"typed_minutes": gen.HISTORICAL["typed_minutes"]})
    gen.main(out=str(tmp_path))
    d = tmp_path / "typed_minutes"
    assert (d / "material.txt").read_text().strip() == "typewritten"
    assert (d / "clean_100.png").exists() and (d / "camera_100.jpg").exists() and (d / "gt.txt").read_text().strip()
