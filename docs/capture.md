# The capture tool

Status: **D07**, 2026-11-21. Code: [`athena/capture/`](../athena/capture). What it produces:
[capture bundles](capture-bundle.md).

A capture session leaves a folder of images: photographs from a phone or a camera, or scans from a flatbed. The
capture tool is an **importer**, not a camera app, so it works the same for all of them. It splits the folder into
items, squares up and measures every page, lists the pages worth shooting again while the paper is still on the
table, and writes one bundle per item.

```bash
athena capture sheets                      # a PDF to print: the separator sheet and a sheet of scale cards
athena capture check  ~/Captures/session-1 # before packing up: the re-shoot list (exit 1 if there's anything on it)
athena capture import ~/Captures/session-1 # one bundle per item, into staging
athena capture describe                    # a form on this computer: title, date, series… from each item's slip
athena capture send                        # staged bundles move to the library's intake directory
```

Also: `athena capture staged` lists what's waiting, and `athena capture discard ID` deletes a staged bundle so its
pages can be imported again.

Install with the `capture` extra (OpenCV, pillow-heif, FastAPI, Uvicorn, Jinja2). It runs on macOS and Linux. On a
Mac with the `sieve` extra, pages also get Apple Vision's text for instant search (tier 0); elsewhere Sieve adds it
later.

## The two printed sheets

**Separator sheet.** A large QR code that means "a new item starts here", with a box below it for the item's slip:
write the title, date and where the paper lives in the box, or lay a handwritten note on it. Photograph the sheet
before the first page of each item. The importer cuts the box out, upright, whichever way round the sheet was
photographed, and keeps it as the item's `slip.jpg` (re-encoded, so no location metadata).

**Scale card.** An AprilTag of a known size. Laid flat on the table beside every page, it gives the page's true
resolution in pixels per inch whatever the camera's distance, and lets the importer give the page its true
proportions when the camera wasn't square to it. The card is cropped out of the page image.

Print at 100% (no "fit to page") and measure the 100 mm ruler on the sheet. If it measures 97 mm, set
`print_scale = 0.97`. Any tag of the 36h11 family works, so a card already printed for OCRX can be used: set `tag_mm`
to the side of its black square.

## How a session folder is read

- Every folder that holds images is a run of frames, in **capture-time order**; by file name (`2` before `10`) when
  some files carry no time. `--order time|name` overrides.
- A **separator sheet** starts a new item. A folder with no separator sheets is **one item**, so "one sub-folder per
  item" works too, and the two can be mixed.
- A folder named after one of the library's **series** places everything beneath it in that series. Otherwise
  `--series`, then `[capture] series`, then `unsorted`.
- Hidden files, notes and videos are ignored. DNG files are reported as skipped (not read yet).
- A frame that can't be decoded stops its item from being imported, and the report says which file. An item with a
  page silently missing would be worse than no item.

Read: JPEG, PNG, TIFF (8- and 16-bit), HEIC/HEIF.

## What import writes

One bundle per item, in the **staging directory**, written whole or not at all:

| In the bundle | From |
|---|---|
| `raw/NNN.ext` | the source file, byte for byte (its metadata, location included, stays here and only here) |
| `pages/NNN.jpg` | the page cut out and squared up at full resolution, JPEG quality 95, with the source's colour profile and no other metadata |
| `quick/NNN.json` | Apple Vision's lines, on a Mac |
| `quality.json` | every measure below, the kind guess, and the page's re-shoot reasons |
| `slip.jpg` | the separator sheet's slip box |

Each page records its size, `ppi` and `ppi_source`, a guessed `kind` and `has_gps`. Resolution comes from the scale
card (`apriltag`), else from a scanner's own metadata (`scanner`), else from `--ppi` (`declared`), else it is unknown.
Page sides are all `recto` unless `--duplex` says pages alternate front and back; the describe form can change them.

**Importing twice adds nothing.** Staging keeps a ledger of the SHA-256 of every source file that became part of a
bundle, so running import again on the same folder, or on a folder that has grown, only adds new items. That still
holds after the bundles have been sent. If an item's pages changed after it was imported (a re-shot page), the
importer refuses that item and says which bundle to discard first.

### Finding the page

The importer takes the rim of the frame to be the table and looks for the largest four-sided thing that isn't, with
OCRX's bright-paper-on-a-darker-desk method as a second try. So lay the paper on **one plain surface that shows
all round it**; a dark, matte cloth is best. When it can't find four clean edges (a scan that fills the frame, a
patterned table) the whole frame is kept and the report notes it. A page that runs off the frame is put on the
re-shoot list. Nothing is ever lost either way: the raw file is kept as captured.

`--rotate 90|180|270` turns every frame first, for pages that lie sideways in the photographs.

## The re-shoot list

`check` and `import` make the same judgement; `import` also records it in `quality.json`. Everything is measured on
a copy reduced to 1,600 pixels on its long side, so the numbers mean the same for any camera.

| Reason | Measure | Default | Setting |
|---|---|---|---|
| blurred | how abrupt the strongest edges are, against the page's tonal range | ≥ 1.0 for text, ≥ 0.15 for photographs | `min_sharpness`, `min_sharpness_photo` |
| soft at one edge | the same, in the weakest ninth of the page that has anything on it | ≥ half of `min_sharpness` | |
| glare | fraction of the page blown out to white (cameras only: a scanner's white is not glare) | ≤ 1% | `max_glare` |
| uneven light | spread between the darker and brighter parts of the bare paper | ≥ 75% | `min_evenness` |
| too dark | level of the bare paper, 0–255 (cameras only) | ≥ 140 | `min_brightness` |
| low resolution | measured ppi | ≥ 400, ≥ 600 for photographs | `min_ppi`, `min_ppi_photo` |
| camera not square | one side of the page longer than its opposite | ≤ 8% | `max_keystone` |
| page runs off the frame | the paper touches the frame's edge | | |

The glare, evenness and brightness targets are the ones tuned on real cameras in OCRX. The sharpness and resolution
thresholds are starting points: **the pilot on real pages (D06) should set them.** `quality.json` also records OCRX's
own sharpness (variance of the Laplacian) beside the one judged here, so the pilot can compare them.

Sharpness is judged at roughly the scale OCR reads a page, so it catches a missed focus or a moved camera, not the
slight softness visible only at full magnification.

**Kind** is a guess from the page's tones: `blank` (nothing on it), `photo` (mostly continuous tone), `mixed`, or
`text`. It can't tell handwriting from print; the describe form can set it.

## Describing items

`athena capture describe` opens a form at `http://127.0.0.1:8090/`. For each staged item it shows the slip and the
pages, and takes the series, title, date, type, who made it, where it's kept, how it was made, extent, condition,
tags and notes, plus each page's side and kind. **Only the series is needed**; the rest can wait for the library's
review screen.

Dates are typed as they appear on the slip and stored as [EDTF](capture-bundle.md#dates-edtf):

| Typed | Stored |
|---|---|
| `April 1962`, `Apr. 17, 1962`, `17 April 1962` | `1962-04`, `1962-04-17` |
| `spring 1962`, `fall of 1958` | `1962-21`, `1958-23` |
| `about 1958`, `c. 1958`, `circa April 1962` | `1958~`, `1962-04~` |
| `1960s` | `196X` |
| `1960-1965`, `between 1960 and 1965` | `1960/1965` |
| `before 1970`, `after 1945`, `since 1960` | `../1969`, `1946/..`, `1960/..` |
| `1962?` | `1962?` |
| *(empty)*, `undated`, `n.d.` | no date |

A date that can't be read is refused with examples of how to write it. `4/5/1962` is refused too, because it could
be April 5 or 5 April; `4/17/1962` can only be read one way and is accepted. The reader is
`athena.bundle.edtf.from_text`, for the library's review screen to share.

The form shows private records, so it is closed on purpose:

- it listens on `127.0.0.1` only, and there is no option to change that;
- it answers only requests addressed to this computer, so a web page elsewhere can't reach it under a borrowed name;
- a save needs the token the form itself carries and must come from the form's own page;
- it loads nothing from anywhere else and runs no script;
- it writes no access log, and tells browsers to store nothing.

Saving rewrites the staged bundle's `describe` block (and page sides and kinds) and nothing else: the files and
their checksums stay as imported.

## Sending

`athena capture send` checks each staged bundle against its checksums and moves it into the library's intake
directory. A bundle appears there **complete or not at all**: on the same disk it is renamed into place; onto another
disk or a network share it is copied under a `.partial` name that intake ignores, checked again, then renamed. A
bundle the library already has is left in staging and reported.

From then on the bundle belongs to the library, and corrections are made there.

## Settings

All optional, in `[capture]` of the library's config file (or no file at all: `check` works with defaults).

```toml
[capture]
station = "desk-1"            # a slug for this station; never a person's name
operator = "station-1"        # optional: the station login
staging = "/srv/library/capture-staging"
collection = "archive"        # default: the library's only collection
series = "unsorted"           # for items no folder name or --series places
tag_mm = 50                   # side of the scale card's black square
print_scale = 1.0
min_ppi = 400                 # ... and the other thresholds in the table above
jpeg_quality = 95
quick = true                  # Apple Vision text on a Mac
```

| Environment | |
|---|---|
| `ATHENA_CONFIG` | the config file |
| `ATHENA_CAPTURE_STAGING` | the staging directory |
| `ATHENA_INTAKE` | the library's intake directory (else `[library] intake`) |

The form's series and type lists come from the same file's `[[series]]` and `[vocabulary]`. A station on another
machine needs only those sections and `[capture]`; it never needs the database.

## Measured

On a 16 GB M3 MacBook, synthetic frames of 49 megapixels (a Letter page at about 520 ppi):

| | |
|---|---|
| `check` | 0.4 s per frame with one worker |
| `import`, with Apple Vision | 0.7 s per frame with four workers (`--jobs 4`, the default on this machine) |
| memory | about 1.4 GB per worker |
| measured ppi | within 1% of the truth on the fixtures tried (the tests allow 5%) |

So a 300-page session checks in about two minutes and imports in about four.

## What came from OCRX

The author's OCRX is the live desk-camera scanner; this tool does the same job for folders of finished captures.
Adapted from it (and marked as derived in the source): the page outline from a convex hull with a tolerance ladder,
the bright-paper fallback, the AprilTag scale with sub-pixel corners, the glare, evenness and brightness measures
with their targets, and the Laplacian sharpness. Apple Vision is reached through Sieve's tier-0 module, itself
derived from OCRX. New here: the table-rim page finder (for dark prints), the proportion fix from the tag, separator
sheets and slips, and everything about items, bundles and staging.

## Tests

`tests/test_capture.py` and `tests/test_capture_web.py` (50 tests), on sessions rendered at test time by
`tests/fixtures/synthetic_session.py`: made-up pages "photographed" with tilt, perspective, uneven light, blur and
glare, with separator sheets and a scale card. The Apple Vision test carries the `macos` marker.

```bash
python tests/fixtures/synthetic_session.py /tmp/session --ppi 300    # a session folder to try by hand
```

## Not done yet

- **Real captures.** Every number above is from synthetic frames. The thresholds, the page finder and the kind
  guess all need the pilot's real pages (D06).
- **48 MP HEIC from an iPhone** hasn't been read here: only synthetic HEIC up to about 25 megapixels. Phones store large
  HEIC files as a grid of tiles, which the fixtures can't produce.
- **DNG / ProRAW** files are skipped with a message. Later: needs a raw developer, and HEIC or TIFF serve this
  launch.
- **Which way up.** A page photographed sideways needs `--rotate`; nothing detects it.
- **Reading the slip.** The form shows the slip; a person types what it says. Later: handwriting OCR isn't reliable
  enough to prefill a record.
- **Language** isn't on the form (it stays as imported, unset); the library's review screen sets it.
- **OCRX's own `scans/` folders** aren't understood as items (each holds a raw, a page and an enhanced copy).
  Later: a small adapter, if the desk camera is used for this archive.
- **An accessibility audit** of the form beyond its structure (labels, headings, keyboard order, contrast).
