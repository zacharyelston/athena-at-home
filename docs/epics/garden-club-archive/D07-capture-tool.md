# D07 · Capture tool: import, item grouping, metadata entry, quality checks

| | |
|---|---|
| **ID** | D07 |
| **Type** | code |
| **Owner role** | ops agent |
| **Size** | 3 days |
| **Depends on** | D05 (bundle spec); D06's decision shapes defaults but doesn't block the start |
| **Week** | W2 |
| **Branch** | `feat/capture-tool` (from `feat/static-site`, the top of the stack: it reads the library's config) |

> **Built** on `feat/capture-tool` (2026-11-21); usage and settings are in `docs/capture.md` on that branch.
> Commands: `athena capture sheets | check | import | staged | describe | send | discard`. As built:
> - `import` writes bundles into a **staging directory**; `describe` completes them there; `send` moves them to the
>   library's intake (complete or not at all). This keeps the intake watcher from taking a bundle mid-description.
> - The item slip is written in a box on the separator sheet itself; the importer cuts the box out, upright.
> - Resolution comes from an **AprilTag scale card** beside each page (within 1% on fixtures), which also restores
>   the page's proportions; flatbed scans use their own metadata.
> - Page outline, tag scale and the glare, evenness and brightness measures are adapted from the author's OCRX
>   (marked as derived), with its tuned targets as defaults.
> - Dates are typed as written on the slip ("about 1958", "1960s", "April 1962") and stored as EDTF by
>   `athena.bundle.edtf.from_text`, which D13 reuses. Ambiguous numeric dates (4/5/1962) are refused.
> - The describe form listens on 127.0.0.1 only, checks its own token and origin, runs no script.
> - Measured on synthetic 49 MP frames (16 GB M3): check 0.4 s a frame, import 0.7 s a frame with four workers.
>
> **Not done:** nothing here has seen a real capture. The sharpness and resolution thresholds, the page finder on
> the real table, and reading a real 48 MP iPhone HEIC all wait for the pilot (D06). DNG/ProRAW files are skipped
> with a message. Pages photographed sideways need `--rotate`. Tier 0 on Linux is left to Sieve, as planned.

## Why

Capture sessions produce folders of images from an iPhone, a flatbed or the PIXY. Something has to split them into
items, record the item's description and the page facts, check quality while the paper is still on the table, and
write valid bundles for the library. The simplest thing that works for every camera is an **importer**, not a
camera app.

## Inputs

- D05: bundle spec and `athena/bundle/`.
- D06: chosen methods, ppi targets, OCR policy per material (defaults in config).
- Session folders: iPhone photos (AirDrop or Image Capture, HEIC or JPEG at 48 MP), flatbed TIFFs, PIXY JPEGs.

## Outputs

- `athena/capture/` and `athena capture …` commands:
  - `separator` prints a **separator sheet** PDF (a large QR code meaning "next item", plus a box for a
    handwritten item slip).
  - `import <session-folder>` splits images into items at separator sheets (or one sub-folder per item), orders
    pages by capture time or filename, and writes bundles to the library's intake share.
  - `describe` runs a small local web form (localhost only) to enter or confirm each item's series, title, date
    (EDTF, with a helper: "circa", "decade", "between"), type and physical location, showing the page thumbnails
    and the slip photo. Everything except series may be left blank for the review UI (D13).
  - `check <session-folder>` runs quality checks and prints a **re-shoot list** before the session ends.
- Page processing: crop and deskew (OpenCV), measured ppi from the printed scale or an AprilTag (OCRX's approach)
  or EXIF/scanner metadata, sharpness (variance of Laplacian), glare (clipped highlights), evenness; `kind` guess
  (text/photo/blank) for versos and photos.
- Tier 0: Apple Vision text on macOS (via Sieve's `tier0` module); skipped on Linux (Sieve adds it later).

## Steps

1. Separator sheet generator and QR detection (OpenCV's QR detector; no network).
2. Import: walk the folder, read EXIF/TIFF tags (capture time, device, resolution), split at separators, write the
   raw file untouched to `raw/`, process into `pages/`, compute `quality.json`, write bundles atomically.
3. HEIC support via `pillow-heif` (check its licence: BSD-3; libheif LGPL, dynamically linked, compatible).
4. Quality thresholds in config, defaults from D06; `check` lists pages under threshold with the reason
   ("ppi 310 < 400", "glare 4% of page", "blurred").
5. `describe` web form: FastAPI + server-rendered HTML, bound to 127.0.0.1; writes back into the bundle manifest
   before intake (bundles are immutable once the library takes them; later edits are made in the library).
6. Idempotence: re-importing the same session folder doesn't duplicate bundles (sha256 of raw files).

## Acceptance criteria

- On a synthetic session folder (fixture pages, rendered "photographed" with perspective, plus separator sheets),
  `import` produces the expected number of bundles with the expected pages, all passing `athena bundle validate`.
- Raw files in the bundle are byte-identical to the source files.
- Measured ppi within ±5% on fixtures rendered at a known ppi with a scale in frame.
- `check` flags a deliberately blurred page and a page with a glare patch, and passes clean ones.
- Re-import is a no-op.
- `describe` saves EDTF dates and rejects invalid ones with a readable message.
- Runs on macOS and Linux (tier 0 only on macOS; tests for it carry the `macos` marker).

## Test plan

Fixture generator for "session folders" (perspective warp, blur, glare, separator pages, a scale strip); pytest for
split, order, ppi, quality flags, idempotence; FastAPI `TestClient` tests for `describe`.

## Privacy and rights

- [ ] Strips nothing from `raw/` (masters stay as captured), but **flags** GPS EXIF so D15 strips it from every
      derivative; the bundle records `has_gps: true`.
- [ ] The `describe` server listens on localhost only and has no external assets (no CDN).
- [ ] No cloud OCR; tier 0 is Apple Vision on the machine.

## Touches

`athena/capture/`, `athena/__main__.py`, `pyproject.toml` (`capture` extra: opencv-python-headless,
pillow-heif, fastapi, uvicorn, jinja2), `tests/test_capture*.py`, `tests/fixtures/`.

## Done means

A session folder from any of the three cameras becomes valid bundles in the library's intake share, with a
re-shoot list printed before the session ends, and the archivist can describe items from their slips.
