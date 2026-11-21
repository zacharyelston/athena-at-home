# SPDX-License-Identifier: GPL-3.0-or-later
"""The transactional outbox: events written with each change are published to ``library.events`` on NATS.

At least once: a row is marked published only after JetStream acknowledges it, and each message carries the
outbox id as ``Nats-Msg-Id``, so a republish after a crash is dropped by JetStream's duplicate window.
Payloads carry ids only, never content.
"""
from __future__ import annotations

import json

from .core import Library


def stream_name(library: str) -> str:
    return f"LIBRARY_{library.upper().replace('-', '_')}_EVENTS"


async def ensure_stream(js, library: str) -> None:
    from nats.js.api import StreamConfig
    from nats.js.errors import NotFoundError

    name = stream_name(library)
    try:
        await js.stream_info(name)
    except NotFoundError:
        await js.add_stream(StreamConfig(name=name, subjects=[f"library.{library}.>"], duplicate_window=600.0))


async def publish_pending(lib: Library, js, batch: int = 100) -> int:
    """Publish unsent outbox rows in order. Returns how many were published."""
    sent = 0
    with lib.connect() as conn:
        while True:
            with conn.transaction():
                rows = conn.execute(
                    """SELECT id, subject, payload FROM outbox WHERE published_at IS NULL
                       ORDER BY id LIMIT %s FOR UPDATE SKIP LOCKED""", (batch,)).fetchall()
                for r in rows:
                    await js.publish(r["subject"], json.dumps(r["payload"]).encode(),
                                     headers={"Nats-Msg-Id": f"{lib.name}-{r['id']}"})
                    conn.execute("UPDATE outbox SET published_at = now() WHERE id = %s", (r["id"],))
                    sent += 1
            if len(rows) < batch:
                return sent


async def connect_nats(lib: Library):
    import nats

    if not lib.cfg.nats_url:
        raise RuntimeError("no NATS URL configured (ATHENA_NATS_URL); events stay in the outbox")
    opts: dict = {"name": f"athena-library/{lib.name}"}
    if lib.cfg.nats_creds:
        opts["user_credentials"] = lib.cfg.nats_creds
    if lib.cfg.nats_tls_hostname:
        opts["tls_hostname"] = lib.cfg.nats_tls_hostname
    return await nats.connect(lib.cfg.nats_url, **opts)
