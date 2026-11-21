# SPDX-License-Identifier: GPL-3.0-or-later
"""``athena publish …`` commands."""
from __future__ import annotations

import json
import os
import sys
from datetime import date
from pathlib import Path


def add_parser(sub) -> None:
    pub = sub.add_parser("publish", help="build and check feeds").add_subparsers(dest="action", required=True)

    b = pub.add_parser("build", help="build a feed directory (replaced as a whole)")
    b.add_argument("feed")
    b.add_argument("--out", required=True, metavar="DIR")
    b.add_argument("--jobs", type=int, default=min(4, os.cpu_count() or 1), help="image worker processes")
    b.add_argument("--today", help="decide visibility as of this date (YYYY-MM-DD)")
    b.add_argument("--config")
    b.set_defaults(func=_build)

    c = pub.add_parser("check", help="check a built feed against the library (exit 1 on any problem)")
    c.add_argument("feed")
    c.add_argument("dir")
    c.add_argument("--forbid-file", metavar="FILE", help="strings that must not appear anywhere, one per line")
    c.add_argument("--config")
    c.set_defaults(func=_check)

    d = pub.add_parser("diff", help="paths removed, changed and added between two builds")
    d.add_argument("old")
    d.add_argument("new")
    d.add_argument("--json", action="store_true")
    d.set_defaults(func=_diff)


def _lib(args):
    from athena.library.config import load
    from athena.library.core import Library

    return Library(load(args.config))


def _build(args) -> int:
    from .build import PublishError, build

    lib = _lib(args)
    try:
        r = build(lib, args.feed, args.out, today=date.fromisoformat(args.today) if args.today else None,
                  jobs=args.jobs)
    except PublishError as e:
        print(f"refused: {e}", file=sys.stderr)
        return 1
    print(f"feed {r.feed}: {r.items} items, {r.pages} pages, {r.images} images ({r.rendered} rendered) -> {r.out}")
    print(f"  entered the feed: {len(r.shared)}; left it: {len(r.unshared)}")
    for rid, why in r.skipped:
        print(f"  left out {rid}: {why}", file=sys.stderr)
    return 0


def _check(args) -> int:
    from .build import PublishError
    from .leakcheck import check_feed, scan_for_strings

    lib = _lib(args)
    try:
        problems = check_feed(lib, args.feed, args.dir)
    except PublishError as e:
        print(f"refused: {e}", file=sys.stderr)
        return 1
    if args.forbid_file:
        forbidden = [ln.strip() for ln in Path(args.forbid_file).read_text("utf-8").splitlines() if ln.strip()]
        problems += scan_for_strings(args.dir, forbidden)
    for p in problems:
        print(p)
    print(f"{args.dir}: " + (f"{len(problems)} problem(s)" if problems else "clean"), file=sys.stderr)
    return 1 if problems else 0


def _diff(args) -> int:
    from .leakcheck import diff

    d = diff(args.old, args.new)
    if args.json:
        json.dump(d, sys.stdout, indent=1)
        print()
    else:
        for kind in ("removed", "changed", "added"):
            for path in d[kind]:
                print(f"{kind}\t{path}")
    return 0
