# SPDX-License-Identifier: GPL-3.0-or-later
"""Reading the image files a camera, a phone or a scanner leaves in a session folder: the pixels, and the few facts
in their metadata the importer needs (when, with what, at what resolution, and whether a location is recorded)."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

READABLE = {"jpg", "jpeg", "png", "tif", "tiff", "heic", "heif"}
NOT_YET = {"dng": "DNG (raw) files aren't read yet; export a HEIC or TIFF copy"}

_EXIF_IFD, _GPS_IFD = 0x8769, 0x8825
_MAKE, _MODEL, _DATETIME = 0x010F, 0x0110, 0x0132
_ORIGINAL, _DIGITIZED, _SUBSEC, _EXPOSURE, _FNUMBER = 0x9003, 0x9004, 0x9291, 0x829A, 0x829D

Image.MAX_IMAGE_PIXELS = 250_000_000     # a 48 MP phone or a 600 ppi tabloid scan, not a decompression bomb


def heif_available() -> bool:
    """Registers the HEIC reader (pillow-heif) if it's installed."""
    try:
        import pillow_heif
    except ImportError:
        return False
    pillow_heif.register_heif_opener()
    return True


HEIF = heif_available()


class Unreadable(Exception):
    pass


@dataclass(frozen=True)
class Facts:
    taken: datetime | None       # the camera's own clock, no time zone
    make: str
    model: str
    has_gps: bool
    camera: bool                 # the metadata records an exposure: a camera or a phone made it
    dpi: float | None            # the resolution the file declares (a scanner writes a true one)
    icc: bytes | None


def extension(path: Path) -> str:
    return path.suffix.lower().lstrip(".")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _open(path: Path) -> Image.Image:
    if extension(path) in ("heic", "heif") and not HEIF:
        raise Unreadable("HEIC files need pillow-heif (pip install 'athena-at-home[capture]')")
    try:
        return Image.open(path)
    except Exception as e:
        raise Unreadable(f"can't be read as an image ({type(e).__name__})") from None


def _text(value) -> str:
    return str(value).strip().strip("\x00") if value else ""


def facts(path: Path) -> Facts:
    """Read the metadata only; the pixels aren't decoded."""
    with _open(path) as im:
        try:
            exif = im.getexif()
            sub = exif.get_ifd(_EXIF_IFD)
            gps = exif.get_ifd(_GPS_IFD)
        except Exception:
            exif, sub, gps = {}, {}, {}
        taken = None
        stamp = _text(sub.get(_ORIGINAL) or sub.get(_DIGITIZED) or exif.get(_DATETIME))
        if stamp:
            try:
                taken = datetime.strptime(stamp[:19], "%Y:%m:%d %H:%M:%S")
                frac = _text(sub.get(_SUBSEC))
                if frac.isdigit():
                    taken = taken.replace(microsecond=int(frac[:6].ljust(6, "0")))
            except ValueError:
                taken = None
        camera = bool(sub.get(_EXPOSURE) or sub.get(_FNUMBER))
        dpi = None
        declared = im.info.get("dpi")
        if declared and not camera:
            try:
                x, y = float(declared[0]), float(declared[1])
                if 100 <= x <= 4800 and abs(x - y) < 1:   # 72 and 96 are placeholders, not measurements
                    dpi = round(x, 1)
            except (TypeError, ValueError, IndexError):
                pass
        return Facts(taken, _text(exif.get(_MAKE)), _text(exif.get(_MODEL)), bool(gps), camera, dpi,
                     im.info.get("icc_profile"))


def load_rgb(path: Path) -> np.ndarray:
    """The image as an 8-bit RGB array, turned the way the camera says it was held."""
    try:
        with _open(path) as im:
            im = ImageOps.exif_transpose(im)
            return np.asarray(im.convert("RGB"))
    except Unreadable:
        raise
    except Exception as e:
        if extension(path) not in ("tif", "tiff"):
            detail = str(e).strip().splitlines()[0][:90] if str(e).strip() else "damaged or cut short?"
            raise Unreadable(f"can't be decoded ({type(e).__name__}: {detail})") from None
        first = e
    import cv2                                # 16-bit colour TIFFs, which Pillow can't convert
    data = cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_UNCHANGED)
    if data is None:
        raise Unreadable(f"can't be read as an image ({type(first).__name__})")
    if data.dtype == np.uint16:
        data = (data >> 8).astype(np.uint8)
    if data.ndim == 2:
        return cv2.cvtColor(data, cv2.COLOR_GRAY2RGB)
    return cv2.cvtColor(data[:, :, :3], cv2.COLOR_BGR2RGB)


def save_jpeg(path: Path, rgb: np.ndarray, quality: int = 95, icc: bytes | None = None, max_edge: int = 0) -> None:
    """Write ``rgb`` as a JPEG with no metadata except the colour profile (so colours stay right)."""
    im = Image.fromarray(rgb)
    if max_edge and max(im.size) > max_edge:
        im.thumbnail((max_edge, max_edge), Image.LANCZOS)
    kw = {"icc_profile": icc} if icc else {}
    im.save(path, "JPEG", quality=quality, **kw)
