# SPDX-License-Identifier: GPL-3.0-or-later
"""Worker and client over a real nats-server, with a fake model."""
import asyncio
import base64

import pytest
from conftest import FakeEngine

from sieve.client import SieveClient, SieveError
from sieve.worker import serve


async def start_worker(url, engine, subject="ocr.page"):
    ready = asyncio.Event()
    task = asyncio.create_task(serve(url, subject, "sieve", engine.model_id, engine=engine, ready=ready))
    await asyncio.wait_for(ready.wait(), 10)
    return task


async def test_ping_and_ocr(nats_server, page_b64):
    url, _ = nats_server
    eng = FakeEngine(text="| a | b |")
    task = await start_worker(url, eng)
    try:
        c = SieveClient(url, timeout_s=10)
        assert (await c.aping())["pong"]
        r = await c.aocr(base64.b64decode(page_b64), tiles=2)
        assert r["text"] == "| a | b |" and r["tiles"] == 2
    finally:
        task.cancel()


async def test_worker_error_is_raised_once_not_retried(nats_server, page_b64):
    url, _ = nats_server
    eng = FakeEngine(fail=RuntimeError("boom"))
    task = await start_worker(url, eng)
    try:
        with pytest.raises(SieveError, match="boom"):
            await SieveClient(url, timeout_s=10, retries=3).aocr(base64.b64decode(page_b64))
        assert len(eng.calls) == 1
    finally:
        task.cancel()


async def test_no_worker_fails_after_retries(nats_server):
    url, _ = nats_server
    with pytest.raises(SieveError, match="failed after 2 tries"):
        await SieveClient(url, timeout_s=0.5, retries=1).aping()


async def test_oversized_page_is_refused_before_sending(nats_server):
    url, max_payload = nats_server
    task = await start_worker(url, FakeEngine())
    try:
        with pytest.raises(SieveError, match="max payload"):
            await SieveClient(url, timeout_s=10).aocr(b"\xff" * max_payload)
    finally:
        task.cancel()


async def test_requests_are_served_one_at_a_time(nats_server, page_b64):
    url, _ = nats_server
    eng = FakeEngine()
    task = await start_worker(url, eng)
    try:
        c = SieveClient(url, timeout_s=10)
        img = base64.b64decode(page_b64)
        rs = await asyncio.gather(*(c.aocr(img, tiles=t) for t in (0, 2, 6)))
        assert sorted(r["tiles"] for r in rs) == [0, 2, 6] and len(eng.calls) == 3
    finally:
        task.cancel()
