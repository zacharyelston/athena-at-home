# D10 · Library MVP: named instances, intake, originals, schema, FTS5, events

| | |
|---|---|
| **ID** | D10 |
| **Type** | code |
| **Owner role** | ops agent |
| **Size** | 4 days |
| **Depends on** | D05 (bundle spec). Uses D09's `ocr.jobs` when it lands; until then its own `ocr.page` fallback. |
| **Week** | W2–W3 (schema on day 1 so D11, D13 and D15 can start against it) |
| **Branch** | `feat/library-core` (from `feat/capture-bundle`) |

## Why

The library is where the archive lives: write-once originals, the arrangement (collection → series → item →
page), OCR text with its provenance, full-text search, and the visibility fields the club's choices go into. It
must be a **named instance** so the same code runs the archivist's private library here and a household's, a
guest library or a car library elsewhere.

## Inputs

- D05 bundles in an intake directory (a share on the Ubuntu mini).
- design.md's store and schema; portal.md's tiers and hierarchy; backend.md's provenance rules (myvect
  `ingest.py` hash-dedupe and `watcher.py` patterns, derived with headers).
- Sieve's client (`ocr.page`) and D09's `ocr.jobs` contract.

## Outputs

- `athena/library/` and `athena library …` commands: `init <name>`, `serve`, `intake`, `ocr submit|ingest`,
  `search`, `events`.
- **Named instance**: `athena library init garden --data /srv/athena/garden` creates
  `<data>/library.toml`, `originals/`, `derivatives/`, `library.sqlite`, `intake/`, `logs/`. Every command takes
  `--library <name>` or `--data <dir>`; nothing defaults to a household or club name. Two instances run side by
  side on one machine on different ports and subjects.
- **Originals**: `originals/<sha256[:2]>/<sha256>.<ext>`, write-once (written to a temp file, fsynced, renamed,
  then `chmod 0444`); the code has no delete or overwrite path.
- **Schema** (SQLite, WAL, migrations from v1):
  - `collection`, `series` (slug, title, parent collection, description, sort order) — seeded from
    `library.toml`.
  - `item` (id, bundle id, series, title, date_edtf, date_earliest, date_latest, type, creator, physical
    location, format, extent, notes, tags, status: `new`/`ocr_done`/`reviewed`, rights fields).
  - `page` (item, n, side, kind, original sha256, page sha256, width, height, ppi, quality json).
  - `text` (page, engine, engine_version, tiles, markdown, plain, looped, hit_cap, created_at) — every engine run
    kept; `item_text_current` view picks tier 1 over tier 0.
  - `capture` (bundle provenance: station, device, operator login, created_at, manifest json).
  - **Visibility columns from day one** (portal.md build order 1): `visibility` (`private`/`members`/`public`,
    default `private`, nullable on children to mean "inherit") on collection, series, item and page; `redaction`
    (regions and text spans) on page; `policy` and `audit_log` tables. D11 owns their logic.
  - `fts_text` (FTS5, porter tokenizer over plain text and item titles), kept in sync by triggers.
- **Intake**: watch `intake/` (polling is fine), validate each bundle, copy raw and page files into `originals/`,
  insert rows, make the OCR derivative (~200 ppi JPEG q90), submit OCR, emit events; a bad bundle moves to
  `intake/rejected/` with a reason file.
- **OCR ingest**: subscribe to `ocr.jobs.done.<library>`; store text; show `ocr.jobs.dead.<library>` entries in
  an `ocr_failure` table for the review UI.
- **Fallback until D09 lands**: `athena library ocr run --via page` sends each page without text through
  `ocr.page` with Sieve's client, one at a time, resumable (it skips pages that already have tier-1 text). It stays
  as a tool for small re-runs.
- **`library.events`** on NATS: `library.<name>.item.created|updated|reviewed`, `…visibility.changed`,
  `…ocr.done`, with ids only, never content.
- `athena library search "<query>"` over FTS5 (for tests and the review UI).

## Steps

1. Day 1: `library.toml` format, schema and migrations, published on the branch for D11, D13 and D15.
2. Originals store and intake with validation and rejection.
3. OCR derivative, submit, ingest, failures.
4. FTS5 and search; events.
5. Storage estimate from D06 recorded in the README (expect ~50–150 GB of masters for the shoebox).
6. **Deploy the club's instance** on the Ubuntu mini: `athena library init` on the encrypted disk, seeded with
   D01's draft series list and a type vocabulary in its `library.toml` (kept in the data directory, not in git),
   running under the templated systemd unit, connected to home NATS with the `library` creds.

## Acceptance criteria

- Two named instances initialized in temp dirs run side by side with no shared files, ports or subjects.
- Ingesting the fixture bundles gives the expected rows; every original's sha256 matches its file name; originals
  are read-only; re-ingesting a bundle is a no-op.
- A corrupt bundle (sha256 mismatch) is rejected with a reason, and nothing from it is stored.
- OCR results from `FakeEngine` via a throwaway JetStream server land in `text`; a dead-lettered job shows in
  `ocr_failure`.
- FTS5 finds a word from a fixture page's OCR text and an item title; doesn't find text from a deleted test row.
- Every new row's visibility is `private` (or inherit → private), whatever the bundle says.
- Events are emitted with ids only (test inspects payloads for any text field).
- No code path deletes or rewrites a file under `originals/` (a test greps the package for `unlink`/`rmtree`/
  `open(…, "w")` against the originals path, plus a behavioural test).

## Test plan

pytest with temp data dirs, fixture bundles from D05, the JetStream fixture from D09 (or a fake bus); migration
test from an empty DB; a 2,500-page synthetic load test (generated duplicates of fixture pages) checking intake and
FTS stay fast (< 10 minutes intake on the Ubuntu mini, search < 100 ms).

## Privacy and rights

- [ ] Data directory on the encrypted disk; `library.toml` says where, and `init` warns if the path isn't on an
      encrypted volume (Linux: checks for a dm-crypt parent; best effort).
- [ ] No content in logs or events.
- [ ] Private by default at every level, enforced by schema defaults and a test.
- [ ] Derived code from myvect carries SPDX and provenance headers; nothing from myvect's Azure/oauth2-proxy parts.

## Touches

`athena/library/` (new), `athena/__main__.py`, `pyproject.toml` (`library` extra: nats-py, fastapi, uvicorn,
jinja2, pillow, pillow-heif), `tests/test_library*.py`, `deploy/systemd/athena-library@.service` (templated by
instance name).

## Done means

The club's named library is running on the Ubuntu mini; it ingests capture bundles into write-once originals and SQLite, gets OCR text from
Sieve, searches it, emits events, and holds everything private until someone chooses otherwise.
