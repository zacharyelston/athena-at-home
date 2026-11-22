# SPDX-License-Identifier: GPL-3.0-or-later
"""``athena capture …`` commands: print the sheets, check a session, import it, describe the items, send them."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

NEEDS = "the capture tool needs its extra packages: pip install 'athena-at-home[capture]'"


def _default_jobs() -> int:
    return max(1, min(4, (os.cpu_count() or 2) // 2))


def add_parser(sub) -> None:
    cap = sub.add_parser("capture", help="turn session folders of photographs or scans into bundles")
    cap = cap.add_subparsers(dest="action", required=True)

    def common(p, staging=True):
        p.add_argument("--config", help="the config file with a [capture] section (default: ATHENA_CONFIG)")
        if staging:
            p.add_argument("--staging", metavar="DIR", help="where imported bundles wait (ATHENA_CAPTURE_STAGING)")

    def reading(p):
        p.add_argument("session", metavar="SESSION_FOLDER")
        p.add_argument("--series", help="series for items no folder name places (default: [capture] series)")
        p.add_argument("--order", choices=("auto", "time", "name"), default="auto",
                       help="page order: capture time, file name, or time when every file has one (default)")
        p.add_argument("--source", choices=("camera", "flatbed", "import"),
                       help="how the pages were captured (default: from each file's metadata)")
        p.add_argument("--ppi", type=float, help="resolution to record for pages with no scale card or scanner tag")
        p.add_argument("--rotate", type=int, choices=(0, 90, 180, 270), default=0,
                       help="turn every frame clockwise first, when the pages lie sideways in the photographs")
        p.add_argument("--duplex", action="store_true", help="pages alternate front and back")
        p.add_argument("--jobs", type=int, default=_default_jobs(), help="frames to work on at once")
        p.add_argument("--json", action="store_true", help="print the result as JSON")

    s = cap.add_parser("sheets", aliases=["separator"],
                       help="write a PDF to print: the separator sheet and a sheet of scale cards")
    common(s, staging=False)
    s.add_argument("--out", default="athena-capture-sheets.pdf", metavar="FILE.pdf")
    s.add_argument("--paper", choices=("letter", "a4"), default="letter")
    s.set_defaults(func=_sheets)

    c = cap.add_parser("check", help="list the pages to shoot again, before the session ends (exit 1 if any)")
    common(c)
    reading(c)
    c.set_defaults(func=_check)

    i = cap.add_parser("import", help="write one bundle per item into staging; a second run adds only what's new")
    common(i)
    reading(i)
    i.add_argument("--collection", help="collection for the bundles (default: [capture] collection)")
    i.add_argument("--station", help="this station's name, a slug (default: [capture] station)")
    i.set_defaults(func=_import)

    st = cap.add_parser("staged", help="list the bundles waiting in staging")
    common(st)
    st.set_defaults(func=_staged)

    d = cap.add_parser("describe", help="open a form on this computer to describe the staged items")
    common(d)
    d.add_argument("--port", type=int, default=8090)
    d.add_argument("--no-browser", action="store_true", help="don't open the browser")
    d.set_defaults(func=_describe)

    se = cap.add_parser("send", help="move staged bundles into the library's intake directory")
    common(se)
    se.add_argument("ids", nargs="*", metavar="BUNDLE_ID", help="only these (default: everything staged)")
    se.add_argument("--intake", metavar="DIR", help="the library's intake directory (ATHENA_INTAKE)")
    se.set_defaults(func=_send)

    x = cap.add_parser("discard", help="delete staged bundles, so their pages can be imported again")
    common(x)
    x.add_argument("ids", nargs="+", metavar="BUNDLE_ID")
    x.set_defaults(func=_discard)


def _config(args, **more):
    from .config import load, override

    cfg = load(args.config)
    staging = getattr(args, "staging", None)
    return override(cfg, staging=Path(staging).expanduser() if staging else None, **more)


def _options(args):
    from .session import Options

    return Options(series=args.series, order=args.order, source=args.source, ppi=args.ppi, rotate=args.rotate,
                   duplex=args.duplex, jobs=max(1, args.jobs))


def _fail(e: Exception) -> int:
    if isinstance(e, ImportError):
        print(f"{NEEDS}\n  ({e})", file=sys.stderr)
    else:
        print(f"refused: {e}", file=sys.stderr)
    return 2


def _sheets(args) -> int:
    try:
        from . import sheets

        cfg = _config(args)
        out = sheets.write_pdf(args.out, args.paper, cfg.tag_mm)
    except (ImportError, ValueError, OSError) as e:
        return _fail(e)
    print(f"{out}: a separator sheet and a sheet of scale cards ({cfg.tag_mm:g} mm)")
    print("Print at 100% (no “fit to page”), then measure the 100 mm ruler. If it isn't 100 mm, set print_scale in\n"
          "[capture] to what it measures divided by 100.")
    return 0


def _read(args, importing: bool) -> int:
    try:
        from . import session

        more = {"collection": args.collection, "station": args.station} if importing else {}
        cfg = _config(args, **more)
        run = session.import_session if importing else session.survey
        result = run(args.session, cfg, _options(args))
    except (ImportError, ValueError, OSError) as e:
        return _fail(e)
    if args.json:
        json.dump(session.as_data(result), sys.stdout, indent=1)
        print()
    else:
        print(session.report(result, imported=importing))
        if importing:
            new = sum(1 for i in result.items if i.status == "new" and i.bundle)
            print(f"{new} new bundle{'' if new == 1 else 's'} in {cfg.staging}. Next: athena capture describe, "
                  "then athena capture send.")
    if importing:
        return 1 if any(i.status in ("conflict", "unreadable") for i in result.items) else 0
    return 1 if result.problems else 0


def _check(args) -> int:
    return _read(args, importing=False)


def _import(args) -> int:
    return _read(args, importing=True)


def _need_staging(cfg):
    if cfg.staging is None:
        raise ValueError("no staging directory: set ATHENA_CAPTURE_STAGING, [capture] staging, or --staging")
    return cfg.staging


def _staged(args) -> int:
    try:
        from . import staging

        root = _need_staging(_config(args))
        rows = [staging.summary(b) for b in staging.staged(root)]
    except (ImportError, ValueError, OSError) as e:
        return _fail(e)
    for r in rows:
        title = r["item"].get("title") or "(not described yet)"
        flags = f", {r['flags']} to shoot again" if r["flags"] else ""
        print(f"{r['id']}  {r['series']}  {r['pages']} page{'' if r['pages'] == 1 else 's'}{flags}  {title}")
    print(f"{len(rows)} waiting in {root}", file=sys.stderr)
    return 0


def _describe(args) -> int:
    try:
        from . import web

        cfg = _config(args)
        _need_staging(cfg)
        url = f"http://127.0.0.1:{args.port}/"
        print(f"Describe items at {url} (this computer only). Press Ctrl-C to stop.")
        if not args.no_browser:
            import threading
            import webbrowser
            threading.Timer(1.0, webbrowser.open, [url]).start()
        web.serve(cfg, args.port)
    except (ImportError, ValueError, OSError) as e:
        return _fail(e)
    return 0


def _send(args) -> int:
    try:
        from . import staging

        cfg = _config(args, intake=Path(args.intake).expanduser() if args.intake else None)
        root = _need_staging(cfg)
        if cfg.intake is None:
            raise ValueError("no intake directory: set ATHENA_INTAKE, [library] intake, or --intake")
        results = staging.send(root, cfg.intake, args.ids or None, series=cfg.series_slugs() or None,
                               collections=set(cfg.known_collections) or None, types=set(cfg.types) or None)
    except (ImportError, ValueError, OSError) as e:
        return _fail(e)
    for bundle, status, detail in results:
        print(f"{bundle}  {status}{'  ' + detail if detail else ''}")
    sent = sum(1 for _, status, _ in results if status == "sent")
    print(f"{sent} sent to {cfg.intake}, {len(results) - sent} not", file=sys.stderr)
    return 0 if sent == len(results) else 1


def _discard(args) -> int:
    try:
        from . import staging

        root = _need_staging(_config(args))
        done = [(i, staging.discard(root, i)) for i in args.ids]
    except (ImportError, ValueError, OSError) as e:
        return _fail(e)
    for bundle, ok in done:
        print(f"{bundle}  {'discarded' if ok else 'not in staging'}")
    return 0 if all(ok for _, ok in done) else 1
