# SPDX-License-Identifier: GPL-3.0-or-later
# Derived from the author's OCRX ocrx.py (an AprilTag 36h11 beside the page as the scale, found with sub-pixel corners).
"""The two printed sheets a capture session uses, and finding them again in a photograph.

**Separator sheet**: a large QR code (``athena.bundle.SEPARATOR_PAYLOAD``) that means "a new item starts here", with a
box below it for a handwritten item slip. The box sits at a fixed place relative to the code, so the importer can
cut it out, upright, from a photograph taken at any angle.

**Scale card**: an AprilTag of a known printed size. Laid flat beside a page, it gives the page's resolution in
pixels per inch whatever the camera's distance.

Both are drawn with Pillow's built-in font and OpenCV's own encoders: no network, no system fonts.
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from athena.bundle import SEPARATOR_PAYLOAD, is_separator_payload

MM_PER_INCH = 25.4
PAPER_MM = {"letter": (215.9, 279.4), "a4": (210.0, 297.0)}

QR_MM = 90.0                             # side of the code itself, without its white margin
QR_TOP_MM = 48.0                         # from the top of the sheet
# The slip box in millimetres, measured from the code's top-left corner (x to the right, y down).
SLIP_BOX_MM = (-40.0, 108.0, 130.0, 198.0)
SLIP_LINES = ("Title", "Date", "Kept in")
RULER_MM = 100.0

TAG_ID = 0
DEFAULT_TAG_MM = 50.0                    # side of the tag's black square


def _tag_dictionary():
    return cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_APRILTAG_36h11)


def _font(px: float):
    return ImageFont.load_default(size=max(8, int(px)))


class _Sheet:
    """A white page addressed in millimetres."""

    def __init__(self, paper: str, ppi: int):
        if paper not in PAPER_MM:
            raise ValueError(f"paper must be one of {sorted(PAPER_MM)}: {paper!r}")
        self.w_mm, self.h_mm = PAPER_MM[paper]
        self.k = ppi / MM_PER_INCH
        self.img = Image.new("L", (round(self.w_mm * self.k), round(self.h_mm * self.k)), 255)
        self.d = ImageDraw.Draw(self.img)

    def px(self, mm: float) -> int:
        return round(mm * self.k)

    def text(self, x_mm, y_mm, s, size_mm, anchor="la", fill=0):
        self.d.text((self.px(x_mm), self.px(y_mm)), s, font=_font(size_mm * self.k), fill=fill, anchor=anchor)

    def paste(self, tile: np.ndarray, x_mm, y_mm, side_mm):
        side = self.px(side_mm)
        big = cv2.resize(tile, (side, side), interpolation=cv2.INTER_NEAREST)
        self.img.paste(Image.fromarray(big), (self.px(x_mm), self.px(y_mm)))

    def ruler(self, x_mm, y_mm):
        d, px = self.d, self.px
        d.line([px(x_mm), px(y_mm), px(x_mm + RULER_MM), px(y_mm)], fill=0, width=max(2, px(0.35)))
        for i in range(0, int(RULER_MM) + 1, 10):
            d.line([px(x_mm + i), px(y_mm - 3), px(x_mm + i), px(y_mm)], fill=0, width=max(2, px(0.35)))
        self.text(x_mm + RULER_MM + 4, y_mm, f"{RULER_MM:.0f} mm when printed at 100%", 3.2, anchor="lm")


def _qr_modules(payload: str) -> np.ndarray:
    """The code as an image of one pixel per module, without OpenCV's white border."""
    code = cv2.QRCodeEncoder.create().encode(payload)
    ys, xs = np.nonzero(code == 0)
    return code[ys.min():ys.max() + 1, xs.min():xs.max() + 1]


def separator_sheet(paper: str = "letter", ppi: int = 300) -> Image.Image:
    s = _Sheet(paper, ppi)
    cx = s.w_mm / 2
    qx = cx - QR_MM / 2
    s.text(cx, 14, "NEW ITEM", 13, anchor="ma")
    s.text(cx, 33, "Photograph this sheet before the first page of each item.", 4.2, anchor="ma")
    s.paste(_qr_modules(SEPARATOR_PAYLOAD), qx, QR_TOP_MM, QR_MM)
    x0, y0, x1, y1 = (qx + SLIP_BOX_MM[0], QR_TOP_MM + SLIP_BOX_MM[1], qx + SLIP_BOX_MM[2], QR_TOP_MM + SLIP_BOX_MM[3])
    s.d.rectangle([s.px(x0), s.px(y0), s.px(x1), s.px(y1)], outline=0, width=max(2, s.px(0.5)))
    s.text(x0, y0 - 6.5, "Item slip: write here, or lay a note in the box.", 4.2)
    step = (y1 - y0) / len(SLIP_LINES)
    for i, label in enumerate(SLIP_LINES):
        y = y0 + step * (i + 1) - 6
        s.text(x0 + 4, y, label, 3.6, anchor="ls", fill=90)
        s.d.line([s.px(x0 + 24), s.px(y), s.px(x1 - 5), s.px(y)], fill=150, width=max(1, s.px(0.2)))
    s.ruler(x0, y1 + 16)
    return s.img


def scale_sheet(paper: str = "letter", ppi: int = 300, tag_mm: float = DEFAULT_TAG_MM) -> Image.Image:
    """A sheet of scale cards to cut out. Each is the tag with a white margin and its size printed beneath."""
    s = _Sheet(paper, ppi)
    s.text(s.w_mm / 2, 12, "SCALE CARDS", 9, anchor="ma")
    s.text(s.w_mm / 2, 25, "Print at 100% (no “fit to page”). Cut one out and lay it flat beside every page.", 3.8,
           anchor="ma")
    cells = 8                                # the tag: 6 x 6 data cells inside a one-cell black border
    tile = cv2.aruco.generateImageMarker(_tag_dictionary(), TAG_ID, cells * 8, borderBits=1)
    card = tag_mm + 24
    cols = max(1, int((s.w_mm - 20) // card))
    rows = max(1, int((s.h_mm - 75) // (card + 8)))
    left = (s.w_mm - cols * card) / 2
    for r in range(rows):
        for c in range(cols):
            x, y = left + c * card, 36 + r * (card + 8)
            s.d.rectangle([s.px(x), s.px(y), s.px(x + card), s.px(y + card + 6)], outline=170, width=max(1, s.px(0.2)))
            s.paste(tile, x + 12, y + 10, tag_mm)
            s.text(x + card / 2, y + tag_mm + 16, f"black square: {tag_mm:g} mm", 3.4, anchor="ma")
    s.ruler(left, s.h_mm - 16)
    return s.img


def write_pdf(path: str | Path, paper: str = "letter", tag_mm: float = DEFAULT_TAG_MM, ppi: int = 300) -> Path:
    """One PDF: the separator sheet, then a sheet of scale cards. Black and white, lossless."""
    path = Path(path)
    pages = [separator_sheet(paper, ppi), scale_sheet(paper, ppi, tag_mm)]
    pages = [p.point(lambda v: 255 if v > 200 else 0).convert("1") for p in pages]
    pages[0].save(path, "PDF", resolution=float(ppi), save_all=True, append_images=pages[1:])
    return path


# --- finding them in a photograph -------------------------------------------------------------------------------

def _shrink(gray: np.ndarray, long_edge: int) -> tuple[np.ndarray, float]:
    scale = long_edge / max(gray.shape[:2])
    if scale >= 1:
        return gray, 1.0
    size = (round(gray.shape[1] * scale), round(gray.shape[0] * scale))
    return cv2.resize(gray, size, interpolation=cv2.INTER_AREA), scale


def find_separator(gray: np.ndarray) -> np.ndarray | None:
    """The four corners of the separator sheet's code (top-left, top-right, bottom-right, bottom-left, in the code's
    own orientation) in ``gray``'s pixels, or None when the image isn't a separator sheet."""
    detector = cv2.QRCodeDetector()
    for long_edge in (1400, 2400, 800):      # the code is large; one of these sizes suits any camera
        small, scale = _shrink(gray, long_edge)
        try:
            text, points, _ = detector.detectAndDecode(small)
        except cv2.error:
            continue
        if points is not None and is_separator_payload(text):
            return points.reshape(4, 2).astype(np.float64) / scale
        if scale == 1.0:
            break
    return None


def slip_from_separator(rgb: np.ndarray, corners: np.ndarray, px_per_mm: float = 8.0) -> np.ndarray:
    """The slip box cut out of a photographed separator sheet, upright, at ``px_per_mm``."""
    x0, y0, x1, y1 = SLIP_BOX_MM
    code = np.float32([[0, 0], [QR_MM, 0], [QR_MM, QR_MM], [0, QR_MM]])
    to_image = cv2.getPerspectiveTransform(code, corners.astype(np.float32))
    box = np.float32([[x0, y0], [x1, y0], [x1, y1], [x0, y1]]).reshape(-1, 1, 2)
    src = cv2.perspectiveTransform(box, to_image).reshape(4, 2)
    w, h = round((x1 - x0) * px_per_mm), round((y1 - y0) * px_per_mm)
    dst = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    return cv2.warpPerspective(rgb, cv2.getPerspectiveTransform(src, dst), (w, h), flags=cv2.INTER_AREA,
                               borderMode=cv2.BORDER_REPLICATE)


def find_tag(gray: np.ndarray) -> np.ndarray | None:
    """The four corners of the scale card's tag in ``gray``'s pixels, or None when there's no tag in the frame."""
    small, scale = _shrink(gray, 2400)
    params = cv2.aruco.DetectorParameters()
    params.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
    corners, ids, _ = cv2.aruco.ArucoDetector(_tag_dictionary(), params).detectMarkers(small)
    if ids is None or len(corners) == 0:
        return None
    # Any tag of the family will do, so a card printed for OCRX works too; the largest is the one beside the page.
    best = max(corners, key=lambda c: cv2.contourArea(c.reshape(4, 2).astype(np.float32)))
    return best.reshape(4, 2).astype(np.float64) / scale
