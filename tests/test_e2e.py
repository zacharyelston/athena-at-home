# SPDX-License-Identifier: GPL-3.0-or-later
"""End to end: real DeepSeek-OCR-2 (8-bit, MLX) behind the NATS worker, Phase 0 pages, tiles on auto.

Slow and needs the model, Apple Silicon and the Phase 0 test set:
    phase0/.venv/bin/python phase0/gen_testset.py          # once, renders phase0/testset/
    SIEVE_E2E=1 .venv/bin/python -m pytest -m e2e -s
"""
import asyncio
import json
import os
import sys
from pathlib import Path

import pytest
from conftest import nats_server_running

ROOT = Path(__file__).resolve().parents[1]
TESTSET = ROOT / "phase0" / "testset"
DOCS = ["bank_statement", "utility_bill", "insurance_letter", "receipt", "intake_form", "w2_form"]

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(os.environ.get("SIEVE_E2E") != "1", reason="set SIEVE_E2E=1 to run (slow, needs the model)"),
    pytest.mark.skipif(not TESTSET.exists(), reason="render phase0/testset first (phase0/gen_testset.py)"),
]


def fields_missing(text, doc):
    sys.path.insert(0, str(ROOT / "phase0"))
    from bench import compact, normalize  # Phase 0 scoring, so results are comparable

    fields = json.loads((TESTSET / doc / "fields.json").read_text())
    got = compact(normalize(text))
    return [f for f in fields if compact(f) not in got]


async def test_phase0_pages_over_nats_with_auto_tiles():
    from sieve.client import SieveClient
    from sieve.engine import Engine
    from sieve.worker import serve

    engine = Engine()
    with nats_server_running(8 * 1024 * 1024) as url:
        ready = asyncio.Event()
        task = asyncio.create_task(serve(url, "ocr.page", "sieve", engine.model_id, engine=engine, ready=ready))
        await asyncio.wait_for(ready.wait(), 30)
        try:
            client = SieveClient(url, timeout_s=300)
            print()
            failures = {}
            for doc in DOCS:
                img = (TESTSET / doc / "camera_150.jpg").read_bytes()
                r = await client.aocr(img, tiles="auto")
                missing = fields_missing(r["text"], doc)
                print(f"{doc:17s} {r['tiles']} tiles ({r['tile_reason']:28s}) {r['seconds']:5.1f}s "
                      f"{r['gen_tokens']:4d} tok  tier0 {r['tier0']['chars']:4d} chars  missing {missing}")
                assert not r["looped"] and not r["hit_cap"]
                if missing:
                    failures[doc] = missing
            assert not failures, failures
        finally:
            task.cancel()
