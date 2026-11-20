# SPDX-License-Identifier: GPL-3.0-or-later
"""Summarize results/*.jsonl into markdown tables (printed to stdout)."""
import glob
import json
import os
from collections import defaultdict
from statistics import mean, median

HERE = os.path.dirname(os.path.abspath(__file__))


def load():
    rows = []
    for p in sorted(glob.glob(os.path.join(HERE, "results", "*.jsonl"))):
        if os.path.basename(p).startswith("smoke"):
            continue
        rows += [json.loads(l) for l in open(p) if l.strip()]
    return rows


def table(rows, keys, title):
    groups = defaultdict(list)
    for r in rows:
        groups[tuple(r.get(k) for k in keys)].append(r)
    print(f"\n### {title}\n")
    hdr = keys + ["pages", "word F1", "median CER", "fields exact", "fields missed", "s/page", "peak GB", "loops"]
    print("| " + " | ".join(hdr) + " |")
    print("|" + "---|" * len(hdr))
    for k, g in sorted(groups.items(), key=lambda kv: [str(x) for x in kv[0]]):
        nf = sum(len(json.load(open(os.path.join(HERE, "testset", r["doc"], "fields.json")))) for r in g)
        missed = sum(len(r["fields_missing"]) for r in g)
        peak = max((r.get("peak_gb") or 0) for r in g)
        vals = [*map(lambda x: "–" if x is None else str(x), k), str(len(g)),
                f"{mean(r['word_f1'] for r in g):.3f}", f"{median(r['cer'] for r in g):.3f}",
                f"{(nf - missed) / nf:.1%}", str(missed),
                f"{mean(r['seconds'] for r in g):.1f}", f"{peak:.1f}" if peak else "–",
                str(sum(r["looped"] or r.get("hit_cap", False) for r in g))]
        print("| " + " | ".join(vals) + " |")


def per_doc(rows, engine_filter, title):
    print(f"\n### {title}\n")
    print("| doc | engine | word F1 | fields missed | s/page | tokens out |")
    print("|---|---|---|---|---|---|")
    for r in sorted(rows, key=lambda r: (r["doc"], r["engine"])):
        if engine_filter(r):
            print(f"| {r['doc']} | {r['engine']} | {r['word_f1']:.3f} | {len(r['fields_missing'])} | "
                  f"{r['seconds']:.1f} | {r.get('gen_tokens') or '–'} |")


if __name__ == "__main__":
    rows = load()
    vis_main = [r for r in rows if r["engine"] == "vision"]
    ds = [r for r in rows if r["engine"].startswith("ds")]
    table(vis_main, ["engine", "cond", "dpi"], "Apple Vision by capture condition and DPI")
    table(ds, ["engine", "cond", "dpi", "crops"], "DeepSeek-OCR-2 by model, condition, DPI and tile budget")
    main_cfg = [r for r in rows if r["cond"] == "camera" and r["dpi"] == 150 and r.get("crops") in (None, 6)]
    table(main_cfg, ["engine"], "Head to head: camera capture, 150 DPI, full tile budget")
    per_doc(rows, lambda r: r["cond"] == "camera" and r["dpi"] == 150 and r.get("crops") in (None, 6),
            "Per document: camera, 150 DPI")
