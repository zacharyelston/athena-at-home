# SPDX-License-Identifier: GPL-3.0-or-later
# Derived from the author's myvect src/myvect/ocr.py (OcrClient, NATS backend).
"""Client for Sieve's ``ocr.page`` service. Works on any platform with nats-py (Linux capture stations included).

Differences from OcrClient: NATS only (no OpenAI-compatible HTTP backend, no vLLM ``extra_body``), a ``tiles``
parameter, and the whole reply (text, tier-0 text, tile choice, timings) returned instead of just the text.
"""
from __future__ import annotations

import asyncio
import base64
import json


class SieveError(RuntimeError):
    pass


class SieveClient:
    def __init__(self, nats_url: str = "nats://127.0.0.1:4222", subject: str = "ocr.page",
                 creds: str | None = None, tls_hostname: str | None = None,
                 timeout_s: float = 180.0, retries: int = 1):
        self.nats_url, self.subject = nats_url, subject
        self.creds, self.tls_hostname = creds, tls_hostname
        self.timeout_s, self.retries = timeout_s, retries

    async def _connect(self):
        import nats

        opts: dict = {"name": "sieve-client"}
        if self.creds:
            opts["user_credentials"] = self.creds
        if self.tls_hostname:
            opts["tls_hostname"] = self.tls_hostname
        return await nats.connect(self.nats_url, **opts)

    async def arequest(self, req: dict) -> dict:
        last: Exception | None = None
        for _ in range(self.retries + 1):
            nc = None
            try:
                nc = await self._connect()
                payload = json.dumps(req).encode()
                if len(payload) > nc.max_payload:
                    raise SieveError(f"request is {len(payload)} bytes but the server's max payload is "
                                     f"{nc.max_payload}; send JPEG or raise max_payload on the server")
                resp = await nc.request(self.subject, payload, timeout=self.timeout_s)
                data = json.loads(resp.data)
                if "error" in data:
                    raise SieveError(data["error"])  # the worker answered: don't retry
                return data
            except SieveError:
                raise
            except Exception as e:  # timeout, no responders, connection: retry
                last = e
            finally:
                if nc is not None:
                    await nc.drain()
        raise SieveError(f"{self.subject} failed after {self.retries + 1} tries: {last}")

    async def aocr(self, image: bytes, tiles: int | str = "auto", mode: str = "markdown",
                   prompt: str | None = None, max_tokens: int | None = None, tier0: bool = True) -> dict:
        req: dict = {"image_b64": base64.b64encode(image).decode(), "mode": mode, "tiles": tiles, "tier0": tier0}
        if prompt:
            req["prompt"] = prompt
        if max_tokens:
            req["max_tokens"] = max_tokens
        return await self.arequest(req)

    async def aping(self) -> dict:
        return await self.arequest({"ping": True})

    def ocr(self, image: bytes, **kw) -> dict:
        """Sync wrapper: one connection per call (negligible next to OCR time)."""
        return asyncio.run(self.aocr(image, **kw))

    def ping(self) -> dict:
        return asyncio.run(self.aping())
