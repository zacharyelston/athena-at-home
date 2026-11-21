# SPDX-License-Identifier: GPL-3.0-or-later
"""Feed images: redaction burned in first, then resized, with every trace of metadata gone.

Order matters. The page image is decoded to bare pixels, redaction rectangles are filled solid black at full
resolution (grown by a small margin, so resampling can't pull hidden pixels back across the edge), and only then
are the feed sizes made. The outputs are fresh JPEGs encoded from pixels alone: no EXIF (so no GPS, camera or
date), no XMP, no IPTC, no embedded colour profile (pixels are converted to sRGB first), no thumbnails.

No output is ever as large as the page image: every size is capped at ``max_fraction`` of its long edge.
"""
from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageCms, ImageDraw, ImageOps

SIZES = ("thumb", "web", "zoom")
Image.MAX_IMAGE_PIXELS = 250_000_000   # a 48 MP capture is ~50M pixels; refuse absurd inputs, not real ones


@dataclass(frozen=True)
class ImageSpec:
    thumb: int
    web: int
    zoom: int
    max_fraction: float
    jpeg_quality: int
    redaction_margin: float

    def key(self) -> str:
        return f"{self.thumb}-{self.web}-{self.zoom}-{self.max_fraction}-{self.jpeg_quality}-{self.redaction_margin}"


def _to_srgb_pixels(im: Image.Image) -> Image.Image:
    """Bare pixels in sRGB (or greyscale): orientation applied, colour profile converted then dropped."""
    im = ImageOps.exif_transpose(im)
    icc = im.info.get("icc_profile")
    if im.mode in ("L", "1"):
        return im.convert("L")
    if im.mode in ("LA", "RGBA", "P"):
        im = im.convert("RGBA")
        flat = Image.new("RGB", im.size, "white")
        flat.paste(im, mask=im.getchannel("A"))
        im = flat
    elif im.mode != "RGB":
        im = im.convert("RGB")
    if icc:
        try:
            src = ImageCms.ImageCmsProfile(io.BytesIO(icc))
            im = ImageCms.profileToProfile(im, src, ImageCms.createProfile("sRGB"), outputMode="RGB")
        except (ImageCms.PyCMSError, OSError, ValueError):
            pass            # an unreadable profile is dropped; the pixels are used as they are
    # A new image from raw pixels: nothing from the source's info, EXIF or ancillary chunks can follow.
    clean = Image.new(im.mode, im.size)
    clean.paste(im)
    return clean


def burn(im: Image.Image, regions, margin: float) -> Image.Image:
    """Fill each region (fractions of width/height) with black, grown by ``margin`` × the long edge."""
    if not regions:
        return im
    w, h = im.size
    pad = int(round(margin * max(w, h))) + 1
    draw = ImageDraw.Draw(im)
    for x, y, rw, rh in regions:
        box = (max(0, int(x * w) - pad), max(0, int(y * h) - pad),
               min(w, int(round((x + rw) * w)) + pad), min(h, int(round((y + rh) * h)) + pad))
        draw.rectangle(box, fill=0)
    return im


def target_sizes(size: tuple[int, int], spec: ImageSpec) -> dict[str, tuple[int, int]]:
    """Pixel sizes for each output. Never upscaled, never above max_fraction of the page image; ``zoom`` is left
    out when it wouldn't be larger than ``web``."""
    w, h = size
    long_edge = max(w, h)
    ceiling = max(1, int(long_edge * spec.max_fraction))
    out = {}
    for name in SIZES:
        edge = min(getattr(spec, name), ceiling)
        scale = edge / long_edge
        out[name] = (max(1, round(w * scale)), max(1, round(h * scale)))
    if out["zoom"] == out["web"]:
        del out["zoom"]
    return out


def render(src: str | Path, regions, spec: ImageSpec) -> dict[str, tuple[bytes, int, int]]:
    """The feed images for one page: {"thumb"|"web"|"zoom": (jpeg bytes, width, height)}."""
    with Image.open(src) as opened:
        im = _to_srgb_pixels(opened)
    im = burn(im, regions, spec.redaction_margin)
    out = {}
    # Largest first; each smaller size is made from the one before, which is already redacted. (Resizing a 48 MP
    # page three times costs about three times as much; reducing_gap lets Pillow pre-shrink in one cheap pass.)
    for name, (w, h) in sorted(target_sizes(im.size, spec).items(), key=lambda kv: -kv[1][0]):
        im = im.resize((w, h), Image.LANCZOS, reducing_gap=3.0)
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=spec.jpeg_quality, optimize=True, progressive=True)
        out[name] = (buf.getvalue(), w, h)
    return out


def render_to_dir(src: str, regions, spec: ImageSpec, dest: str) -> dict[str, list[int]]:
    """Render and write <dest>/<size>.jpg (for a worker process). Returns {size: [width, height]}."""
    d = Path(dest)
    d.mkdir(parents=True, exist_ok=True)
    sizes = {}
    for name, (data, w, h) in render(src, regions, spec).items():
        (d / f"{name}.jpg").write_bytes(data)
        sizes[name] = [w, h]
    return sizes
