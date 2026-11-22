# SPDX-License-Identifier: GPL-3.0-or-later
"""The capture pilot: OCR a sample of real pages with both engines, score them, and report aggregate numbers.

    python -m pilot.run BUNDLES_DIR --out RESULTS_DIR         OCR every page (Apple Vision; DeepSeek-OCR-2 auto and 6 tiles)
    python -m pilot.score RESULTS_DIR --truth TRUTH_DIR       score against typed transcriptions, and engine agreement
    python -m pilot.report RESULTS_DIR                        tables of aggregate numbers, safe to share

Results hold the text of real records, so they are written only outside this repository (docs/pilot.md).
"""
from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PAGES, OCR, SCORES, TEXT = "pages.jsonl", "ocr.jsonl", "scores.jsonl", "text"


class PilotError(Exception):
    pass


def private_dir(path: str | Path, create: bool = False) -> Path:
    """``path``, refused if it is inside the repository: real page text must never be one ``git add`` from public."""
    p = Path(path).expanduser().resolve()
    if p == REPO or REPO in p.parents:
        raise PilotError(f"{p} is inside the repository; pilot results hold real page text. "
                         "Choose a directory on the library's disk.")
    if create:
        p.mkdir(parents=True, exist_ok=True)
    return p


def read_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text("utf-8").splitlines():
        if line.strip():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue                       # a line cut short by an interrupted run
    return rows


def write_jsonl(path: Path, rows: list[dict]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), "utf-8")
    tmp.replace(path)


def text_file(out: Path, page: str, engine: str) -> Path:
    bundle, n = page.split("/")
    return out / TEXT / f"{bundle}-{int(n):03d}.{engine}.txt"


def truth_name(page: str) -> str:
    bundle, n = page.split("/")
    return f"{bundle}-{int(n):03d}.txt"
