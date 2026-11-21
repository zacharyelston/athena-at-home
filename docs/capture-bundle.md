# Capture bundle, version 1

Status: **v1**, 2026-11-21. Code: [`athena/bundle/`](../athena/bundle). Schema:
[`athena/bundle/capture-bundle-v1.schema.json`](../athena/bundle/capture-bundle-v1.schema.json).

A capture bundle is the one contract every capture source (the desk camera, an iPhone, a flatbed, a file import)
produces and every library consumes ([design.md](design.md#capture-bundle-the-one-contract-every-source-produces)).
Version 1 adds archival description, so an archive can say where each item belongs:
**collection → series → item → page**.

**One bundle is one item**: a newsletter issue, a yearbook, a clipping, a photo, the minutes of one meeting. A
capture session produces many bundles. A bundle is **immutable** once written; later corrections to its
description are made in the library, which keeps the bundle as it arrived.

## Layout

```
<bundle-id>/                 the id: a ULID (26 characters, sortable by time, no personal data)
  manifest.json              everything below, described and checksummed
  raw/001.<ext>              the master, byte for byte as captured: heic, heif, jpg, jpeg, png, tif, tiff, dng
  pages/001.jpg              cropped and deskewed at full resolution: jpg (q95) or png
  quick/001.json             Apple Vision tier-0 lines (optional; Sieve adds tier 0 later when a station can't)
  quality.json               per page: sharpness, glare, evenness, skew, measured ppi (optional)
  slip.jpg                   photo of a handwritten item slip, to help describe the item later (optional)
```

Page files are numbered from `001` and the number matches the page's `n`. Nothing else may be in the directory
(operating-system debris such as `.DS_Store`, `Thumbs.db` and `._*` files is ignored). Symbolic links are refused.

## manifest.json

```json
{
 "bundle_version": 1,
 "id": "01M7Z8XQJC1FN7PJXH7FM7ZW3E",
 "created_at": "2026-12-08T14:03:11-05:00",
 "station": {"name": "desk-iphone", "software": "athena-capture", "version": "0.1.0"},
 "source": "camera",
 "device": {"make": "Apple", "model": "iPhone"},
 "operator": "station-1",
 "describe": {
  "collection": "archive",
  "series": "newsletters",
  "item": {
   "title": "Newsletter, spring issue",
   "date": "1962-21",
   "type": "newsletter",
   "creator": "",
   "physical": {"container": "box 1, folder 3", "format": "mimeograph", "extent": "2 pages", "condition": ""},
   "language": "en",
   "tags": ["flower-show"],
   "notes": ""
  }
 },
 "slip": "slip.jpg",
 "quality": "quality.json",
 "pages": [
  {"n": 1, "side": "recto", "kind": "text", "raw": "raw/001.heic", "page": "pages/001.jpg", "quick": "quick/001.json",
   "width": 5200, "height": 6730, "ppi": 610, "ppi_source": "scale", "has_gps": true},
  {"n": 2, "side": "verso", "kind": "text", "raw": "raw/002.heic", "page": "pages/002.jpg",
   "width": 5200, "height": 6730, "ppi": 610, "ppi_source": "scale", "has_gps": true}
 ],
 "files": {
  "pages/001.jpg": {"sha256": "…", "bytes": 4810233},
  "pages/002.jpg": {"sha256": "…", "bytes": 4630012},
  "quality.json": {"sha256": "…", "bytes": 212},
  "quick/001.json": {"sha256": "…", "bytes": 1840},
  "raw/001.heic": {"sha256": "…", "bytes": 7310045},
  "raw/002.heic": {"sha256": "…", "bytes": 7129960},
  "slip.jpg": {"sha256": "…", "bytes": 301772}
 }
}
```

| Field | Required | Meaning |
|---|---|---|
| `bundle_version` | yes | `1` |
| `id` | yes | ULID; equals the directory name |
| `created_at` | yes | RFC 3339 with a UTC offset |
| `station` | yes | `name` (a slug), `software`, `version` |
| `source` | yes | `camera`, `flatbed` or `import` |
| `device` | no | camera or scanner `make` and `model` |
| `operator` | no | the station login (a slug), never a person's full name |
| `describe.collection`, `describe.series` | yes | slugs defined in the receiving library's config |
| `describe.item` | yes | the description below; every field optional, so capture can record only the series and leave the rest to review |
| `slip`, `quality` | no | the optional files |
| `pages[]` | yes, ≥ 1 | see below |
| `files` | yes | every file except `manifest.json`: `sha256` and `bytes`. Every file referenced must be listed, every listed file referenced, and every file on disk listed. |

### Item description

| Field | Meaning |
|---|---|
| `title` | as the archivist would label it |
| `date` | **EDTF** (below); leave it out for an undated item |
| `type` | a slug from the library's type vocabulary (e.g. `yearbook`, `program`, `newsletter`, `flyer`, `minutes`, `letter`, `directory`, `clipping`, `photograph`, `other`) |
| `creator` | who made it, if known |
| `physical` | `container` (where the paper lives), `format` (typeset, typewritten, mimeograph, photocopy, newsprint, photo print…), `extent`, `condition` |
| `language` | BCP 47 tag, e.g. `en` |
| `tags` | slugs |
| `notes` | free text |

### Pages

| Field | Meaning |
|---|---|
| `n` | 1, 2, 3… in order |
| `side` | `recto`, `verso` or `spread` |
| `kind` | `text`, `photo`, `handwriting`, `mixed` or `blank`; Sieve skips OCR or picks a tile budget from it, and the site labels image-only pages |
| `raw`, `page`, `quick` | the files (above) |
| `width`, `height` | of the page image, in pixels |
| `ppi`, `ppi_source` | measured resolution (or `null`) and how: `scale` (a ruler in frame), `apriltag`, `exif`, `scanner`, `declared`, `unknown` |
| `has_gps` | the raw file's metadata carries a location (iPhones add one); the publisher strips metadata from every derivative regardless |

## Dates: EDTF

Item dates use the Library of Congress [Extended Date/Time Format](https://www.loc.gov/standards/datetime/),
levels 0 and 1, because archive dates are often partial or uncertain:

| EDTF | Means |
|---|---|
| `1962`, `1962-04`, `1962-04-17` | a year, a month, a day |
| `196X`, `19XX` | a decade, a century |
| `1962-XX`, `1962-04-XX` | some month of 1962; some day of April 1962 |
| `1962-21` … `1962-24` | spring, summer, autumn, winter 1962 (Northern Hemisphere months; winter runs Dec–Feb) |
| `1962~`, `1962?`, `1962%` | circa; uncertain; both |
| `1960/1969` | between |
| `../1969`, `1960/..` | open: up to 1969; from 1960 on |
| `/1969`, `1960/` | an end that's unknown |

`athena.bundle.parse_date` returns the earliest and latest calendar dates an expression allows. Visibility policies
use the **latest** possible date for "newer than" and rights tests, so uncertainty never makes an item look older
than it may be.

## What a bundle never holds

- **Visibility.** Everything enters the library private; audiences are set there.
- **Rights decisions.** Set in the library, where the owner of the collection decides.
- **Personal data in names or paths.** Ids are ULIDs and files are numbered; personal data may appear only inside
  `describe` (and in the images themselves).

## Writing and validating

```python
from athena.bundle import PageInput, write_bundle, validate

path = write_bundle(intake_dir, station={...}, source="camera",
                    describe={"collection": "archive", "series": "newsletters", "item": {"title": "…"}},
                    pages=[PageInput(raw=..., page=..., width=..., height=..., side="recto")])
report = validate(path, series={"newsletters", "yearbooks"})   # report.ok, report.errors, report.warnings
```

`write_bundle` writes into `<id>.partial/`, fsyncs, validates, then renames to `<id>/`, so a reader never sees half
a bundle; it never overwrites an existing bundle. `validate` checks the schema, the directory name, the timestamp,
the EDTF date, page numbering, that the files on disk are exactly the files listed, and every size and sha256.
A series, collection or type the library doesn't know is a **warning**: the library files the item under
"unsorted".

```bash
athena bundle validate DIR... [--series a,b] [--collections a] [--types a,b] [--json]
```

Exit status 0 when every bundle is valid (warnings allowed), 1 otherwise.

## Separator sheets

During a capture session a printed **separator sheet** marks the start of each item, so a run of photos can be split
into bundles. It carries a QR code whose text is `ATHENA-ITEM-BREAK/1` (`athena.bundle.SEPARATOR_PAYLOAD`) and a box
for a handwritten item slip. The capture tool prints and detects them; the version suffix lets the sheet change
later without misreading old ones.

## Changing the format

A change that a v1 reader can't ignore bumps `bundle_version` and gets a new schema file; the library accepts
every version it knows. The schema refuses unknown keys, so additions are always deliberate.
