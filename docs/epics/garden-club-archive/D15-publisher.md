# D15 · Publisher: feeds, redaction burn-in, derivatives, zoom tiles

| | |
|---|---|
| **ID** | D15 |
| **Type** | code |
| **Owner role** | ops agent |
| **Size** | 3 days |
| **Depends on** | D10 (library), D11 (effective visibility, feed definitions) |
| **Week** | W3–W4 |
| **Branch** | `feat/publisher` (from `feat/visibility`) |

## Why

The feed is the boundary ([portal.md](../../portal.md#feeds-the-boundary)): a site never reads the library; the
publisher builds a separate, filtered, redacted copy of what one audience may see. If something private leaks, it
leaks here, so this deliverable carries the strictest tests.

## Inputs

- D10 library (items, pages, current text, originals), D11 (`effective`, `feeds_for`, feed definitions),
  D14's redactions (regions and text spans).
- D06's OCR policy (image-only pages carry no text).

## Outputs

- `athena/publish/` and `athena publish build <feed> --out <dir>`, run as the compose `publisher` service
  (`docker compose run --rm publisher build public`; a Job in a cluster), writing to the `site` volume: a **feed
  directory**:

  ```
  feed/
    feed.json            feed name, tiers, built_at, library name, cutoff year used, counts, builder version
    items/<item-id>.json title, date, series, type, description fields the feed allows, page list, rights statement
    pages/<page-id>.json OCR text (redacted) or "image only", text provenance ("uncorrected machine OCR" or "checked")
    images/<page-id>/    thumb.jpg (≤ 300 px), web.jpg (≤ 1600 px long edge), zoom.jpg (≤ 3200 px), or tiles/ (Deep Zoom)
    log.jsonl            what was included and why (ids and tiers only)
  ```

- **Zoom with few files by default**: three JPEG sizes per page that OpenSeadragon reads as a simple image
  pyramid (~7,500 image files for 2,500 pages). Deep Zoom tiles (`zoom = "dzi"` in config, via libvips `dzsave`)
  look better on large pages but produce ~50–150 files per page, which breaks hosts that cap files per deployment
  (D19). The site records which mode it used.
- Feed and item ids are opaque (hash of library name + item id + feed name), so ids don't reveal private item
  counts or order.
- **Redaction burned in before anything else**: the page image is rendered from the original, redaction rectangles
  are filled solid black on the full-resolution image, and only then resized and tiled; text spans are replaced by
  `[redacted]` before writing JSON.
- **All metadata stripped** from every derivative (EXIF, XMP, IPTC, ICC profile replaced by sRGB), which removes
  iPhone GPS and device data.
- **Originals never copied**; derivative pixel sizes capped so no feed image reaches master resolution.
- **Share log**: each build records as assertions (source `publisher`) what entered or left each feed since the last build
  (who, what, when, why: from the visibility changes).
- **Takedown**: `athena publish build` after an unshare drops the item; `athena publish diff <old> <new>` lists
  removed paths so D19's deploy can purge them from the host's cache.
- **Full rebuild** every time, with a content-hash cache of derivatives so unchanged pages aren't re-rendered
  (incremental builds from `library.events` are later).

## Steps

1. Feed selection from D11; opaque ids; `feed.json` and item/page JSON.
2. Image pipeline (Pillow for burn-in and the pyramid sizes; `pyvips` `dzsave` for the optional tiles); metadata
   stripping; size caps.
3. Text redaction and image-only handling.
4. Share log and `publish diff`.
5. **Leak test harness** (also used by D16 and D19): build every feed from a canary fixture library and scan every
   output byte.

## Acceptance criteria

The canary fixture library contains items at every tier, policy-capped items, a page narrower than its item,
redacted regions and spans, an image with GPS EXIF, each carrying unique canary strings in titles, text and
rendered into pixels.

- **Tier leaks**: no private canary appears anywhere in the members or public feed; no members canary in the
  public feed (byte scan of all files, plus OCR of every output image with Apple Vision on macOS or a
  pixel-difference check against the redaction mask on Linux).
- **Redaction**: every pixel inside a redaction rectangle is black in every output image: thumb, web, zoom, and
  every tile at every level in `dzi` mode; redacted text spans are absent from all JSON.
- **Metadata**: no output image has EXIF/XMP/GPS (checked with Pillow and `exiftool` if present).
- **Originals**: no output file is byte-identical to, or as large in pixels as, any original.
- Unsharing an item and rebuilding removes every file of it; `publish diff` lists them.
- A 2,500-page synthetic library builds (pyramid mode) in under 30 minutes on the Ubuntu mini (first build) and under 5 minutes
  with an unchanged cache.

## Test plan

pytest with the canary fixture library; the leak scanner as a reusable module (`athena/publish/leakcheck.py`) with
its own tests (it must detect a deliberately leaked canary in text, JSON, an image tile and a gzip file); a
performance test marked `slow`.

## Privacy and rights

- [ ] Fail closed: an item whose effective tier can't be computed (bad config, missing data) is left out and logged.
- [ ] The feed carries the rights statement the club chose per series (D18), e.g. "© the club, shared for personal
      and research use" or "No known copyright restrictions".
- [ ] No content in logs; `log.jsonl` has ids and tiers only.

## Touches

`athena/publish/` (new), `athena/__main__.py`, `pyproject.toml` (`publish` extra: pyvips, pillow, pillow-heif),
`tests/test_publish*.py`, `tests/fixtures/canary/`, `docs/publisher.md`.

## Done means

`athena publish build public` and `… members` produce feed directories that carry exactly what the club chose,
with redactions burned into every image and text, no metadata, no originals, and a leak checker that proves it.
