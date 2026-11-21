# SPDX-License-Identifier: GPL-3.0-or-later
"""``athena site …`` commands."""
from __future__ import annotations

import os
import sys
from pathlib import Path


def add_parser(sub) -> None:
    site = sub.add_parser("site", help="build, check and preview static sites").add_subparsers(dest="action",
                                                                                              required=True)
    b = site.add_parser("build", help="build a static site from a feed directory (replaced as a whole)")
    b.add_argument("feed_dir", metavar="FEED_DIR")
    b.add_argument("--out", required=True, metavar="DIR")
    b.add_argument("--site", metavar="NAME", help="the [site.NAME] section to use (default: the feed's name)")
    b.add_argument("--config", help="the config file holding the [site.*] sections (default: ATHENA_CONFIG)")
    b.add_argument("--no-search", action="store_true", help="build without Pagefind's search")
    b.set_defaults(func=_build)

    c = site.add_parser("check", help="check a built site (exit 1 on any problem)")
    c.add_argument("dir")
    c.add_argument("--feed", metavar="FEED_DIR", help="also compare the site against the feed it was built from")
    c.add_argument("--forbid-file", metavar="FILE", help="strings that must not appear anywhere, one per line")
    c.set_defaults(func=_check)

    s = site.add_parser("serve", help="serve a built site for a preview (this computer only, unless --bind)")
    s.add_argument("dir")
    s.add_argument("--port", type=int, default=8080)
    s.add_argument("--bind", default="127.0.0.1", help="0.0.0.0 lets other devices on the network see the preview")
    s.set_defaults(func=_serve)


def _build(args) -> int:
    import json

    from .build import FEED_MARKER, SiteError, build
    from .config import SiteConfigError, load_site

    config = args.config or os.environ.get("ATHENA_CONFIG") or "library.toml"
    try:
        name = args.site
        if name is None:
            name = json.loads((Path(args.feed_dir) / FEED_MARKER).read_text("utf-8")).get("feed", "")
        r = build(args.feed_dir, load_site(config, name), args.out, search=not args.no_search)
    except (SiteError, SiteConfigError) as e:
        print(f"refused: {e}", file=sys.stderr)
        return 1
    except (OSError, ValueError) as e:
        print(f"refused: can't read the feed at {args.feed_dir}: {e}", file=sys.stderr)
        return 1
    search = f"search by Pagefind {r.pagefind}" if r.search else "no search"
    print(f"site for feed {r.feed}: {r.items} items, {r.pages} pages, {r.html} HTML pages; {search} -> {r.out}")
    print(f"  {r.files} files, {r.bytes / 1e6:.1f} MB; search engines {'may' if r.indexable else 'may not'} index it")
    for w in r.warnings:
        print(f"  warning: {w}", file=sys.stderr)
    return 0


def _check(args) -> int:
    from .check import check_site

    forbidden = []
    if args.forbid_file:
        forbidden = [ln.strip() for ln in Path(args.forbid_file).read_text("utf-8").splitlines() if ln.strip()]
    problems = check_site(args.dir, feed_dir=args.feed, forbidden=forbidden)
    for p in problems:
        print(p)
    print(f"{args.dir}: " + (f"{len(problems)} problem(s)" if problems else "clean"), file=sys.stderr)
    return 1 if problems else 0


def make_server(directory: str | Path, bind: str = "127.0.0.1", port: int = 8080):
    """An HTTP server for a built site: static files, and the site's own 404 page for anything missing."""
    from functools import partial
    from http import HTTPStatus
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

    directory = Path(directory)

    class Handler(SimpleHTTPRequestHandler):
        def send_error(self, code, message=None, explain=None):
            page = directory / "404.html"
            if code == HTTPStatus.NOT_FOUND and page.is_file() and self.command != "HEAD":
                body = page.read_bytes()
                self.send_response(HTTPStatus.NOT_FOUND)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                super().send_error(code, message, explain)

        def log_message(self, fmt, *a):          # quiet: a preview, not a server to monitor
            pass

    return ThreadingHTTPServer((bind, port), partial(Handler, directory=str(directory)))


def _serve(args) -> int:
    from .build import MARKER

    if not (Path(args.dir) / MARKER).is_file():
        print(f"refused: {args.dir} is not a site directory (no {MARKER})", file=sys.stderr)
        return 1
    server = make_server(args.dir, args.bind, args.port)
    where = "this computer only" if args.bind in ("127.0.0.1", "localhost", "::1") else "anyone on this network"
    print(f"serving {args.dir} at http://{args.bind}:{server.server_address[1]}/ ({where}); Ctrl-C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
