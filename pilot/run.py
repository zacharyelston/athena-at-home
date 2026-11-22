# SPDX-License-Identifier: GPL-3.0-or-later
"""OCR every page of a set of capture bundles with both engines, and list the pages.

    python -m pilot.run BUNDLES_DIR --out RESULTS_DIR                     in process (Apple Silicon, like `sieve local`)
    python -m pilot.run BUNDLES_DIR --out RESULTS_DIR --nats-url nats://m1.home:4222      through a Sieve worker
    python -m pilot.run BUNDLES_DIR --out RESULTS_DIR --list              only list the pages (no OCR)

BUNDLES_DIR is a capture staging directory or a library's intake (bundles in ``ingested/`` are found too). Each
page is sent the way the library sends it: a JPEG at about 200 ppi. One request asks for Sieve's own tile choice
and Apple Vision's text; a second asks for 6 tiles, unless the first already used 6.

Written to RESULTS_DIR (private): ``pages.jsonl`` and ``pages.tsv`` (the pages, with the name of the transcription
file to type for each), ``ocr.jsonl`` (one row per page and engine setting) and ``text/`` (what each engine read).
Running again continues where it stopped.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import sys
from pathlib import Path
from typing import Callable

from athena.bundle import read_bundle

from . import OCR, PAGES, PilotError, private_dir, read_jsonl, text_file, truth_name, write_jsonl

OCR_PPI, OCR_MAX_EDGE = 200, 2400       # the library's defaults (athena.library.config)
QUALITY_KEYS = ("sharpness", "sharpness_min", "laplacian_var", "glare", "evenness", "brightness", "keystone",
                "skew_deg", "cropped")
Asker = Callable[[dict], dict]


def bundles_in(root: Path) -> list[Path]:
    found = [d for base in (root, root / "ingested") if base.is_dir()
             for d in sorted(base.iterdir()) if (d / "manifest.json").is_file()]
    if not found:
        raise PilotError(f"no capture bundles in {root}")
    return found


def inventory(bundles: list[Path]) -> list[dict]:
    """One row per page: what the report groups by (material, method), and what a person needs to find the page."""
    rows = []
    for d in bundles:
        b = read_bundle(d)
        m = b.manifest
        quality = {}
        if m.get("quality"):
            try:
                quality = json.loads((d / m["quality"]).read_text("utf-8"))
            except (OSError, json.JSONDecodeError):
                quality = {}
        item = b.item
        for p in b.pages:
            q = quality.get(str(p.n), {})
            rows.append({
                "page": f"{b.id}/{p.n}", "bundle": b.id, "n": p.n, "title": item.get("title", ""),
                "material": (item.get("physical", {}).get("format") or "not set").strip().lower(),
                "method": m["station"]["name"], "source": m["source"], "device": m.get("device", {}).get("model", ""),
                "comparison": "comparison" in item.get("tags", []),
                "kind": p.kind, "ppi": p.ppi, "ppi_source": p.ppi_source, "width": p.width, "height": p.height,
                "raw_bytes": p.raw.stat().st_size, "page_bytes": p.page.stat().st_size, "taken": _taken(p.raw),
                "quality": {k: q[k] for k in QUALITY_KEYS if k in q}, "flags": q.get("flags", []),
                "image": str(p.page), "truth": truth_name(f"{b.id}/{p.n}"),
            })
    return rows


def _taken(raw: Path) -> str | None:
    """When the frame was captured, from the master's own metadata (for seconds per page)."""
    try:
        from athena.capture import images
        t = images.facts(raw).taken
        return t.isoformat() if t else None
    except Exception:
        return None


def ocr_jpeg(row: dict) -> bytes:
    from athena.library.intake import ocr_derivative
    return ocr_derivative(Path(row["image"]), row["ppi"], OCR_PPI, OCR_MAX_EDGE)


def local_asker(model: str | None = None) -> Asker:
    """DeepSeek-OCR-2 and Apple Vision in this process, through the same function the worker serves."""
    from sieve import service
    from sieve.engine import DEFAULT_MODEL, Engine
    from sieve.worker import _tier0_fn

    engine, tier0 = Engine(model or DEFAULT_MODEL), _tier0_fn()
    return lambda req: service.handle(req, engine, tier0)


def nats_asker(url: str, subject: str = "ocr.page", creds: str | None = None, tls_hostname: str | None = None,
               timeout_s: float = 300.0) -> Asker:
    from sieve.client import SieveClient, SieveError

    client = SieveClient(url, subject, creds, tls_hostname, timeout_s=timeout_s)

    def ask(req: dict) -> dict:
        try:
            return asyncio.run(client.arequest(req))
        except SieveError as e:
            return {"error": str(e)}
    return ask


def _row(page: str, engine: str, reply: dict, **more) -> dict:
    if "error" in reply:
        return {"page": page, "engine": engine, "error": str(reply["error"])[:300]}
    keys = ("tiles", "tile_reason", "seconds", "gen_tokens", "image_tokens", "looped", "hit_cap", "model")
    return {"page": page, "engine": engine, "chars": len(reply.get("text", "")),
            **{k: reply.get(k) for k in keys}, **more}


def run(bundles_root: str | Path, out: str | Path, ask: Asker | None, tag: str = "ds", skip_kinds=("blank",),
        limit: int | None = None, log=print) -> dict:
    """List the pages and (with ``ask``) OCR those not yet done. Returns counts."""
    out = private_dir(out, create=True)
    pages = inventory(bundles_in(Path(bundles_root)))
    write_jsonl(out / PAGES, pages)
    with open(out / "pages.tsv", "w", encoding="utf-8") as f:
        f.write("page\ttitle\tmaterial\tmethod\tkind\timage\ttranscription file\n")
        for r in pages:
            f.write("\t".join(str(r[k]).replace("\t", " ") for k in
                              ("page", "title", "material", "method", "kind", "image", "truth")) + "\n")
    counts = {"pages": len(pages), "ocr": 0, "skipped": 0, "errors": 0, "already": 0}
    if ask is None:
        return counts
    (out / "text").mkdir(exist_ok=True)
    rows = read_jsonl(out / OCR)
    done = {(r["page"], r["engine"]) for r in rows if "error" not in r}
    auto, six = f"{tag}-auto", f"{tag}-6"
    todo = [r for r in pages if r["kind"] not in skip_kinds]
    counts["skipped"] = len(pages) - len(todo)
    for r in todo[:limit]:
        page = r["page"]
        if (page, auto) in done and (page, six) in done:
            counts["already"] += 1
            continue
        new = []
        image = base64.b64encode(ocr_jpeg(r)).decode()
        first = ask({"image_b64": image, "tiles": "auto", "tier0": True, "mode": "markdown", "id": page})
        new.append(_row(page, auto, first))
        if "error" not in first:
            text_file(out, page, auto).write_text(first["text"], "utf-8")
            if first.get("tier0") and (page, "vision") not in done:
                text_file(out, page, "vision").write_text(first["tier0"]["text"], "utf-8")
                new.append({"page": page, "engine": "vision", "chars": first["tier0"]["chars"]})
            if first.get("tiles") == 6:
                text_file(out, page, six).write_text(first["text"], "utf-8")
                new.append(_row(page, six, first, same_as=auto))
            else:
                second = ask({"image_b64": image, "tiles": 6, "tier0": False, "mode": "markdown", "id": page})
                new.append(_row(page, six, second))
                if "error" not in second:
                    text_file(out, page, six).write_text(second["text"], "utf-8")
        failed = [n for n in new if "error" in n]
        counts["errors"] += len(failed)
        counts["ocr"] += 1
        # keep the latest row for each page and setting
        fresh = {(n["page"], n["engine"]) for n in new}
        rows = [x for x in rows if (x["page"], x["engine"]) not in fresh] + new
        write_jsonl(out / OCR, rows)
        seconds = sum(n.get("seconds") or 0 for n in new if "same_as" not in n)
        log(f"{page}  {r['material']:<12} {seconds:6.1f}s  tiles {first.get('tiles', '-')}"
            + (f"  ERROR {failed[0]['error']}" if failed else ""))
    return counts


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m pilot.run", description=__doc__.splitlines()[0])
    ap.add_argument("bundles", metavar="BUNDLES_DIR")
    ap.add_argument("--out", required=True, metavar="RESULTS_DIR", help="a private directory outside the repository")
    ap.add_argument("--list", action="store_true", help="only write the list of pages; no OCR")
    ap.add_argument("--model", help="DeepSeek-OCR-2 model for an in-process run (default: Sieve's)")
    ap.add_argument("--tag", default="ds", help="name for this model's rows, e.g. bf16 for a second run (default ds)")
    ap.add_argument("--nats-url", help="use a running Sieve worker instead of loading the model here")
    ap.add_argument("--subject", default="ocr.page")
    ap.add_argument("--creds")
    ap.add_argument("--tls-hostname")
    ap.add_argument("--limit", type=int, help="stop after this many pages")
    a = ap.parse_args(argv)
    try:
        ask = None
        if not a.list:
            ask = (nats_asker(a.nats_url, a.subject, a.creds, a.tls_hostname) if a.nats_url
                   else local_asker(a.model))
        counts = run(a.bundles, a.out, ask, tag=a.tag, limit=a.limit)
    except PilotError as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    except ImportError as e:
        print(f"can't load the engines here ({e}). An in-process run needs Apple Silicon and the sieve extra "
              "(pip install -e '.[sieve]'); otherwise pass --nats-url for a running Sieve worker, or --list.",
              file=sys.stderr)
        return 2
    print(f"{counts['pages']} pages listed in {a.out}/pages.tsv; OCR run on {counts['ocr']}, "
          f"{counts['already']} done before, {counts['skipped']} blank pages skipped, {counts['errors']} errors",
          file=sys.stderr)
    return 1 if counts["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
