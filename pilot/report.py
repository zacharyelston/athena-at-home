# SPDX-License-Identifier: GPL-3.0-or-later
"""The pilot's report: aggregate numbers by material and by capture method, and what the agreed criteria say.

    python -m pilot.report RESULTS_DIR [--total-pages 2500] [--out report.md]

The report holds numbers, material names (a bundle's ``physical.format``), method names (the station name each
bundle was imported under) and engine names: no titles, no text, no file names. It is the one output of the pilot
that may leave the library's disk. Decisions are the author's; the report only applies the criteria agreed before
the scores came in.
"""
from __future__ import annotations

import argparse
import math
import re
import statistics
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from . import OCR, PAGES, SCORES, PilotError, private_dir, read_jsonl

# Agreed before any scores (docs/epics/garden-club-archive/D06-capture-pilot.md).
RECORD_F1, RECORD_KEYS = 0.95, 0.95      # OCR as the text of record
USABLE_F1 = 0.80                         # OCR shown, labelled uncorrected; below this, image only
DOCUMENT_PPI, PHOTO_PPI = 400, 600
F1_MARGIN = 0.02                         # "as good as the best method"; and what makes 6 fixed tiles worth it
SESSION_HOURS = 2.5
PAUSE_SECONDS = 300                      # a longer gap between frames is a break, not handling time

REASONS = (("blurred", "blurred"), ("soft at", "soft at an edge"), ("glare", "glare"), ("uneven", "uneven light"),
           ("too dark", "too dark"), ("camera not square", "camera not square"), ("ppi", "low resolution"),
           ("the page runs off", "page off the frame"))


def label(text) -> str:
    """A material or method name, cut down to something that can't carry a sentence of a record with it."""
    return re.sub(r"[^a-z0-9 -]", "", str(text or "").lower()).strip()[:24] or "not set"


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def _median(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def _pct(xs, q):
    xs = sorted(x for x in xs if x is not None)
    return xs[min(len(xs) - 1, max(0, math.ceil(q * len(xs)) - 1))] if xs else None


def _f(x, digits=3, suffix=""):
    return "–" if x is None else f"{x:.{digits}f}{suffix}"


def _engines(names) -> list[str]:
    return sorted(names, key=lambda e: (e != "vision", e))


def load(out: str | Path) -> dict:
    out = private_dir(out)
    pages = {p["page"]: p for p in read_jsonl(out / PAGES)}
    if not pages:
        raise PilotError(f"no {PAGES} in {out}; run python -m pilot.run first")
    ocr = {(r["page"], r["engine"]): r for r in read_jsonl(out / OCR)}
    scores = {(r["page"], r["engine"]): r for r in read_jsonl(out / SCORES)}
    return {"pages": pages, "ocr": ocr, "scores": scores}


def by_material(data: dict) -> list[dict]:
    """One row per material and engine setting."""
    groups: dict[tuple[str, str], list[tuple[dict, dict, dict]]] = defaultdict(list)
    for (page, engine), r in data["ocr"].items():
        if "error" not in r and page in data["pages"]:
            groups[(label(data["pages"][page]["material"]), engine)].append((data["pages"][page], r,
                                                                               data["scores"].get((page, engine), {})))
    rows = []
    for material in sorted({m for m, _ in groups}):
        for engine in _engines({e for m, e in groups if m == material}):
            got = groups[(material, engine)]
            scored = [s for _, _, s in got if "word_f1" in s]
            keys, exact = sum(s.get("keys", 0) for s in scored), sum(s.get("keys_exact", 0) for s in scored)
            rows.append({
                "material": material, "engine": engine, "pages": len(got), "truth_pages": len(scored),
                "f1": _mean(s["word_f1"] for s in scored), "f1_min": min((s["word_f1"] for s in scored), default=None),
                "cer": _mean(s["cer"] for s in scored), "keys": keys,
                "keys_exact": exact / keys if keys else None,
                "agreement": _mean(s.get("agrees_with_vision") for _, _, s in got),
                "loops": sum(bool(r.get("looped")) for _, r, _ in got),
                "caps": sum(bool(r.get("hit_cap")) for _, r, _ in got),
                "seconds": _median(r.get("seconds") for _, r, _ in got if "same_as" not in r),
                "six_tiles": _mean(1.0 if r.get("tiles") == 6 else 0.0 for _, r, _ in got) if engine.endswith("-auto")
                else None,
            })
    return rows


def policy(rows: list[dict], tag: str = "ds") -> list[dict]:
    """What the agreed criteria say for each material, from the production setting (Sieve's own tile choice)."""
    out = []
    for material in sorted({r["material"] for r in rows}):
        mine = {r["engine"]: r for r in rows if r["material"] == material}
        auto, six = mine.get(f"{tag}-auto"), mine.get(f"{tag}-6")
        if not auto or not auto["truth_pages"]:
            out.append({"material": material, "policy": "not decided", "why": "no transcribed pages"})
            continue
        f1, keys = auto["f1"], auto["keys_exact"]
        if f1 >= RECORD_F1 and keys is not None and keys >= RECORD_KEYS:
            verdict, why = "text of record", f"word F1 {f1:.3f}, key items {keys:.0%}"
        elif f1 >= USABLE_F1:
            short = "no key items typed" if keys is None else f"key items {keys:.0%}"
            verdict, why = "shown, labelled uncorrected", f"word F1 {f1:.3f}, {short}"
        else:
            verdict, why = "image only", f"word F1 {f1:.3f}"
        gain = six["f1"] - f1 if six and six["f1"] is not None else None
        out.append({"material": material, "policy": verdict, "why": why, "truth_pages": auto["truth_pages"],
                    "six_tiles_gain": gain, "six_tiles_worth_it": gain is not None and gain >= F1_MARGIN})
    return out


def _seconds_per_page(pages: list[dict]) -> float | None:
    """Mean time from one page to the next, from the masters' own clocks. It includes the handling in between
    (turning the page, the separator sheet before a new item); a gap of five minutes or more is a break."""
    times = sorted(datetime.fromisoformat(p["taken"]) for p in pages if p.get("taken"))
    gaps = [(b - a).total_seconds() for a, b in zip(times, times[1:])]
    gaps = [g for g in gaps if 0 < g < PAUSE_SECONDS]
    return sum(gaps) / len(gaps) if gaps else None


def by_method(data: dict, tag: str = "ds") -> tuple[list[dict], bool]:
    """One row per capture method, over the pages tagged ``comparison`` (or every page, if none is tagged)."""
    pages = list(data["pages"].values())
    tagged = [p for p in pages if p.get("comparison")]
    chosen = tagged or pages
    rows = []
    for method in sorted({label(p["method"]) for p in chosen}):
        mine = [p for p in chosen if label(p["method"]) == method]
        docs = [p for p in mine if p["kind"] not in ("photo", "blank")]
        photos = [p for p in mine if p["kind"] == "photo"]
        scores = [data["scores"].get((p["page"], f"{tag}-auto"), {}) for p in docs]
        seconds = _seconds_per_page(mine)

        def reasons(prefixes, group=mine):
            return sum(any(f.startswith(prefixes) for f in p.get("flags", [])) for p in group)

        rows.append({
            "method": method, "pages": len(mine), "documents": len(docs), "photos": len(photos),
            "ppi": _median(p["ppi"] for p in docs), "ppi_min": min((p["ppi"] for p in docs if p["ppi"]), default=None),
            "ppi_unknown": sum(p["ppi"] is None for p in mine),
            "photo_ppi_min": min((p["ppi"] for p in photos if p["ppi"]), default=None),
            "sharpness": _median(p["quality"].get("sharpness") for p in docs),
            "glare_pages": reasons(("glare",)), "soft_pages": reasons(("blurred", "soft at")),
            "flagged": sum(bool(p.get("flags")) for p in mine),
            "f1": _mean(s.get("word_f1") for s in scores), "truth_pages": sum("word_f1" in s for s in scores),
            "agreement": _mean(s.get("agrees_with_vision") for s in scores),
            "seconds": seconds, "pages_per_hour": 3600 / seconds if seconds else None,
            "bytes_per_page": _mean(p["raw_bytes"] + p["page_bytes"] for p in mine),
        })
    return rows, bool(tagged)


def pick_method(rows: list[dict]) -> tuple[str | None, list[str]]:
    """The fastest method that meets the document criteria, and why each other method doesn't."""
    known = [r["f1"] if r["f1"] is not None else r["agreement"] for r in rows if r["documents"]]
    best = max((k for k in known if k is not None), default=None)
    fit, notes = [], []
    for r in rows:
        if not r["documents"]:
            continue
        quality = r["f1"] if r["f1"] is not None else r["agreement"]
        fails = []
        if r["ppi_min"] is None:
            fails.append("resolution not measured")
        elif r["ppi_min"] < DOCUMENT_PPI:
            fails.append(f"lowest page {r['ppi_min']:.0f} ppi, under {DOCUMENT_PPI}")
        if r["glare_pages"]:
            fails.append(f"glare on {r['glare_pages']} pages")
        if r["soft_pages"]:
            fails.append(f"{r['soft_pages']} soft or blurred pages")
        if best is not None and quality is not None and quality < best - F1_MARGIN:
            fails.append(f"OCR {quality:.3f} against {best:.3f} for the best method")
        if fails:
            notes.append(f"{r['method']}: {'; '.join(fails)}")
        else:
            fit.append(r)
    fit.sort(key=lambda r: (r["seconds"] is None, r["seconds"] or 0))
    return (fit[0]["method"] if fit else None), notes


def settings(data: dict) -> tuple[list[dict], dict[str, int]]:
    """What the capture tool measured on these pages, per method, for choosing its thresholds."""
    pages = list(data["pages"].values())
    rows = []
    for method in sorted({label(p["method"]) for p in pages}):
        q = [p["quality"] for p in pages if label(p["method"]) == method and p["kind"] in ("text", "mixed")]
        rows.append({"method": method, "pages": len(q),
                     "sharpness_p5": _pct([x.get("sharpness") for x in q], 0.05),
                     "sharpness": _median(x.get("sharpness") for x in q),
                     "laplacian_p5": _pct([x.get("laplacian_var") for x in q], 0.05),
                     "laplacian": _median(x.get("laplacian_var") for x in q),
                     "evenness_p5": _pct([x.get("evenness") for x in q], 0.05),
                     "brightness_p5": _pct([x.get("brightness") for x in q], 0.05),
                     "glare_p95": _pct([x.get("glare") for x in q], 0.95),
                     "keystone_p95": _pct([x.get("keystone") for x in q], 0.95)})
    counts: dict[str, int] = defaultdict(int)
    for p in pages:
        for flag in p.get("flags", []):
            counts[next((name for prefix, name in REASONS if flag.startswith(prefix)), "other")] += 1
    return rows, dict(counts)


def render(data: dict, total_pages: int | None = None, tag: str = "ds", today: str | None = None) -> str:
    pages = data["pages"]
    materials = by_material(data)
    methods, tagged = by_method(data, tag)
    setting_rows, reasons = settings(data)
    engines = _engines({e for _, e in data["ocr"]})
    errors = sum("error" in r for r in data["ocr"].values())
    truth = len({p for (p, _), s in data["scores"].items() if "word_f1" in s})
    out = [f"# Capture pilot: report{' (' + today + ')' if today else ''}", "",
           "Aggregate numbers only: no titles, text or file names. Decisions are the author's.", "",
           f"- {len(pages)} pages in {len({p['bundle'] for p in pages.values()})} items; "
           f"{truth} with a typed transcription.",
           f"- Engines: {', '.join(engines) or 'none run yet'}." + (f" {errors} OCR requests failed." if errors else ""),
           "", "## OCR by material", "",
           "| Material | Engine | Pages | Transcribed | Word F1 | Lowest | CER | Key items exact | Agrees with Vision "
           "| Loops | Hit cap | s/page | Chose 6 tiles |", "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in materials:
        out.append(f"| {r['material']} | {r['engine']} | {r['pages']} | {r['truth_pages']} | {_f(r['f1'])} | "
                   f"{_f(r['f1_min'])} | {_f(r['cer'])} | "
                   f"{'–' if r['keys_exact'] is None else format(r['keys_exact'], '.0%') + ' of ' + str(r['keys'])} | "
                   f"{_f(r['agreement'])} | {r['loops']} | {r['caps']} | {_f(r['seconds'], 1)} | "
                   f"{'–' if r['six_tiles'] is None else format(r['six_tiles'], '.0%')} |")
    out += ["", "Word F1 ignores reading order and CER does not: a high F1 with a high CER means the words were read "
            "but out of order (columns read across).",
            "", "## OCR policy by the agreed criteria", "",
            f"Text of record: word F1 ≥ {RECORD_F1} and key items exact ≥ {RECORD_KEYS:.0%}. Shown, labelled "
            f"uncorrected: word F1 ≥ {USABLE_F1}. Image only below that. Judged on `{tag}-auto`, the setting Sieve "
            "runs.", "", "| Material | Policy | Because | Transcribed pages | 6 fixed tiles |", "|---|---|---|---|---|"]
    for p in policy(materials, tag):
        gain = p.get("six_tiles_gain")
        six = "–" if gain is None else (f"worth it ({gain:+.3f})" if p["six_tiles_worth_it"] else f"no ({gain:+.3f})")
        out.append(f"| {p['material']} | **{p['policy']}** | {p['why']} | {p.get('truth_pages', 0)} | {six} |")

    out += ["", "## Capture methods", "",
            ("Over the pages tagged `comparison`." if tagged else
             "No item is tagged `comparison`, so this is over every page; the methods saw different pages."), "",
            "| Method | Pages | Median ppi | Lowest ppi | Lowest photo ppi | Sharpness | Glare pages | Soft pages "
            "| Flagged | Word F1 | Agrees with Vision | s/page | Pages/hour | MB/page |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in methods:
        out.append(f"| {r['method']} | {r['pages']} | {_f(r['ppi'], 0)} | {_f(r['ppi_min'], 0)} | "
                   f"{_f(r['photo_ppi_min'], 0)} | {_f(r['sharpness'], 2)} | {r['glare_pages']} | {r['soft_pages']} | "
                   f"{r['flagged']} | {_f(r['f1'])} | {_f(r['agreement'])} | {_f(r['seconds'], 0)} | "
                   f"{_f(r['pages_per_hour'], 0)} | {_f(r['bytes_per_page'] / 1e6 if r['bytes_per_page'] else None, 1)} |")
    chosen, notes = pick_method(methods)
    out += ["", "s/page is from one page to the next by the files' own clocks, handling and separator sheets "
            "included; breaks of five minutes or more are left out.",
            "", f"Documents need every page at ≥ {DOCUMENT_PPI} ppi, no glare, no soft pages, and OCR within "
                f"{F1_MARGIN} of the best method; the fastest such method wins. Photographs need ≥ {PHOTO_PPI} ppi.", ""]
    out.append(f"- By those criteria, for documents: **{chosen}**." if chosen
               else "- By those criteria, no method qualifies for documents yet.")
    out += [f"- Not {n}." for n in notes]
    for r in methods:
        if r["photos"]:
            ok = r["photo_ppi_min"] is not None and r["photo_ppi_min"] >= PHOTO_PPI
            out.append(f"- Photographs with {r['method']}: {'meets' if ok else 'does not meet'} {PHOTO_PPI} ppi "
                       f"(lowest {_f(r['photo_ppi_min'], 0)}).")
        if r["seconds"] is None:
            out.append(f"- {r['method']}: no capture times in the files; time a few pages by hand.")

    out += ["", "## Sessions and storage", ""]
    n = total_pages
    out.append(f"For {n:,} pages:" if n else "Per 1,000 pages (give `--total-pages` for the whole archive):")
    out += ["", "| Method | Hours of capture | Sessions of 2.5 hours | Originals and page images |", "|---|---|---|---|"]
    for r in methods:
        count = n or 1000
        hours = count / r["pages_per_hour"] if r["pages_per_hour"] else None
        gb = count * r["bytes_per_page"] / 1e9 if r["bytes_per_page"] else None
        out.append(f"| {r['method']} | {_f(hours, 1)} | {'–' if hours is None else math.ceil(hours / SESSION_HOURS)} | "
                   f"{_f(gb, 1, ' GB')} |")

    out += ["", "## What the capture tool measured", "",
            "For setting `[capture]` thresholds: on text pages, the weakest 5% and the median. A threshold belongs "
            "below what good pages score and above what the re-shot ones did.", "",
            "| Method | Text pages | Sharpness p5 | median | Laplacian p5 | median | Evenness p5 | Brightness p5 "
            "| Glare p95 | Out of square p95 |", "|---|---|---|---|---|---|---|---|---|---|"]
    for r in setting_rows:
        out.append(f"| {r['method']} | {r['pages']} | {_f(r['sharpness_p5'], 2)} | {_f(r['sharpness'], 2)} | "
                   f"{_f(r['laplacian_p5'], 0)} | {_f(r['laplacian'], 0)} | {_f(r['evenness_p5'], 2)} | "
                   f"{_f(r['brightness_p5'], 0)} | {_f(r['glare_p95'], 3)} | {_f(r['keystone_p95'], 3)} |")
    out += ["", "Re-shoot reasons under the thresholds in force at import: "
            + (", ".join(f"{name} {count}" for name, count in sorted(reasons.items())) or "none") + "."]
    return "\n".join(out) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m pilot.report", description=__doc__.splitlines()[0])
    ap.add_argument("results", metavar="RESULTS_DIR")
    ap.add_argument("--total-pages", type=int, help="pages in the whole archive, for the session and storage estimate")
    ap.add_argument("--tag", default="ds", help="the model run to judge policy on (default ds)")
    ap.add_argument("--out", metavar="FILE.md", help="write the report here instead of printing it")
    a = ap.parse_args(argv)
    try:
        text = render(load(a.results), a.total_pages, a.tag, today=datetime.now().date().isoformat())
    except PilotError as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    if a.out:
        Path(a.out).write_text(text, "utf-8")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
