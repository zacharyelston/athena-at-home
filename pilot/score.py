# SPDX-License-Identifier: GPL-3.0-or-later
# Derived from Athena@Home phase0/bench.py (normalize, compact, CER, word F1 and exact key fields).
"""Score what the engines read: against typed transcriptions where there are any, and against each other everywhere.

    python -m pilot.score RESULTS_DIR --truth TRUTH_DIR

A transcription is a plain text file named as ``pages.tsv`` says (``<bundle-id>-<page>.txt``), typed from the paper,
not by correcting OCR output (which would favour the engine it started from). Beside it, optionally,
``<bundle-id>-<page>.keys.txt``: one name, date or place per line that must come out exactly.

Writes ``scores.jsonl`` to RESULTS_DIR (private: it lists the key items each engine missed).
"""
from __future__ import annotations

import argparse
import html
import re
import sys
from collections import Counter
from pathlib import Path

from . import OCR, PAGES, SCORES, PilotError, private_dir, read_jsonl, text_file, write_jsonl


def normalize(text: str) -> str:
    """Strip what markdown and the model's grounding tags add, so only the words are compared."""
    t = re.sub(r"<\|ref\|>.*?<\|/ref\|>", " ", text, flags=re.S)
    t = re.sub(r"<\|det\|>.*?<\|/det\|>", " ", t, flags=re.S)
    t = re.sub(r"<[^>]+>", " ", t)                           # HTML tables and tags
    t = html.unescape(t)
    t = re.sub(r"^\s*\|?\s*:?-{3,}.*$", " ", t, flags=re.M)  # markdown table separator rows
    t = re.sub(r"^\s*#+\s*", "", t, flags=re.M)              # headings
    t = t.replace("**", " ").replace("__", " ").replace("`", " ").replace("|", " ")
    t = t.replace("–", "-").replace("—", "-").replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    t = re.sub(r"\\([$#_*%&])", r"\1", t)                    # LaTeX-style escapes like \$
    return re.sub(r"\s+", " ", t).strip()


def compact(s: str) -> str:
    return re.sub(r"\s+", "", s)


def word_f1(a: str, b: str) -> float:
    """Order-free overlap of two normalized texts' words. 1.0 when both are empty."""
    aw, bw = Counter(a.split()), Counter(b.split())
    if not aw and not bw:
        return 1.0
    tp = sum((aw & bw).values())
    prec, rec = tp / max(sum(aw.values()), 1), tp / max(sum(bw.values()), 1)
    return 2 * prec * rec / max(prec + rec, 1e-9)


def score(pred_raw: str, truth_raw: str, keys: list[str] | None = None) -> dict:
    """CER and word F1 of a reading against the truth, and how many key items came out exactly (spacing aside)."""
    from rapidfuzz.distance import Levenshtein

    pred, truth = normalize(pred_raw), normalize(truth_raw)
    out = {"cer": round(Levenshtein.distance(truth, pred) / max(len(truth), 1), 4),
           "cer_ci": round(Levenshtein.distance(truth.lower(), pred.lower()) / max(len(truth), 1), 4),
           "word_f1": round(word_f1(pred, truth), 4)}
    if keys:
        squeezed = compact(pred)
        missing = [k for k in keys if compact(normalize(k)) not in squeezed]
        out.update(keys=len(keys), keys_exact=len(keys) - len(missing), keys_missing=missing)
    return out


def agreement(a_raw: str, b_raw: str) -> float:
    """Word F1 between two engines' readings of the same page: high agreement needs no transcription to see."""
    return round(word_f1(normalize(a_raw), normalize(b_raw)), 4)


def _read(path: Path) -> str | None:
    try:
        return path.read_text("utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def read_keys(path: Path) -> list[str] | None:
    text = _read(path)
    if text is None:
        return None
    return [line.strip() for line in text.splitlines() if line.strip()] or None


def score_run(out: str | Path, truth: str | Path | None = None) -> dict:
    out = private_dir(out)
    pages = read_jsonl(out / PAGES)
    if not pages:
        raise PilotError(f"no {PAGES} in {out}; run python -m pilot.run first")
    engines = sorted({r["engine"] for r in read_jsonl(out / OCR) if "error" not in r})
    truth_dir = private_dir(truth) if truth else None
    rows, with_truth = [], 0
    for p in pages:
        page = p["page"]
        texts = {e: t for e in engines if (t := _read(text_file(out, page, e))) is not None}
        if not texts:
            continue
        gt = keys = None
        if truth_dir:
            gt = _read(truth_dir / p["truth"])
            keys = read_keys(truth_dir / p["truth"].replace(".txt", ".keys.txt"))
        with_truth += gt is not None
        for engine, text in texts.items():
            row = {"page": page, "engine": engine}
            if gt is not None:
                row.update(score(text, gt, keys))
            if engine != "vision" and "vision" in texts:
                row["agrees_with_vision"] = agreement(text, texts["vision"])
            rows.append(row)
    write_jsonl(out / SCORES, rows)
    return {"pages": len({r["page"] for r in rows}), "with_truth": with_truth, "engines": engines}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m pilot.score", description=__doc__.splitlines()[0])
    ap.add_argument("results", metavar="RESULTS_DIR")
    ap.add_argument("--truth", metavar="TRUTH_DIR", help="typed transcriptions (private); without it, agreement only")
    a = ap.parse_args(argv)
    try:
        done = score_run(a.results, a.truth)
    except PilotError as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    print(f"scored {done['pages']} pages ({done['with_truth']} with a transcription) for {', '.join(done['engines'])}",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
