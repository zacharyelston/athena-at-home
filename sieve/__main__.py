# SPDX-License-Identifier: GPL-3.0-or-later
"""Sieve command line.

  sieve serve                      run the ocr.page worker (M1 Mac mini)
  sieve ocr PAGE.jpg [--tiles N]   send a page to a running worker over NATS (any platform)
  sieve local PAGE.jpg             OCR a page in process, no NATS (Apple Silicon)
  sieve ping                       check a worker is answering
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys

from .worker import DEFAULTS


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="sieve")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def nats_args(p):
        p.add_argument("--nats-url", default=DEFAULTS["nats_url"])
        p.add_argument("--creds", default=DEFAULTS["creds"])
        p.add_argument("--tls-hostname", default=DEFAULTS["tls_hostname"])
        p.add_argument("--subject", default=DEFAULTS["subject"])

    s = sub.add_parser("serve", help="run the worker")
    nats_args(s)
    s.add_argument("--queue", default=DEFAULTS["queue"])
    s.add_argument("--model", default=DEFAULTS["model"])

    for name in ("ocr", "local"):
        p = sub.add_parser(name)
        p.add_argument("image")
        p.add_argument("--tiles", default="auto", help="auto or 0..6")
        p.add_argument("--mode", default="markdown", choices=["markdown", "free"])
        p.add_argument("--json", action="store_true", help="print the whole reply")
        if name == "ocr":
            nats_args(p)
        else:
            p.add_argument("--model", default=DEFAULTS["model"])

    p = sub.add_parser("ping")
    nats_args(p)

    a = ap.parse_args(argv)
    if a.cmd == "serve":
        from .worker import serve
        asyncio.run(serve(a.nats_url, a.subject, a.queue, a.model, a.creds, a.tls_hostname))
        return 0

    if a.cmd == "ping":
        from .client import SieveClient
        print(json.dumps(SieveClient(a.nats_url, a.subject, a.creds, a.tls_hostname, timeout_s=10).ping()))
        return 0

    with open(a.image, "rb") as f:
        data = f.read()
    if a.cmd == "ocr":
        from .client import SieveClient
        reply = SieveClient(a.nats_url, a.subject, a.creds, a.tls_hostname).ocr(data, tiles=a.tiles, mode=a.mode)
    else:
        import base64

        from . import service
        from .engine import Engine
        from .worker import _tier0_fn
        reply = service.handle({"image_b64": base64.b64encode(data).decode(), "mode": a.mode, "tiles": a.tiles},
                               Engine(a.model), _tier0_fn())
        if "error" in reply:
            print(reply["error"], file=sys.stderr)
            return 1
    if a.json:
        print(json.dumps(reply, indent=1))
    else:
        print(reply["text"])
        print(f"\n[{reply['tiles']} tiles: {reply['tile_reason']}; {reply['seconds']}s; "
              f"{reply['gen_tokens']} tokens]", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
