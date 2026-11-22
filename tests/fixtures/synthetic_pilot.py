# SPDX-License-Identifier: GPL-3.0-or-later
"""A synthetic capture pilot for tests and dry runs: the made-up historical pages of ``phase0/gen_testset.py``,
"captured" by more than one method, imported with the capture tool, described, and transcribed.

    python tests/fixtures/synthetic_pilot.py OUT_DIR [--ppi 200]

writes OUT_DIR/bundles (capture bundles), OUT_DIR/truth (the transcriptions) and prints the commands to run next.
No real records: every name, date and place is invented.
"""
from __future__ import annotations

import argparse
import sys
from datetime import timedelta
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parents[2]
for extra in (ROOT, ROOT / "phase0", Path(__file__).parent):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

import gen_testset as gen  # noqa: E402
import synthetic_session as ss  # noqa: E402

from athena.bundle import read_bundle  # noqa: E402
from athena.capture import session, staging  # noqa: E402
from athena.capture.config import CaptureConfig  # noqa: E402
from pilot import truth_name  # noqa: E402

TITLES = {"mimeo_newsletter": "News letter no. 14", "typed_minutes": "Minutes, regular meeting",
          "news_clipping": "Clipping, bulbs on the green", "program_page": "Year book 1958-1959",
          "handwritten_note": "Note about the phlox"}
SECONDS_PER_FRAME = {"camera-a": 18, "camera-b": 31}


def page_at(name: str, ppi: int):
    layout, rgb, material = gen.render_historical(name)
    size = (round(8.5 * ppi), round(11 * ppi))
    return layout, cv2.resize(rgb, size, interpolation=cv2.INTER_AREA), material


def make_pilot(root: Path, methods=("camera-a", "flatbed"), ppi: int = 200, names=None, blur: dict | None = None) -> dict:
    """Capture every historical page with every method. Camera methods photograph it (with a separator sheet and
    the scale card); ``flatbed`` scans it. ``blur`` maps a method to a blur for its frames."""
    root = Path(root)
    names = list(names or gen.HISTORICAL)
    bundles_dir, truth_dir = root / "bundles", root / "truth"
    truth_dir.mkdir(parents=True, exist_ok=True)
    out = {"bundles": bundles_dir, "truth": truth_dir, "pages": {}, "titles": list(TITLES.values())}
    for method in methods:
        folder = root / "sessions" / method
        when, n, order = ss.START, 1, []
        for i, name in enumerate(names, start=1):
            layout, rgb, material = page_at(name, ppi)
            if method == "flatbed":
                from PIL import Image
                path = folder / f"{i:02d}-{name}" / "scan.png"         # numbered: folders are read in name order
                path.parent.mkdir(parents=True, exist_ok=True)
                Image.fromarray(rgb).save(path, dpi=(ppi, ppi))
            else:
                gap = timedelta(seconds=SECONDS_PER_FRAME.get(method, 20))
                ss.save_photo(folder / f"IMG_{n:04d}.jpg",
                              ss.photograph(ss.separator_page(ppi), ppi=ppi, seed=n, tag_mm=None), when)
                frame = ss.photograph(rgb, ppi=ppi, seed=n, blur=(blur or {}).get(method, 0.0))
                ss.save_photo(folder / f"IMG_{n + 1:04d}.jpg", frame, when + gap)
                when, n = when + 2 * gap, n + 2
            order.append((name, layout, material))
        cfg = CaptureConfig(station=method, staging=bundles_dir, collection="archive", quick=False,
                            min_ppi=100, min_ppi_photo=100)
        result = session.import_session(folder, cfg, session.Options(order="name"))
        assert len(result.items) == len(order)
        for item, (name, layout, material) in zip(result.items, order):
            staging.redescribe(bundles_dir / item.bundle, collection="archive", series="unsorted",
                               item={"title": TITLES[name], "physical": {"format": material}, "tags": ["comparison"]})
            page = f"{item.bundle}/1"
            text = "\n".join(layout.lines) + "\n"
            (truth_dir / truth_name(page)).write_text(text, "utf-8")
            (truth_dir / truth_name(page).replace(".txt", ".keys.txt")).write_text("\n".join(layout.fields) + "\n", "utf-8")
            out["pages"][page] = {"name": name, "material": material, "method": method, "text": text,
                                  "keys": list(layout.fields), "kind": read_bundle(bundles_dir / item.bundle).pages[0].kind}
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("out")
    ap.add_argument("--ppi", type=int, default=200)
    a = ap.parse_args()
    made = make_pilot(Path(a.out), ppi=a.ppi)
    print(f"{len(made['pages'])} pages in {made['bundles']}; transcriptions in {made['truth']}", file=sys.stderr)
    print(f"python -m pilot.run {made['bundles']} --out {a.out}/results\n"
          f"python -m pilot.score {a.out}/results --truth {made['truth']}\n"
          f"python -m pilot.report {a.out}/results --total-pages 2500")
