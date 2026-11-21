# SPDX-License-Identifier: GPL-3.0-or-later
"""Athena@Home command line.

  athena bundle validate DIR...   check capture bundles (schema, files, checksums, pages, dates)
  athena library setup|intake|ingest|ocr|search|publish-events|status
  athena library explain|set-visibility|reviews|check-policies
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _slugs(text: str | None) -> set[str] | None:
    return {s.strip() for s in text.split(",") if s.strip()} if text else None


def _bundle_validate(args) -> int:
    from .bundle import validate

    bad = 0
    results = []
    for d in args.dirs:
        rep = validate(Path(d), collections=_slugs(args.collections), series=_slugs(args.series),
                       types=_slugs(args.types))
        bad += not rep.ok
        if args.json:
            results.append({"path": str(rep.path), "ok": rep.ok, "errors": rep.errors, "warnings": rep.warnings})
        else:
            print(rep)
    if args.json:
        json.dump(results, sys.stdout, indent=1)
        print()
    elif len(args.dirs) > 1:
        print(f"{len(args.dirs) - bad} valid, {bad} invalid", file=sys.stderr)
    return 1 if bad else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="athena", description="Athena@Home")
    sub = ap.add_subparsers(dest="cmd", required=True)

    bundle = sub.add_parser("bundle", help="capture bundles").add_subparsers(dest="action", required=True)
    v = bundle.add_parser("validate", help="check capture bundle directories")
    v.add_argument("dirs", nargs="+", metavar="DIR")
    v.add_argument("--collections", help="known collection slugs, comma-separated (others are warnings)")
    v.add_argument("--series", help="known series slugs, comma-separated (others are warnings)")
    v.add_argument("--types", help="known item type slugs, comma-separated (others are warnings)")
    v.add_argument("--json", action="store_true", help="print results as JSON")
    v.set_defaults(func=_bundle_validate)

    from .library.cli import add_parser as add_library
    add_library(sub)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
