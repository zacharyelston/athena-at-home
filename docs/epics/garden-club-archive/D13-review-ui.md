# D13 · Review UI: metadata, OCR check, review status

| | |
|---|---|
| **ID** | D13 |
| **Type** | code |
| **Owner role** | ops agent |
| **Size** | 3 days |
| **Depends on** | D10 (library schema, search) |
| **Week** | W3 |
| **Branch** | `feat/review-ui` (from `feat/library-core`) |

## Why

The archivist describes and checks every item: title, date, series, type, and whether the OCR text is usable. It
has to be simple enough to use without the author in the room, on a laptop on the home network.

## Inputs

- D10's library (`athena library serve` in the compose `library` service hosts the UI), its search, OCR failures.
- D06's OCR policy per material (what "check OCR" means for each).
- D07's slip photos.

## Outputs

`athena/library/web/` served by `athena library serve` in the `library` container, published on the LAN address only:

- **Browse**: collection → series → items, with counts by status (`new`, `ocr_done`, `reviewed`) and a "next
  item to review" button.
- **Item page**: page thumbnails; a large page viewer with zoom; the OCR text beside it (tier 1, with tier 0 for
  comparison); the slip photo; the metadata form (title, EDTF date with the same helper as D07, series, type,
  creator, physical location, format, tags, notes); flags (`looped`, `hit_cap`, OCR failure).
- **OCR check**: per page, mark "OCR good", "OCR usable (uncorrected)", "image only", or edit the text (an edit is
  stored as a new `text` row with engine `human` and the editor's login; the original OCR row stays).
- **Mark reviewed**: an item becomes `reviewed` only when title, date (or "undated"), series and type are set and
  every page has an OCR check.
- **Search** over D10's full-text index (title and text) and trigram title matches, with filters by series,
  decade (from the date range), type, status.
- **Queues**: unreviewed items, OCR failures and dead letters, items in "unsorted".
- **Logins**: two local accounts (archivist and author) with hashed passwords in the library's data dir, session
  cookies, and an access log (who viewed or changed what). No public exposure: published on the LAN address only
  (D22's rule), documented as never to be port-forwarded or given a public Ingress.

## Steps

1. FastAPI + Jinja2 server-rendered pages; htmx vendored into the package (no CDN); OpenSeadragon vendored for
   zoom over D10's page derivatives.
2. Item page and metadata form with validation; every change written as a `person:<login>` assertion (D10's audit log).
3. OCR check and text edit; review status rules.
4. Search and queues.
5. Accessibility: large default text, high contrast, full keyboard use, no time-outs while typing; tested at
   200% zoom.
6. A one-page "how to review" help screen (feeds D20).

## Acceptance criteria

- On a fixture library: the archivist's path works end to end: open next item → fix the title and date → mark
  each page's OCR → mark reviewed → next item. An item can't be marked reviewed with a required field missing.
- Edited OCR text is stored as a new row; the original remains and is shown on request.
- Search finds fixture text; filters combine.
- Unauthenticated requests get the login page; all changes are audited with the login.
- No external network requests from any page (test asserts every asset URL is local).
- Lighthouse accessibility score ≥ 90 on the item page; keyboard-only review of one item works.

## Test plan

FastAPI `TestClient` tests for every route, form validation and auth; a Playwright smoke test (optional, local)
for the review path; manual session with the archivist on synthetic items before real ones (feeds D20).

## Privacy and rights

- [ ] LAN only, login required, no CDN or analytics, no content in logs or URLs (item ids only in URLs).
- [ ] Passwords hashed (argon2 or scrypt); session cookies `HttpOnly`, `SameSite=Strict`.
- [ ] The UI shows the effective visibility badge on every item (read-only here; D14 makes it editable).

## Touches

`athena/library/web/` (routes, templates, static), `athena/library/auth.py`, `tests/test_web*.py`,
`pyproject.toml` (argon2-cffi).

## Done means

The archivist can review an item from start to finish in the browser, unaided, and the library records who changed
what.
