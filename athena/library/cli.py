# SPDX-License-Identifier: GPL-3.0-or-later
"""``athena library …`` commands. Configuration: --config or ATHENA_CONFIG, plus ATHENA_* environment variables."""
from __future__ import annotations

import asyncio
import sys
import time


def add_parser(sub) -> None:
    lib = sub.add_parser("library", help="a library instance").add_subparsers(dest="action", required=True)

    def cmd(name, func, help):
        p = lib.add_parser(name, help=help)
        p.add_argument("--config", help="library.toml (default: $ATHENA_CONFIG or ./library.toml)")
        p.set_defaults(func=func)
        return p

    cmd("setup", _setup, "create directories, apply migrations, seed collections and series")
    p = cmd("intake", _intake, "ingest the bundles waiting in the intake directory")
    p.add_argument("--watch", type=float, metavar="SECONDS", help="keep polling every SECONDS")
    p = cmd("ingest", _ingest, "ingest one bundle directory (left in place)")
    p.add_argument("bundle")
    p = cmd("ocr", _ocr, "OCR pages that have no OCR text yet, through Sieve's ocr.page")
    p.add_argument("--limit", type=int)
    p.add_argument("--tiles", default="auto")
    p = cmd("search", _search, "full-text search")
    p.add_argument("query")
    p.add_argument("--limit", type=int, default=20)
    p = cmd("publish-events", _publish, "publish the outbox to library.events on NATS")
    p.add_argument("--watch", type=float, metavar="SECONDS", help="keep publishing every SECONDS")
    cmd("status", _status, "check the database is reachable and show counts (exit 1 if not)")
    p = cmd("explain", _explain, "an item's effective visibility, why, and the feeds it goes to")
    p.add_argument("item")
    p.add_argument("--today", help="decide as of this date (YYYY-MM-DD), e.g. to see a rights cutoff move")
    p = cmd("set-visibility", _set_visibility, "set a collection, series, item or page to a tier (or inherit)")
    p.add_argument("kind", choices=["collection", "series", "item", "page"])
    p.add_argument("id")
    p.add_argument("tier", choices=["private", "members", "public", "inherit"])
    p.add_argument("--as", dest="who", required=True, help="your login (recorded with the change)")
    p.add_argument("--why", default="", help="the reason (required when widening)")
    p = cmd("reviews", _reviews, "widened settings waiting for a second look")
    p.add_argument("--confirm", type=int, metavar="ID")
    p.add_argument("--as", dest="who")
    p = cmd("check-policies", _check_policies,
            "preview what a changed library.toml's policies would do, before applying it")
    p.add_argument("new_config")
    p.add_argument("--today")


def _lib(args):
    from .config import load
    from .core import Library

    return Library(load(args.config))


def _setup(args) -> int:
    lib = _lib(args)
    applied = lib.setup()
    print(f"library {lib.name}: " + (f"applied {', '.join(applied)}" if applied else "schema up to date"))
    return 0


def _intake(args) -> int:
    from .intake import scan

    lib = _lib(args)
    bad = 0
    while True:
        for r in scan(lib):
            bad += r.status == "rejected"
            print(f"{r.status}: {r.bundle.name}" + (f" ({r.reason.splitlines()[0]})" if r.reason else ""))
        if not args.watch:
            return 1 if bad else 0
        time.sleep(args.watch)


def _ingest(args) -> int:
    from .intake import ingest

    r = ingest(_lib(args), args.bundle)
    print(f"{r.status}: {r.bundle}" + (f"\n{r.reason}" if r.reason else ""))
    return 1 if r.status == "rejected" else 0


def _ocr(args) -> int:
    from sieve.client import SieveClient

    from .ocr import run_via_page

    lib = _lib(args)
    client = SieveClient(lib.cfg.nats_url or "nats://127.0.0.1:4222", subject=lib.cfg.sieve_subject,
                         creds=lib.cfg.nats_creds, tls_hostname=lib.cfg.nats_tls_hostname)
    run = run_via_page(lib, client, limit=args.limit,
                       tiles=int(args.tiles) if str(args.tiles).isdigit() else args.tiles)
    print(f"OCR: {run.done} pages done, {run.failed} failed; {len(run.items_ocr_done)} items now ocr_done")
    return 1 if run.failed else 0


def _search(args) -> int:
    from .search import search

    lib = _lib(args)
    with lib.connect() as conn:
        for h in search(conn, args.query, args.limit):
            pages = f" pages {','.join(map(str, h.pages))}" if h.pages else ""
            print(f"{h.rank:.3f}  {h.item}  {h.date or 'undated':>12}  {h.title}{pages}")
    return 0


def _publish(args) -> int:
    from .outbox import connect_nats, ensure_stream, publish_pending

    lib = _lib(args)

    async def go():
        nc = await connect_nats(lib)
        try:
            js = nc.jetstream()
            await ensure_stream(js, lib.name)
            while True:
                n = await publish_pending(lib, js)
                if n:
                    print(f"published {n} events")
                if not args.watch:
                    return
                await asyncio.sleep(args.watch)
        finally:
            await nc.drain()

    asyncio.run(go())
    return 0


def _status(args) -> int:
    lib = _lib(args)
    try:
        with lib.connect() as conn:
            counts = conn.execute(
                """SELECT (SELECT count(*) FROM item) AS items, (SELECT count(*) FROM page) AS pages,
                          (SELECT count(*) FROM item WHERE status = 'reviewed') AS reviewed,
                          (SELECT count(*) FROM outbox WHERE published_at IS NULL) AS unpublished,
                          (SELECT count(*) FROM ocr_failure WHERE resolved_at IS NULL) AS ocr_failures"""
            ).fetchone()
    except Exception as e:  # noqa: BLE001 - a health check reports any failure
        print(f"library {lib.name}: database unreachable: {type(e).__name__}", file=sys.stderr)
        return 1
    print(f"library {lib.name}: " + ", ".join(f"{k} {v}" for k, v in counts.items()))
    return 0


def _today(args):
    from datetime import date

    return date.fromisoformat(args.today) if getattr(args, "today", None) else date.today()


def _explain(args) -> int:
    from athena.visibility.library import decide_item

    lib = _lib(args)
    with lib.connect() as conn:
        d = decide_item(conn, lib.cfg, args.item, _today(args))
    print(f"item {args.item}: {d.item.tier}")
    print("  " + d.item.explain())
    if d.item.cutoff_year:
        print(f"  public-domain cutoff used: published in or before {d.item.cutoff_year}")
    print(f"  feeds: {', '.join(d.feeds) or 'none'}")
    for page_id, pd in d.pages.items():
        if pd.tier != d.item.tier:
            print(f"  page {page_id}: {pd.tier}")
    return 0


def _set_visibility(args) -> int:
    from athena.visibility.library import set_visibility

    lib = _lib(args)
    tier = None if args.tier == "inherit" else args.tier
    try:
        with lib.connect() as conn:
            r = set_visibility(conn, lib.name, args.kind, args.id, tier, args.who, args.why)
    except (ValueError, KeyError) as e:
        print(f"refused: {e}", file=sys.stderr)
        return 1
    note = f"; widened, waiting for review #{r['review']}" if r["widened"] else ""
    print(f"{args.kind} {args.id}: {r['before']} -> {r['after']}{note}")
    return 0


def _reviews(args) -> int:
    from athena.visibility.library import confirm_review, open_reviews

    lib = _lib(args)
    with lib.connect() as conn:
        if args.confirm is not None:
            if not args.who:
                print("say who is confirming: --as LOGIN", file=sys.stderr)
                return 2
            confirm_review(conn, args.confirm, args.who)
            print(f"review {args.confirm} confirmed")
            return 0
        for r in open_reviews(conn):
            v = r["value"]
            print(f"#{r['id']}  {r['subject_type']} {r['subject_id']}: {v['before']} -> {v['after']}  "
                  f"by {r['source']}: {v['why']}")
    return 0


def _check_policies(args) -> int:
    from .config import load
    from athena.visibility.library import preview_policies

    lib = _lib(args)
    new = load(args.new_config)
    with lib.connect() as conn:
        p = preview_policies(conn, list(lib.cfg.policies), list(new.policies), _today(args))
    print(p.summary())
    for c in p.changes:
        print(f"  {c.item}: {c.before} -> {c.after}")
    return 0
