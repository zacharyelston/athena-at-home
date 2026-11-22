# SPDX-License-Identifier: GPL-3.0-or-later
"""Synthetic capture sessions for tests: made-up pages "photographed" on a dark table, with separator sheets and a
scale card, the way a real session folder comes off a phone. No real records.

    python tests/fixtures/synthetic_session.py OUT_DIR [--ppi 150] [--items 4] [--layout separators|folders]

Sessions are generated at test time, never committed (their images carry camera EXIF, which the repository guard
refuses on purpose).
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

import cv2
import numpy as np
from PIL import ExifTags, Image, ImageDraw, ImageFont

from athena.capture import images  # noqa: F401  (registers the HEIC reader and writer when pillow-heif is there)
from athena.capture import sheets

TABLE = (46, 47, 50)
PAPER = 232
PAGE_IN = (8.5, 11.0)
START = datetime(2026, 12, 3, 10, 0, 0)

LINES = [
    "RIVERSIDE GARDEN SOCIETY", "Newsletter of the membership", "",
    "The spring flower show will be held on the town green.", "Entries are welcome in all twelve classes,",
    "from forced bulbs to miniature arrangements.", "", "PLANT SALE", "Volunteers are needed for the potting bench",
    "and the tea table. Please bring labelled divisions", "of perennials and any spare clay pots.", "",
    "CIVIC PLANTING", "Four hundred bulbs were set along the library walk", "and the beds at the station were replanted.",
    "", "MEETINGS", "Second Tuesday of each month at two o'clock.", "Guests of members are always welcome.", "",
    "TREASURER'S NOTE", "Dues remain two dollars for the year.", "The balance carried forward is in good order.",
]


def text_page(seed: int = 0, ppi: int = 150, lines: list[str] | None = None) -> np.ndarray:
    w, h = round(PAGE_IN[0] * ppi), round(PAGE_IN[1] * ppi)
    img = Image.new("RGB", (w, h), (PAPER, PAPER - 2, PAPER - 8))
    d = ImageDraw.Draw(img)
    f = ImageFont.load_default(size=int(0.15 * ppi))
    rng = np.random.default_rng(seed)
    lines = lines if lines is not None else [LINES[(i + seed * 3) % len(LINES)] for i in range(34)]
    y = int(0.9 * ppi)
    d.text((int(0.9 * ppi), y - int(0.35 * ppi)), f"No. {seed + 1}", font=f, fill=(30, 30, 34))
    for line in lines:
        d.text((int(0.9 * ppi) + int(rng.integers(0, 3)), y), line, font=f, fill=(28, 28, 32))
        y += int(0.26 * ppi)
    return np.array(img)


def photo_page(seed: int = 0, ppi: int = 150) -> np.ndarray:
    """A continuous-tone "print": soft shapes, no text."""
    w, h = round(PAGE_IN[0] * ppi), round(PAGE_IN[1] * ppi)
    rng = np.random.default_rng(100 + seed)
    low = rng.uniform(35, 205, (14, 11)).astype(np.float32)
    tone = cv2.resize(low, (w, h), interpolation=cv2.INTER_CUBIC)
    fine = cv2.resize(rng.uniform(-28, 28, (h // 6, w // 6)).astype(np.float32), (w, h), interpolation=cv2.INTER_LINEAR)
    tone = np.clip(tone + fine, 12, 228)
    for _ in range(9):                         # things with edges: a wall, a path, figures
        x, y = int(rng.uniform(0.05, 0.75) * w), int(rng.uniform(0.05, 0.75) * h)
        bw, bh = int(rng.uniform(0.06, 0.25) * w), int(rng.uniform(0.06, 0.25) * h)
        if rng.random() < 0.5:
            cv2.rectangle(tone, (x, y), (x + bw, y + bh), float(rng.uniform(20, 215)), -1)
        else:
            cv2.ellipse(tone, (x + bw // 2, y + bh // 2), (bw // 2, bh // 2), 0, 0, 360, float(rng.uniform(20, 215)), -1)
    border = int(0.25 * ppi)                   # the print's white border
    framed = np.full((h, w), 226, np.float32)
    framed[border:h - border, border:w - border] = tone[border:h - border, border:w - border]
    tone = framed
    return np.clip(np.dstack([tone * 1.02, tone * 0.97, tone * 0.86]), 0, 255).astype(np.uint8)


def blank_page(seed: int = 0, ppi: int = 150) -> np.ndarray:
    w, h = round(PAGE_IN[0] * ppi), round(PAGE_IN[1] * ppi)
    return np.full((h, w, 3), (PAPER, PAPER - 2, PAPER - 8), np.uint8)


def separator_page(ppi: int = 150, slip: str = "") -> np.ndarray:
    img = sheets.separator_sheet("letter", ppi).convert("RGB")
    if slip:                                   # "handwriting" in the slip box
        d = ImageDraw.Draw(img)
        k = ppi / sheets.MM_PER_INCH
        qx = sheets.PAPER_MM["letter"][0] / 2 - sheets.QR_MM / 2
        d.text(((qx + sheets.SLIP_BOX_MM[0] + 30) * k, (sheets.QR_TOP_MM + sheets.SLIP_BOX_MM[1] + 12) * k), slip,
               font=ImageFont.load_default(size=int(0.22 * ppi)), fill=(20, 30, 120))
    return np.array(img)


def photograph(page: np.ndarray, *, ppi: int = 150, seed: int = 0, tilt: float = 2.5, keystone: float = 0.04,
               blur: float = 0.0, glare: bool = False, tag_mm: float | None = sheets.DEFAULT_TAG_MM,
               zoom: float = 1.0, cut_off: bool = False, uneven: float = 0.08) -> np.ndarray:
    """``page`` (drawn at ``ppi``) lying on a dark table with the scale card beside it, as a camera would see it:
    slightly turned, slightly out of square, unevenly lit. ``zoom`` scales the whole scene (the camera's distance)."""
    rng = np.random.default_rng(1000 + seed)
    ph, pw = page.shape[:2]
    m = int(0.7 * ppi)
    side = int(3.2 * ppi) if tag_mm else m
    table = np.empty((ph + 2 * m, pw + m + side, 3), np.uint8)
    table[:] = TABLE
    table[m:m + ph, m:m + pw] = page
    if tag_mm:
        tag_px = round(tag_mm / sheets.MM_PER_INCH * ppi)
        pad = round(0.35 * ppi)
        card = np.full((tag_px + 2 * pad, tag_px + 2 * pad, 3), 240, np.uint8)
        tile = cv2.aruco.generateImageMarker(sheets._tag_dictionary(), sheets.TAG_ID, 64, borderBits=1)
        card[pad:pad + tag_px, pad:pad + tag_px] = cv2.cvtColor(
            cv2.resize(tile, (tag_px, tag_px), interpolation=cv2.INTER_NEAREST), cv2.COLOR_GRAY2RGB)
        y0, x0 = m + int(0.6 * ppi), m + pw + int(0.45 * ppi)
        table[y0:y0 + card.shape[0], x0:x0 + card.shape[1]] = card

    th, tw = table.shape[:2]
    fw, fh = round(tw * zoom), round(th * zoom)
    src = np.float32([[0, 0], [tw, 0], [tw, th], [0, th]])
    k = keystone * fw / 2
    dst = np.float32([[k, 0], [fw - k, 0], [fw, fh], [0, fh]])
    centre = np.float32([fw / 2, fh / 2])
    a = np.deg2rad(tilt)
    rot = np.float32([[np.cos(a), -np.sin(a)], [np.sin(a), np.cos(a)]])
    dst = (dst - centre) @ rot.T * 0.93 + centre
    if cut_off:
        dst = dst + np.float32([-0.2 * fw, 0])
    frame = cv2.warpPerspective(table, cv2.getPerspectiveTransform(src, dst), (fw, fh), flags=cv2.INTER_AREA,
                                borderMode=cv2.BORDER_CONSTANT, borderValue=TABLE).astype(np.float32)

    yy, xx = np.mgrid[0:fh, 0:fw].astype(np.float32)
    light = 1.03 - uneven * (((xx - fw * 0.45) / fw) ** 2 + ((yy - fh * 0.4) / fh) ** 2) * 2
    frame *= light[..., None]
    if glare:
        cx, cy = fw * 0.42, fh * 0.45
        spot = np.exp(-(((xx - cx) / (fw * 0.16)) ** 2 + ((yy - cy) / (fh * 0.11)) ** 2))
        frame += 190 * spot[..., None]
    if blur:
        frame = cv2.GaussianBlur(frame, (0, 0), blur)
    frame += rng.normal(0, 1.6, frame.shape).astype(np.float32)
    return np.clip(frame, 0, 255).astype(np.uint8)


def save_photo(path: Path, rgb: np.ndarray, when: datetime | None, gps: bool = False, camera: bool = True) -> Path:
    exif = Image.Exif()
    if camera:
        exif[ExifTags.Base.Make] = "Synthetic"
        exif[ExifTags.Base.Model] = "Renderer 1"
        ifd = exif.get_ifd(ExifTags.IFD.Exif)
        ifd[ExifTags.Base.ExposureTime] = 0.01
        if when:
            ifd[ExifTags.Base.DateTimeOriginal] = when.strftime("%Y:%m:%d %H:%M:%S")
    if gps:                                    # the made-up location is in the middle of the Atlantic
        g = exif.get_ifd(ExifTags.IFD.GPSInfo)
        g[ExifTags.GPS.GPSLatitudeRef], g[ExifTags.GPS.GPSLatitude] = "N", (30.0, 0.0, 0.0)
        g[ExifTags.GPS.GPSLongitudeRef], g[ExifTags.GPS.GPSLongitude] = "W", (40.0, 0.0, 0.0)
    path.parent.mkdir(parents=True, exist_ok=True)
    kw = {"quality": 92} if path.suffix.lower() in (".jpg", ".jpeg", ".heic") else {}
    Image.fromarray(rgb).save(path, exif=exif, **kw)
    return path


# One page of an item: (kind, options for ``photograph``).
DEFAULT_ITEMS = [
    {"slip": "Spring newsletter 1962", "pages": [("text", {}), ("text", {})]},
    {"slip": "Flower show program", "pages": [("text", {}), ("text", {"blur": 3.0}), ("blank", {})]},
    {"slip": "", "pages": [("photo", {})]},
    {"slip": "Clipping, town green", "pages": [("text", {"glare": True})]},
]


def make_session(root: Path, items: list[dict] | None = None, *, layout: str = "separators", ppi: int = 150,
                 ext: str = "jpg", gps: bool = False, tag_mm: float | None = sheets.DEFAULT_TAG_MM,
                 zoom: float = 1.0, start: datetime = START, first: int = 1) -> list[dict]:
    """Write a session folder and return what it holds: per item, the file names of its pages in order, and the
    separator's file name. ``layout="folders"`` writes one sub-folder per item and no separator sheets."""
    items = DEFAULT_ITEMS if items is None else items
    draw = {"text": text_page, "photo": photo_page, "blank": blank_page}
    root = Path(root)
    n, when, out = first, start, []
    for i, item in enumerate(items, start=1):
        folder = root / f"item-{i:02d}" if layout == "folders" else root
        entry = {"separator": None, "pages": [], "kinds": [k for k, _ in item["pages"]]}
        if layout == "separators":
            frame = photograph(separator_page(ppi, item.get("slip", "")), ppi=ppi, seed=n, tag_mm=None, zoom=zoom)
            entry["separator"] = save_photo(folder / f"IMG_{n:04d}.{ext}", frame, when, gps).name
            n, when = n + 1, when + timedelta(seconds=7)
        for kind, opts in item["pages"]:
            opts = {"tag_mm": tag_mm, "zoom": zoom, **opts}
            frame = photograph(draw[kind](seed=n, ppi=ppi), ppi=ppi, seed=n, **opts)
            entry["pages"].append(save_photo(folder / f"IMG_{n:04d}.{ext}", frame, when, gps).name)
            n, when = n + 1, when + timedelta(seconds=7)
        out.append(entry)
    return out


def flatbed_scan(path: Path, seed: int = 0, dpi: int = 300) -> Path:
    """A clean scan: the page fills the frame, the file says its resolution, and there's no camera EXIF."""
    page = text_page(seed, dpi)
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(page).save(path, dpi=(dpi, dpi))
    return path


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("out")
    ap.add_argument("--ppi", type=int, default=150)
    ap.add_argument("--items", type=int, default=len(DEFAULT_ITEMS))
    ap.add_argument("--layout", choices=("separators", "folders"), default="separators")
    a = ap.parse_args()
    spec = [DEFAULT_ITEMS[i % len(DEFAULT_ITEMS)] for i in range(a.items)]
    made = make_session(Path(a.out), spec, layout=a.layout, ppi=a.ppi)
    print(f"{len(made)} items, {sum(len(m['pages']) for m in made)} pages -> {a.out}", file=sys.stderr)
