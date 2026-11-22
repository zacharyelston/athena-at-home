# SPDX-License-Identifier: GPL-3.0-or-later
# Derived from the author's OCRX scan.py and ocrx.py (the page outline from a convex hull, the AprilTag scale, and
# the glare, evenness, brightness and Laplacian measures with the targets tuned there on real cameras).
"""One captured frame becomes one page image: find the paper, square it up, and measure how good the capture is.

Everything is measured on a reduced copy (``WORK_EDGE`` pixels on its long side), so checking a session is quick and
the numbers mean the same thing for a 12 MP and a 48 MP camera. Only the final crop is done at full resolution.

The page finder takes the rim of the frame to be the table and looks for the largest thing that isn't: so the paper
should lie on one plain surface that shows all round it (a dark, matte cloth or board is best). When it can't find
four clean edges (a flatbed scan, a patterned table, a page running off the frame) it leaves the frame uncropped and
says so; nothing is ever lost, because the raw file is kept as captured.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import cv2
import numpy as np

from .sheets import MM_PER_INCH, find_tag

WORK_EDGE = 1600           # long edge of the copy everything is measured on
GROW = 0.006               # the crop is this much larger than the paper, so its edges stay in the picture
INSET = 0.03               # measurements ignore this much at each edge (the table, shadows, a curled corner)


@dataclass
class Found:
    """What the reduced copy shows. Coordinates are in full-resolution pixels."""
    quad: np.ndarray | None          # the paper's corners: top-left, top-right, bottom-right, bottom-left
    cropped: bool
    cut_off: bool                    # the paper touches the edge of the frame
    skew_deg: float
    keystone: float                  # 0 when the camera is square to the page; 0.1 = one side 10% longer
    tag: np.ndarray | None           # the scale card's tag, if it's in the frame
    size: tuple[int, int] = (0, 0)   # width and height of the page image the crop will produce


@dataclass
class Measured:
    kind: str                        # text | photo | mixed | blank (a guess)
    sharpness: float
    sharpness_min: float             # the softest ninth of the page that has anything on it
    soft_where: str                  # "", or e.g. "lower right"
    glare: float                     # fraction of the page blown out to white
    evenness: float                  # 1 = evenly lit; 0.75 = the darker parts are a quarter dimmer
    marks: float                     # fraction of the page with anything on it
    brightness: float = 0.0          # level of the bare paper, 0-255
    laplacian_var: float = 0.0
    extra: dict = field(default_factory=dict)


def _order(pts: np.ndarray) -> np.ndarray:
    s, d = pts.sum(axis=1), pts[:, 1] - pts[:, 0]
    return np.array([pts[s.argmin()], pts[d.argmin()], pts[s.argmax()], pts[d.argmax()]], dtype=np.float64)


def _side(a, b) -> float:
    return float(np.hypot(*(a - b)))


def reduce(rgb: np.ndarray, edge: int = WORK_EDGE) -> tuple[np.ndarray, float]:
    """A copy with its long edge at most ``edge``, and the factor it was shrunk by (≤ 1)."""
    scale = edge / max(rgb.shape[:2])
    if scale >= 1:
        return rgb, 1.0
    size = (max(1, round(rgb.shape[1] * scale)), max(1, round(rgb.shape[0] * scale)))
    return cv2.resize(rgb, size, interpolation=cv2.INTER_AREA), scale


def _not_table(blur: np.ndarray) -> np.ndarray:
    """Everything that doesn't look like the rim of the frame (the table), so a dark print on a dark cloth is found
    as readily as white paper."""
    h, w = blur.shape[:2]
    t = max(4, min(h, w) // 50)
    wide = blur.astype(np.int16)
    rim = np.concatenate([wide[:t].reshape(-1, 3), wide[-t:].reshape(-1, 3), wide[:, :t].reshape(-1, 3),
                          wide[:, -t:].reshape(-1, 3)])
    mask = (np.abs(wide - np.median(rim, axis=0)).max(axis=2) > 28).astype(np.uint8) * 255
    return cv2.morphologyEx(cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8)), cv2.MORPH_CLOSE,
                            np.ones((9, 9), np.uint8))


def _bright(blur: np.ndarray) -> np.ndarray:
    """OCRX's way: bright paper on a darker desk, whatever the desk's grain or pattern."""
    gray = cv2.cvtColor(blur, cv2.COLOR_RGB2GRAY)
    _, otsu = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return cv2.morphologyEx(otsu, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))


def find_paper(small: np.ndarray) -> tuple[np.ndarray | None, bool]:
    """The paper's four corners in the reduced copy (or None), and whether a page-sized thing runs off the frame.

    A flatbed scan, where the page fills the frame, rightly finds nothing."""
    h, w = small.shape[:2]
    blur = cv2.GaussianBlur(small, (5, 5), 0)
    cut_off = False
    for mask in (_not_table(blur), _bright(blur)):
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for c in sorted(contours, key=cv2.contourArea, reverse=True)[:5]:
            area = cv2.contourArea(c)
            if not 0.12 * w * h <= area <= 0.985 * w * h:
                continue
            x, y, bw, bh = cv2.boundingRect(c)
            if x <= 1 or y <= 1 or x + bw >= w - 1 or y + bh >= h - 1:
                cut_off = True
                continue
            # The hull, not the outline: a torn corner, an offset sheet beneath or a thumb on the edge notches the
            # outline, but the hull of a page is still a clean four-sided shape.
            hull = cv2.convexHull(c)
            hull_area = max(cv2.contourArea(hull), 1.0)
            if area / hull_area < 0.8:
                continue
            for eps in (0.02, 0.03, 0.05):
                approx = cv2.approxPolyDP(hull, eps * cv2.arcLength(hull, True), True)
                if len(approx) == 4:
                    quad = _order(approx.reshape(4, 2).astype(np.float64))
                    if hull_area / max(cv2.contourArea(quad.astype(np.float32)), 1.0) >= 0.93:
                        return quad, False
        if cut_off:
            break
    return None, cut_off


def look(rgb: np.ndarray) -> tuple[Found, np.ndarray]:
    """Find the paper and the scale card. Returns what was found and the squared-up page at reduced size."""
    small, scale = reduce(rgb)
    quad, cut_off = find_paper(small)
    tag = find_tag(cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY))
    H, W = rgb.shape[:2]
    if quad is None or cut_off:
        return Found(None, False, cut_off, 0.0, 0.0, tag, (W, H)), small
    quad = quad / scale
    centre = quad.mean(axis=0)
    quad = centre + (quad - centre) * (1 + GROW)
    quad[:, 0] = quad[:, 0].clip(0, W - 1)
    quad[:, 1] = quad[:, 1].clip(0, H - 1)
    tl, tr, br, bl = quad
    top, bottom, left, right = _side(tl, tr), _side(bl, br), _side(tl, bl), _side(tr, br)
    size = (max(1, round((top + bottom) / 2)), max(1, round((left + right) / 2)))
    skew = math.degrees(math.atan2(tr[1] - tl[1], tr[0] - tl[0]))
    keystone = max(top, bottom) / max(1.0, min(top, bottom)) - 1, max(left, right) / max(1.0, min(left, right)) - 1
    found = Found(quad, True, False, round(skew, 2), round(max(keystone), 4), tag, size)
    _true_shape(found)
    return found, square_up(small, found, scale)


def _true_shape(found: Found) -> None:
    """Squaring up a page seen at an angle gets its outline right but not its proportions. The scale card is a
    known square in the same plane, so stretch the page image until the card is square again."""
    if found.tag is None:
        return
    t = cv2.perspectiveTransform(found.tag.astype(np.float32).reshape(-1, 1, 2), _transform(found)[0]).reshape(4, 2)
    e1, e2 = ((t[1] - t[0]) + (t[2] - t[3])) / 2, ((t[3] - t[0]) + (t[2] - t[1])) / 2
    # Find sx with |(sx·e1x, e1y)| = |(sx·e2x, e2y)| and the two at right angles (least squares over both).
    a1, b1 = e1[0] ** 2 - e2[0] ** 2, e2[1] ** 2 - e1[1] ** 2
    a2, b2 = e1[0] * e2[0], -e1[1] * e2[1]
    if a1 * a1 + a2 * a2 < 1e-6:
        return
    s2 = (a1 * b1 + a2 * b2) / (a1 * a1 + a2 * a2)
    if not 0.75 <= s2 <= 1.33:               # more than about 15% out: don't trust it
        return
    sx = math.sqrt(s2)
    w, h = found.size
    found.size = (round(w * sx), h) if sx >= 1 else (w, round(h / sx))


def _transform(found: Found, scale: float = 1.0) -> tuple[np.ndarray, tuple[int, int]]:
    w, h = max(1, round(found.size[0] * scale)), max(1, round(found.size[1] * scale))
    dst = np.float32([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]])
    return cv2.getPerspectiveTransform((found.quad * scale).astype(np.float32), dst), (w, h)


def square_up(rgb: np.ndarray, found: Found, scale: float = 1.0) -> np.ndarray:
    """The page cut out of ``rgb`` and made rectangular. ``scale`` is ``rgb``'s size relative to full resolution."""
    if not found.cropped:
        return rgb
    m, size = _transform(found, scale)
    return cv2.warpPerspective(rgb, m, size, flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)


def measured_ppi(found: Found, tag_mm: float) -> float | None:
    """Pixels per inch of the page image, from the scale card. The card lies in the paper's plane, so squaring up
    the page squares up the card by the same amount, even though the card itself is cropped away."""
    if found.tag is None or tag_mm <= 0:
        return None
    tag = found.tag.astype(np.float32).reshape(-1, 1, 2)
    if found.cropped:
        tag = cv2.perspectiveTransform(tag, _transform(found)[0])
    t = tag.reshape(4, 2)
    side = sum(_side(t[i], t[(i + 1) % 4]) for i in range(4)) / 4
    return round(side / (tag_mm / MM_PER_INCH), 1)


# --- measurements -----------------------------------------------------------------------------------------------

_NINTHS = (("upper left", "top", "upper right"), ("left", "centre", "right"), ("lower left", "bottom", "lower right"))


def _sharpness(gray: np.ndarray) -> float:
    """How abrupt the strongest edges are, 0 to about 1: the top 0.5% of second differences against the tonal range.
    Unlike a plain variance, it doesn't rise just because a page has more text on it."""
    lo, hi = np.percentile(gray, (2, 98))
    contrast = float(hi - lo)
    if contrast < 12:
        return 0.0
    lap = np.abs(cv2.Laplacian(gray, cv2.CV_32F, ksize=3))
    return round(float(np.percentile(lap, 99.5)) / (2 * contrast), 3)


def measure(small_page: np.ndarray) -> Measured:
    """Measure a squared-up page at reduced size (an RGB array)."""
    h, w = small_page.shape[:2]
    dy, dx = int(h * INSET), int(w * INSET)
    inner = small_page[dy:h - dy, dx:w - dx]
    gray = cv2.cvtColor(inner, cv2.COLOR_RGB2GRAY)
    gh, gw = gray.shape

    # Anything on the page at all: local detail, which lighting can't fake.
    detail = cv2.absdiff(gray, cv2.GaussianBlur(gray, (0, 0), 6))
    marks = float((detail > 14).mean())

    # The level of the bare paper in each cell of an 8 x 8 grid (its 90th percentile, so a block of text doesn't read
    # as a shadow). Judging tone against the paper nearby keeps a badly lit page of text from looking like a picture.
    levels = np.array([[np.percentile(gray[r * gh // 8:(r + 1) * gh // 8, c * gw // 8:(c + 1) * gw // 8], 90)
                        for c in range(8)] for r in range(8)], dtype=np.float32)
    nearby = cv2.resize(levels, (gw, gh), interpolation=cv2.INTER_LINEAR)
    paper = float(np.percentile(gray, 95))
    paper_fraction = float((gray >= 0.78 * nearby).mean())
    if marks < 0.002 and paper_fraction > 0.9:
        kind = "blank"
    elif paper_fraction < 0.55:
        kind = "photo"
    elif paper_fraction < 0.8:
        kind = "mixed"
    else:
        kind = "text"

    glare = float((inner.min(axis=2) >= 250).mean())

    soft = []
    for r in range(3):
        for c in range(3):
            rows, cols = slice(r * gh // 3, (r + 1) * gh // 3), slice(c * gw // 3, (c + 1) * gw // 3)
            if float((detail[rows, cols] > 14).mean()) >= 0.01:
                soft.append((_sharpness(gray[rows, cols]), _NINTHS[r][c]))
    overall = _sharpness(gray) if kind != "blank" else 0.0
    weakest = min(soft) if soft else (overall, "")

    # Evenness, after OCRX: the spread between the darker and the brighter parts of the page.
    lo, hi = np.percentile(levels, (10, 90))
    evenness = max(0.0, 1 - (hi - lo) / max(hi, 1.0)) if kind != "photo" else 1.0

    # OCRX's sharpness (noise-suppressed variance of the Laplacian, page at 1200 pixels), recorded so a pilot on real
    # pages can compare it with the measure above. It rises with the amount of text, so it isn't judged here.
    ref, _ = reduce(gray, 1200)
    laplacian_var = float(cv2.Laplacian(cv2.GaussianBlur(ref, (3, 3), 0), cv2.CV_64F).var())
    return Measured(kind=kind, sharpness=overall, sharpness_min=round(weakest[0], 3), soft_where=weakest[1],
                    glare=round(glare, 4), evenness=round(evenness, 3), marks=round(marks, 4),
                    brightness=round(paper, 1), laplacian_var=round(laplacian_var, 1),
                    extra={"paper_fraction": round(paper_fraction, 3)})
