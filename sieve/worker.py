# SPDX-License-Identifier: GPL-3.0-or-later
# Derived from the author's HaberDash deploy/nats/worker/gpu_worker.py (NATS queue-group service).
"""Sieve's NATS worker: serves ``ocr.page`` in a queue group, so several workers share the load.

The model is loaded once and runs in a background thread, one page at a time, so the NATS connection keeps
answering pings and heartbeats during a long page.

Env (all optional): SIEVE_NATS_URL (nats://127.0.0.1:4222), SIEVE_NATS_CREDS (.creds file),
SIEVE_NATS_TLS_HOSTNAME, SIEVE_SUBJECT (ocr.page), SIEVE_QUEUE (sieve), SIEVE_MODEL.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import signal
import sys

from . import service
from .engine import DEFAULT_MODEL, Engine

DEFAULTS = {
    "nats_url": os.environ.get("SIEVE_NATS_URL", "nats://127.0.0.1:4222"),
    "creds": os.environ.get("SIEVE_NATS_CREDS"),
    "tls_hostname": os.environ.get("SIEVE_NATS_TLS_HOSTNAME"),
    "subject": os.environ.get("SIEVE_SUBJECT", "ocr.page"),
    "queue": os.environ.get("SIEVE_QUEUE", "sieve"),
    "model": os.environ.get("SIEVE_MODEL", DEFAULT_MODEL),
}


def _tier0_fn():
    """Apple Vision if this machine has it: macOS with pyobjc's Vision bindings installed."""
    if sys.platform != "darwin" or importlib.util.find_spec("Vision") is None:
        return None
    from . import tier0
    return tier0.recognize


async def serve(nats_url: str, subject: str, queue: str, model: str, creds: str | None = None,
                tls_hostname: str | None = None, engine=None, ready: asyncio.Event | None = None) -> None:
    import nats

    engine = engine or Engine(model)
    tier0_fn = _tier0_fn()
    log(f"model {engine.model_id} loaded in {getattr(engine, 'load_seconds', '?')}s; "
        f"tier 0 {'Apple Vision' if tier0_fn else 'unavailable'}")

    opts = {"name": f"sieve/{service.WORKER}", "max_reconnect_attempts": -1}
    if creds:
        opts["user_credentials"] = creds
    if tls_hostname:
        opts["tls_hostname"] = tls_hostname
    nc = await nats.connect(nats_url, **opts)
    log(f"connected {nats_url}; serving {subject} (queue {queue}); max payload {nc.max_payload} bytes")

    gate = asyncio.Semaphore(1)
    loop = asyncio.get_running_loop()

    async def on_msg(msg) -> None:
        try:
            req = json.loads(msg.data or b"{}")
            if not isinstance(req, dict):
                raise ValueError("request must be a JSON object")
        except Exception as e:
            await msg.respond(json.dumps({"error": f"bad request json: {e}", "worker": service.WORKER}).encode())
            return
        if req.get("ping"):
            await msg.respond(json.dumps(service.handle(req, engine, None)).encode())
            return
        async with gate:
            reply = await loop.run_in_executor(None, service.handle, req, engine, tier0_fn)
        if "error" not in reply:
            log(f"page: {reply['tiles']} tiles ({reply['tile_reason']}), {reply['seconds']}s, "
                f"{reply['gen_tokens']} tokens" + (" LOOP" if reply["looped"] else "")
                + (" CAP" if reply["hit_cap"] else ""))
        else:
            log(f"error: {reply['error']}")
        await msg.respond(json.dumps(reply).encode())

    await nc.subscribe(subject, queue=queue, cb=on_msg)
    stop = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError):
            pass
    if ready is not None:
        ready.set()
    await stop.wait()
    log("draining")
    await nc.drain()


def log(msg: str) -> None:
    print(f"sieve {service.WORKER}: {msg}", flush=True)
