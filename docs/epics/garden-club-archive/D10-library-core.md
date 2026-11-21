# D10 · Library MVP: named instances, intake, originals, PostgreSQL schema, search, events

| | |
|---|---|
| **ID** | D10 |
| **Type** | code |
| **Owner role** | ops agent |
| **Size** | 5 days |
| **Depends on** | D05 (bundle spec). D22 (compose) later, when containers resume. Uses D09's `ocr.jobs` when it lands; until then its own `ocr.page` fallback. |
| **Week** | W2–W3 (schema on day 1 so D11, D13 and D15 can start against it) |
| **Branch** | `feat/library-core` (from `feat/capture-bundle`) |

> **Native first** ([ADR 0001, amendment 1](../../adr/0001-containers-and-postgres.md#amendment-1-native-first-on-apple-silicon-containers-as-the-destination)): the library runs natively (PostgreSQL from Homebrew
> or the distribution, the library under launchd or systemd) until containers resume. Everything below about compose
> projects applies then; until then a named instance is its own database, data directory, config file and NATS
> account, set by environment. Tests start a throwaway PostgreSQL with `initdb`.

## Why

The library is where the archive lives: write-once originals, the arrangement (collection → series → item →
page), OCR text with its provenance, full-text search, and the visibility fields the club's choices go into. It
must be a **named instance** so the same code runs the archivist's private library here and a household's, a
guest library or a business's library elsewhere. Per [ADR 0001](../../adr/0001-containers-and-postgres.md) it runs
as a container with **PostgreSQL** as its database and its files on a mounted volume.

D10 can start on day 1 against the stock `postgres` image if D22's image isn't ready; nothing here needs
`pgvector` or AGE yet.

## Inputs

- D05 bundles in the intake volume (a bind mount on the Ubuntu mini that capture stations can reach).
- D22: `library.yml`, the `athena` and `athena-postgres` images, `ATHENA_TEST_DATABASE_URL` in CI.
- design.md's store and schema; portal.md's tiers and hierarchy; backend.md's provenance rules (myvect
  `ingest.py` hash-dedupe and `watcher.py` patterns, derived with headers).
- Sieve's client (`ocr.page`) and D09's `ocr.jobs` contract.

## Outputs

- `athena/library/` and `athena library …` commands: `migrate`, `serve`, `intake`, `ocr submit|ingest|run`,
  `search`, `outbox`.
- **Named instance** = one compose project (`athena-<name>`, later one namespace) with its own database, `files`
  volume, `intake` volume, `library.toml` and NATS account. Configuration comes only from the environment
  (`ATHENA_LIBRARY`, `ATHENA_DATABASE_URL`, `ATHENA_FILES`, `ATHENA_INTAKE`, `ATHENA_NATS_URL`,
  `ATHENA_NATS_CREDS`) and the mounted `library.toml`; nothing defaults to a household or club name.
- **File store** behind a small interface (`put`, `open`, `exists`, `path_for`), filesystem implementation on the
  `files` volume: `originals/<sha256[:2]>/<sha256>.<ext>`, write-once (temp file, fsync, rename, `0444`); the code
  has no delete or overwrite path. An S3-compatible implementation can come later for multi-node clusters.
- **Schema** (PostgreSQL, plain numbered SQL migrations applied by `athena library migrate`, psycopg 3):
  - `assertion` — **append-only provenance**: `id`, `subject` (record type and id), `predicate` (e.g.
    `item.title`, `item.date`, `page.text`, `visibility`), `value` (`jsonb`), `source` (`capture:<station>`,
    `ocr:<engine>@<version>`, `person:<login>`, `policy:<name>`), `asserted_at`, `supersedes`. A trigger refuses
    `UPDATE` and `DELETE`. Every change below is written as an assertion **in the same transaction** as the
    current-state row it changes.
  - Current state: `collection`, `series` (slug, title, parent, description, sort order; seeded from
    `library.toml`); `item` (bundle id, series, title, `date_edtf`, `date_range daterange` plus earliest/latest,
    type, creator, physical `jsonb`, tags `text[]`, notes, status `new`/`ocr_done`/`reviewed`, `extra jsonb` for
    deployment-specific fields); `page` (item, n, side, kind, original and page sha256, width, height, ppi,
    quality `jsonb`); `text` (page, engine, engine_version, tiles, markdown, plain, looped, hit_cap) — every
    engine run kept, a view picks the current one; `capture` (bundle provenance and manifest `jsonb`).
  - **Visibility from day one** (portal.md build order 1): `visibility` (`private`/`members`/`public`, default
    `private`, null on children to mean "inherit") on collection, series, item and page; `redaction` `jsonb` on
    page; `policy`. D11 owns their logic. The audit log is the `assertion` table.
  - **Search**: a generated `tsvector` (English config) over item titles and current text with GIN indexes;
    `pg_trgm` for fuzzy title matches. (Vectors and the entity graph come later; the assertion model already
    expresses "this item mentions that person", so they add tables, not a redesign.)
  - `outbox` (event, payload, created_at, published_at): written in the same transaction as the change.
- **Intake**: watch the intake volume (polling is fine), validate each bundle (D05), copy raw and page files into
  the file store, insert rows and assertions, make the OCR derivative (~200 ppi JPEG q90), submit OCR; a bad bundle
  moves to `intake/rejected/` with a reason file.
- **OCR ingest**: subscribe to `ocr.jobs.done.<library>`; store text with `ocr:` provenance; record
  `ocr.jobs.dead.<library>` in an `ocr_failure` table for the review UI.
- **Fallback until D09 lands**: `athena library ocr run --via page` sends each page without text through
  `ocr.page` with Sieve's client, one at a time, resumable. It stays as a tool for small re-runs.
- **Outbox publisher**: a loop in the `library` service that publishes unsent outbox rows to `library.events`
  (`library.<name>.item.created|updated|reviewed`, `…visibility.changed`, `…ocr.done`, ids only, never content)
  and marks them sent; at-least-once, with the outbox id as the message id so JetStream de-duplicates.
- `athena library search "<query>"` (for tests and the review UI).

## Steps

1. Day 1: `library.toml` format and migration 001 (schema), published on the branch for D11, D13 and D15.
2. File store and intake with validation and rejection.
3. OCR derivative, submit, ingest, failures; the `ocr.page` fallback.
4. Search; outbox and its publisher.
5. Storage estimate from D06 recorded in the README (expect ~50–150 GB of masters for the shoebox).
6. **Deploy the club's instance** with D22's wrapper (`athena-compose garden up`) on the Ubuntu mini, files and
   database volumes on the encrypted disk, `library.toml` seeded with D01's draft series list and a type vocabulary
   (kept in the instance's config directory, not in git), connected to the site's NATS with the library's creds.

## Acceptance criteria

- Two named instances (two compose projects) run side by side with no shared database, files, ports or subjects.
- Ingesting the fixture bundles gives the expected rows and assertions; every original's sha256 matches its file
  name; originals are read-only; re-ingesting a bundle is a no-op.
- A corrupt bundle (sha256 mismatch) is rejected with a reason, and nothing from it is stored (one transaction).
- `UPDATE` or `DELETE` on `assertion` fails; current state can be rebuilt from assertions (a test drops and
  rebuilds `item` and compares).
- OCR results from `FakeEngine` via a throwaway JetStream server land in `text` with `ocr:` provenance; a
  dead-lettered job shows in `ocr_failure`.
- Search finds a word from a fixture page's OCR text and from an item title.
- Every new row's visibility is `private` (or inherit → private), whatever the bundle says.
- Events are published once per change (outbox id as message id) and carry ids only (a test inspects payloads for
  any text field); killing the publisher mid-batch loses nothing.
- No code path deletes or rewrites a file under `originals/` (a test greps the package for `unlink`/`rmtree`/
  `open(…, "w")` against the originals path, plus a behavioural test).

## Test plan

pytest against PostgreSQL from `ATHENA_TEST_DATABASE_URL` (the compose `db` locally, a service container in CI; a
fresh schema per test), fixture bundles from D05, the JetStream fixture from D09 (or a fake bus); migration test
from an empty database; a 2,500-page synthetic load test checking intake and search stay fast (< 10 minutes intake
on the Ubuntu mini, search < 100 ms). Tests needing a database skip with a reason when none is configured.

## Privacy and rights

- [ ] Volumes on the encrypted disk; the compose README says how to check.
- [ ] Database password and NATS creds as compose secrets, never in env files committed to git.
- [ ] No content in logs or events.
- [ ] Private by default at every level, enforced by schema defaults and a test.
- [ ] Derived code from myvect carries SPDX and provenance headers; nothing from myvect's Azure/oauth2-proxy parts.

## Touches

`athena/library/` (new: `config.py`, `store.py`, `db/` with `migrations/*.sql`, `intake.py`, `ocr.py`,
`outbox.py`, `search.py`, `cli.py`), `athena/__main__.py`, `pyproject.toml` (`library` extra: psycopg[binary],
nats-py, fastapi, uvicorn, jinja2, pillow, pillow-heif), `deploy/compose/library.yml` (the `library` service's
command and environment), `tests/test_library*.py`.

## Done means

The club's named library runs on the Ubuntu mini as a compose project: it ingests capture bundles into write-once
originals and PostgreSQL with provenance for every assertion, gets OCR text from Sieve, searches it, publishes
events through an outbox, and holds everything private until someone chooses otherwise.
