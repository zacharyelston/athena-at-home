# The publisher: feeds

Status: **D15**, 2026-11-21. Code: [`athena/publish/`](../athena/publish). Design:
[portal.md](portal.md#feeds-the-boundary); who sees what is [visibility.md](visibility.md).

A site or a receiving library never reads the upstream library. The publisher builds a **feed**: a separate
directory holding only what one audience may see, with redactions already applied. If something private were ever
to leak, it would leak here, so the publisher **fails closed** and ships with a checker that proves what a build
contains.

```bash
athena publish build public --out /srv/feeds/public      # replaced as a whole on every build
athena publish check public /srv/feeds/public            # exit 1 on any problem
athena publish diff /srv/feeds/public.last /srv/feeds/public   # removed / changed / added paths
```

Feeds are defined in `library.toml` by the tiers they carry (`[[feed]]`, see visibility.md). Publishing settings:

```toml
[publish]
thumb = 300                 # long-edge caps in pixels
web = 1600
zoom = 3200
max_fraction = 0.9          # no feed image exceeds this fraction of the page image: never a full-size copy
jpeg_quality = 85
redaction_margin = 0.004    # rectangles grow by this fraction of the long edge before burn-in
fields = ["title", "date", "type", "creator", "tags", "language"]   # may add "physical", "notes"
rights_default = "Rights not evaluated."

[[series]]
slug = "newsletters"
collection = "archive"
rights = "Shared by the society for personal and research use."     # shown with this series' items
```

Notes and physical location stay in the library unless a deployment adds them to `fields`.

## What a feed holds

```
feed/
  feed.json            feed, tiers, library, built_at, decided_as_of, builder, counts, the series it carries,
                       the public-domain cutoff year used (when a rights policy is on)
  items/<id>.json      the allowed description fields, rights statement, series, page list
  pages/<id>.json      n, side, kind, images, text and text_status
  images/<id>/         thumb.jpg, web.jpg, and zoom.jpg when the page is large enough to have one
  log.jsonl            per item: opaque id, tier, page count
```

- **Ids are opaque**: a hash of the library name, feed name and record id. Library ids (which sort by capture
  time) never appear, so a feed doesn't reveal how much else exists or in what order. They are stable across
  builds, so links keep working.
- **An item is in the feed** when its effective tier (settings, then policies, as of the build date) is one the feed
  carries. **A page is in** when its own tier is too; a page narrower than its item is left out.
- **`text_status`**: `checked` (a person's text), `machine-ocr`, `quick-ocr` (Apple Vision only), `image-only`
  (photos, blank pages, no text), or `withheld` (below).
- **Zoom is three image sizes per page** (about 3 files per page, which keeps a site within hosts' per-deployment
  file limits). Deep Zoom tiles are not built yet.

## Redaction

Stored on the page as a list, never applied to originals:

```json
[{"type": "region", "x": 0.03, "y": 0.39, "w": 0.94, "h": 0.06},
 {"type": "text", "text_id": 41, "start": 22, "end": 47},
 {"type": "text_checked", "text_id": 41}]
```

- **Regions** are fractions of the page image. They're filled solid black on the full-resolution pixels, grown
  by a small margin, **before** any resizing, so every size is redacted and resampling can't pull hidden pixels back.
- **Text spans** name the text row they were made against; the span is replaced by `[redacted]`.
- **Text is withheld** (`"text": null`) when a redaction can't be trusted against the page's current text:
  the page has a region but nobody has dealt with the text (add spans, or a `text_checked` entry meaning "I read
  it; nothing more to hide"), or the text has changed since the spans were made (a new OCR run, a correction).
  The image is still published, redacted.
- A page whose redaction list is invalid is left out of every feed.

`athena.library.redaction.set_page_redaction` validates, records who and why as an assertion, and emits an event.
The review UI (D14) is where the archivist draws them.

## Images

Every feed image is re-encoded from bare pixels: orientation applied, colours converted to sRGB, then no EXIF (so
no GPS, camera or date), no XMP or IPTC, no embedded profile, no thumbnails. Originals are never copied, and no
image reaches the page image's size.

Rendered images are cached under `files/derivatives/publish/`, keyed by page content, redactions and settings, so
a rebuild only renders what changed. A 48 MP page takes about 0.6 s on a 16 GB M3 (`--jobs` runs several at
once); measure on the deployment's machine.

## Rebuilds, takedowns and the share log

A build writes a fresh directory and swaps it in whole, so nothing unshared can linger. It refuses to replace a
directory that isn't a feed. To take something down: narrow its visibility, rebuild, and use `publish diff`
against the previous build to purge the removed paths from the host's cache (D19).

Each build records, as assertions with source `publisher`, every item that **entered** (`feed.shared`, with the
tier and the reasons) or **left** (`feed.unshared`) the feed since the last build, and emits a `feed.built` event.

## The checks

`athena publish check` (and `athena.publish.leakcheck`, which the site and deploy steps reuse):

| Check | Catches |
|---|---|
| against the library | an item, page or image the feed must not carry; unexpected files |
| originals | any file that is a byte-for-byte copy of an original; any image as large as its page image |
| redaction | a pixel inside a redaction rectangle that isn't black, in any size; a redacted text span still present |
| metadata | EXIF, XMP, IPTC or a colour profile in any image |
| `--forbid-file` strings | each string in paths, file contents, **gzip-compressed** content (a search index compresses its fragments) and JSON with escaped characters |

The tests build a canary library (items at every tier, a policy-capped item, a page narrower than its item,
redactions, GPS in a page image), assert each marker is where it should be and nowhere else, read the redacted
page with Apple Vision to confirm the hidden line is gone (macOS), and plant each kind of leak to prove the checker
catches it.
