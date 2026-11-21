# SPDX-License-Identifier: GPL-3.0-or-later
# Derived from the author's HaberDash deploy/nats/worker/gpu_worker.py (request/reply contract).
"""The ocr.page request/reply contract, as a plain function (no NATS), so it can be tested directly.

Request (JSON):
  {"ping": true}                                   -> {"pong": true, "worker": …, "model": …}
  {"image_b64": "<base64 jpg/png, or data: URL>",
   "mode": "markdown" | "free",      default markdown
   "prompt": "<instruction>",        optional override of the mode's instruction
   "max_tokens": <int>,              default 3000
   "tiles": "auto" | 0..6,           default auto
   "tier0": true | false,            include Apple Vision text in the reply (default true)
   "id": "<anything>"}               echoed back
Reply:
  {"text", "model", "worker", "tiles", "tile_reason", "seconds", "gen_tokens", "image_tokens",
   "looped", "hit_cap", "tier0": {"text", "chars"} | null, "id"}
  or {"error": "<message>", "worker", "id"}

Differences from gpu_worker.py: the model runs in process (MLX) instead of behind vLLM's HTTP API; per-page tile
budget; tier-0 text and run details in the reply; no raw "messages" passthrough (it was vLLM-specific).
"""
from __future__ import annotations

import base64
import binascii
import io
import socket
from typing import Callable

from . import guard, tiles
from .engine import PROMPTS

WORKER = socket.gethostname()
MAX_IMAGE_BYTES = 20 * 1024 * 1024


class BadRequest(ValueError):
    pass


def decode_image(b64: str):
    from PIL import Image

    if not isinstance(b64, str) or not b64:
        raise BadRequest("image_b64 must be a non-empty base64 string")
    if b64.startswith("data:"):
        b64 = b64.split(",", 1)[-1]
    try:
        raw = base64.b64decode(b64, validate=True)
    except (binascii.Error, ValueError) as e:
        raise BadRequest(f"image_b64 is not valid base64: {e}") from e
    if len(raw) > MAX_IMAGE_BYTES:
        raise BadRequest(f"image is {len(raw)} bytes; limit is {MAX_IMAGE_BYTES}")
    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except Exception as e:
        raise BadRequest(f"not a readable image: {e}") from e
    return img


def handle(req: dict, engine, tier0_fn: Callable | None) -> dict:
    """Serve one ocr.page request. ``engine`` has ``.ocr()`` and ``.model_id``; ``tier0_fn(image)`` returns
    tier-0 lines (``sieve.tier0.recognize``), or is None where Apple Vision isn't available."""
    from . import tier0 as t0

    rid = req.get("id")
    base = {"worker": WORKER, "id": rid} if rid is not None else {"worker": WORKER}
    if req.get("ping"):
        return {"pong": True, "model": engine.model_id, **base}
    try:
        if "image_b64" not in req:
            raise BadRequest("expected image_b64 or ping")
        mode = req.get("mode", "markdown")
        if mode not in PROMPTS:
            raise BadRequest(f"mode must be one of {sorted(PROMPTS)}")
        instruction = req.get("prompt") or PROMPTS[mode]
        max_tokens = int(req.get("max_tokens", guard.DEFAULT_MAX_TOKENS))
        if not 1 <= max_tokens <= 16384:
            raise BadRequest("max_tokens must be 1..16384")
        try:
            wanted = tiles.parse(req.get("tiles"))
        except ValueError as e:
            raise BadRequest(str(e)) from e
        image = decode_image(req["image_b64"])

        lines = None
        if tier0_fn is not None and (wanted == "auto" or req.get("tier0", True)):
            lines = tier0_fn(image)
        if wanted == "auto":
            n_tiles, reason = tiles.choose(t0.signals(lines) if lines is not None else None)
        else:
            n_tiles, reason = wanted, "requested"

        r = engine.ocr(image, tiles=n_tiles, instruction=instruction, max_tokens=max_tokens)
        tier0_out = None
        if lines is not None and req.get("tier0", True):
            text0 = t0.text_of(lines)
            tier0_out = {"text": text0, "chars": len(text0)}
        return {"text": r.text, "model": engine.model_id, "tiles": r.tiles, "tile_reason": reason,
                "seconds": r.seconds, "gen_tokens": r.gen_tokens, "image_tokens": r.image_tokens,
                "looped": r.looped, "hit_cap": r.hit_cap, "tier0": tier0_out, **base}
    except BadRequest as e:
        return {"error": str(e), **base}
    except Exception as e:  # model or Vision failure: report it, keep serving
        return {"error": f"{type(e).__name__}: {e}", **base}
