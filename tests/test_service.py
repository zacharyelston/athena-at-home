# SPDX-License-Identifier: GPL-3.0-or-later
"""The ocr.page contract, without NATS or the real model."""
from conftest import FakeEngine, dense_tier0, sparse_tier0

from sieve import service


def test_ping():
    r = service.handle({"ping": True, "id": 7}, FakeEngine(), None)
    assert r["pong"] and r["model"] == "fake/deepseek-ocr-2" and r["id"] == 7


def test_auto_tiles_sparse_page(page_b64):
    eng = FakeEngine()
    r = service.handle({"image_b64": page_b64}, eng, sparse_tier0)
    assert "error" not in r
    assert r["tiles"] == 0 and r["tile_reason"] == "sparse page"
    assert eng.calls[0]["tiles"] == 0
    assert r["tier0"]["text"].startswith("Dear Jordan,")
    assert r["text"] == "# Page\nhello"


def test_auto_tiles_dense_page(page_b64):
    r = service.handle({"image_b64": page_b64}, FakeEngine(), dense_tier0)
    assert r["tiles"] == 6 and r["tile_reason"].startswith("table-like")


def test_no_vision_means_full_tiles_and_no_tier0(page_b64):
    r = service.handle({"image_b64": page_b64}, FakeEngine(), None)
    assert r["tiles"] == 6 and r["tile_reason"] == "no tier-0 signals" and r["tier0"] is None


def test_explicit_tiles_and_mode(page_b64):
    eng = FakeEngine()
    r = service.handle({"image_b64": page_b64, "tiles": 2, "mode": "free", "tier0": False}, eng, sparse_tier0)
    assert r["tiles"] == 2 and r["tile_reason"] == "requested" and r["tier0"] is None
    assert eng.calls[0]["instruction"] == "Free OCR."


def test_data_url_accepted(page_b64):
    r = service.handle({"image_b64": "data:image/jpeg;base64," + page_b64}, FakeEngine(), None)
    assert "error" not in r


def test_prompt_override_and_max_tokens(page_b64):
    eng = FakeEngine()
    service.handle({"image_b64": page_b64, "prompt": "Describe the figure.", "max_tokens": 500}, eng, None)
    assert eng.calls[0]["instruction"] == "Describe the figure." and eng.calls[0]["max_tokens"] == 500


def test_bad_requests(page_b64):
    eng = FakeEngine()
    cases = [
        ({}, "expected image_b64"),
        ({"image_b64": "not base64!!"}, "not valid base64"),
        ({"image_b64": "aGVsbG8="}, "not a readable image"),
        ({"image_b64": page_b64, "mode": "poetry"}, "mode must be"),
        ({"image_b64": page_b64, "tiles": 9}, "tiles must be"),
        ({"image_b64": page_b64, "max_tokens": 0}, "max_tokens"),
    ]
    for req, msg in cases:
        r = service.handle({**req, "id": "x"}, eng, None)
        assert msg in r["error"], (req, r)
        assert r["id"] == "x"
    assert eng.calls == []


def test_engine_failure_is_reported_not_raised(page_b64):
    r = service.handle({"image_b64": page_b64}, FakeEngine(fail=RuntimeError("GPU timeout")), None)
    assert r["error"] == "RuntimeError: GPU timeout"
