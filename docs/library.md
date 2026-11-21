# The library

Status: **D10 core**, 2026-11-21. Code: [`athena/library/`](../athena/library). Design:
[ADR 0001](adr/0001-containers-and-postgres.md) (PostgreSQL per library; native first on Apple Silicon, containers
later) and [D10](epics/garden-club-archive/D10-library-core.md).

A library is a **named instance**: its own PostgreSQL database, data directory (write-once originals and
regenerable derivatives), intake directory, config file and NATS account. Several run side by side on one machine.

## Run one natively

```bash
brew install postgresql@17 nats-server          # macOS; on Linux, the distribution's postgresql package
createdb athena_garden
uv pip install --python .venv/bin/python -e ".[library,client]"
```

`library.toml` (the deployment's choices; keep it with the instance, not in git):

```toml
[library]
name = "garden"                                  # slug; also the NATS subject prefix: library.garden.*

[[collection]]
slug = "archive"
title = "The archive"

[[series]]
slug = "newsletters"
collection = "archive"
title = "Newsletters"

[vocabulary]
types = ["yearbook", "program", "newsletter", "flyer", "minutes", "letter", "directory", "clipping", "photograph"]

[ocr]
skip_kinds = ["photo", "blank"]                  # pages Sieve doesn't read
derivative_ppi = 200                             # OCR-sized copies; DeepSeek-OCR-2 discards detail above this
```

Everything else comes from the environment, so a launchd plist, a systemd unit or (later) a container sets it:

| Variable | |
|---|---|
| `ATHENA_CONFIG` | path to `library.toml` |
| `ATHENA_DATABASE_URL` | e.g. `postgresql:///athena_garden` |
| `ATHENA_FILES` | data directory (on an encrypted disk) |
| `ATHENA_INTAKE` | where capture stations drop bundles |
| `ATHENA_NATS_URL`, `ATHENA_NATS_CREDS`, `ATHENA_NATS_TLS_HOSTNAME` | the site's NATS (optional until events and OCR are wanted) |

```bash
athena library setup                  # directories, migrations, collections and series from the config
athena library intake [--watch 30]    # ingest bundles waiting in the intake directory
athena library ocr [--limit N]        # OCR pages without OCR text, through Sieve's ocr.page
athena library search "flower show"
athena library publish-events [--watch 5]
athena library status                 # health check: exit 1 if the database is unreachable
```

## What happens to a bundle

1. `athena bundle validate` rules ([capture-bundle.md](capture-bundle.md)); a bad bundle moves to
   `intake/rejected/` with a `.reason.txt`, and nothing from it is stored.
2. Raw masters and page images are copied into `originals/<sha256[:2]>/<sha256>.<ext>`, read-only, never
   overwritten or deleted by software; OCR-sized JPEGs go to `derivatives/ocr/`.
3. One transaction writes the item, its pages, tier-0 text, capture provenance, **assertions** and **outbox
   events**. An unknown series files the item as unsorted, with a warning.
4. The bundle moves to `intake/ingested/`. Ingesting it again is a no-op.

Everything enters **private** (collections are private; series, items and pages inherit). Choosing
audiences is [visibility.md](visibility.md).

## Provenance

Every change is an assertion (who said what about which record, when, from what source: `capture:<station>`,
`ocr:<engine>`, `person:<login>`, `config`) in the append-only `assertion` table, written in the same transaction as
the current-state row it changes. `UPDATE`, `DELETE` and `TRUNCATE` on assertions and on OCR text are refused by
trigger, so an OCR run never overwrites a person's correction and the audit trail can't be edited. Current state can
be rebuilt from assertions (`records.rebuild_item_fields`).

## Events

Each change also writes an outbox row; `publish-events` sends them to `library.<name>.<event>` on JetStream
(stream `LIBRARY_<NAME>_EVENTS`) with the outbox id as `Nats-Msg-Id`, so a republish after a crash is dropped.
Payloads carry ids only: `item.created`, `item.described`, `item.updated`, `item.ocr_done`.

## Measured

1,000 synthetic pages (200 five-page bundles) ingest in 8.3 s on a 16 GB M3 laptop (8 ms per page) and a search takes
under 2 ms. Real 48 MP captures take longer, mostly in making the OCR-sized copy; measure on the deployment's
machine with the pilot's pages (D06).

## Not yet

`ocr.jobs` (D09) replaces `library ocr` for overnight runs; the review UI (D13); backups and
`verify` (D17); vector search and the entity graph (later, in the same database).
