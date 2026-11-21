# Athena@Home backend: derived from myvect and HaberDash

Status: **plan**, 2026-11-20. Refines Phases 1–2 of [design.md](design.md).

myvect and the HaberDash NATS OCR workers are the author's own earlier projects. Athena@Home **adapts and derives
from them rather than running them unchanged**: their proven patterns and code move into this repo, reshaped for a
home records system, and nothing here depends on either project at runtime.

## Provenance

Derived code is GPL-3.0-or-later like the rest of Athena@Home. Each derived file carries the SPDX header and says
what it came from, and the commit that adds it says the same:

```python
# SPDX-License-Identifier: GPL-3.0-or-later
# Derived from the author's myvect (store.py, chunking.py) in HaberDash.
```

Ancera-specific parts never come across: hostnames, Azure AD identity, the k8s manifests, the work NATS accounts.

## What derives from where

| Athena@Home component | Derived from | What changes |
|---|---|---|
| **Search service** (semantic index, document catalog) | myvect `store.py`, `chunking.py`, `ingest.py`, `enrich.py` | Text comes from athena-library, not watched folders; full-text and vector search in the library's PostgreSQL; document ids are Athena's, not file paths |
| **MCP server** (`athena-mcp`) | myvect `mcp_server.py` | Read-only tools over the records, limited by the portal's visibility tiers: cloud clients see public collections only |
| **OCR request/reply** (`ocr.page`) | HaberDash `deploy/nats/worker/gpu_worker.py` | In-process mlx-vlm instead of an HTTP call to vLLM; per-page tile budget |
| **Durable OCR jobs** (`ocr.jobs`) | HaberDash `deploy/nats/worker/pdf_ocr_worker.py` | Capture-bundle pages as well as PDFs; retry and dead-letter instead of ack-on-error |
| **OCR client** for stations and intake | myvect `src/myvect/ocr.py` (`OcrClient`) | NATS only; no vLLM `extra_body`; tile budget parameter |
| **Library intake** | myvect `ingest.py` (hash dedupe), `watcher.py` | Watches the bundle share; stores originals write-once |
| **Home NATS auth** | HaberDash `docs/nats-fabric-for-other-projects.md` (nsc recipe) | New home operator and accounts; no link to any outside hub |
| **DeepSeek-OCR-2 loading** | Athena@Home `phase0/bench.py` | Becomes Sieve's model loader |
| Write-once originals, fields, review queue, records UI, per-person login and audit | nothing comparable | Written new |
| **Portal**: visibility tiers and policies, publisher, static export, later a live portal | nothing comparable | Written new; see [portal.md](portal.md) |

## Architecture

```
 capture stations ──bundle──► Ubuntu mini ──────────────────────────────────────────────┐
                              │ compose stack (containers; ADR 0001)                     │
                              │ athena-library                                           │
                              │   intake ─► originals/ (sha256, write-once, volume)      │
                              │          ─► PostgreSQL: items, pages, assertions,        │
                              │             full text (tsvector), outbox                 │
                              │          ─► embeddings: chunks + vectors (pgvector)      │
                              │   search = full text (exact) + vectors (meaning), merged │
                              │   review queue, records UI, logins, audit log            │
                              │   athena-mcp (read-only, local clients by default)       │
                              │ nats (container; home operator)                          │
                              └──────────────┬───────────────────────────────────────────┘
                                             │ ocr.page (req/reply) · ocr.jobs (JetStream)
                                       M1 Mac mini: Sieve
                                       DeepSeek-OCR-2 8-bit (mlx-vlm, in process)
                                       Apple Vision tier 0 for Linux captures
                                       extraction LLM (later)
```

### Search service (derived from myvect)

- **Same core**: paragraph-aware chunks (1,200 characters, 200 overlap), ONNX all-MiniLM-L6-v2 on the CPU, which
  runs on the Intel mini. Vectors go into the library's PostgreSQL with `pgvector` instead of ChromaDB
  ([ADR 0001](adr/0001-containers-and-postgres.md)), so there's no second store to run and back up.
- **Different input**: athena-library hands it each document's tier-1 markdown with its document id, type, date
  and people, instead of the service walking folders. It never sees images, so myvect's PDF/OCR path isn't needed.
- **Exact plus semantic**: family-records questions are often exact ("account ending 4821", "$1,486.00"), which
  vector search handles badly. PostgreSQL full-text search answers exact terms, the vectors answer meaning, and the two rankings are
  merged (reciprocal rank fusion) into a list of documents, not chunks.
- **Collections instead of boxes**: named per deployment (a household might start with `family` and later
  `finance` for FamilyFinances' text; a club with its series). Each collection carries the portal's visibility tier
  (private, members, public), which also gates MCP.
- **Leave behind** from myvect: Azure AD / oauth2-proxy identity, the dashboard's cross-box endpoints (known
  leaks: `/api/events`, `/api/image`, `/metrics`), git and URL boxes, the amygdala memory tools.
- **Document summaries** (from `enrich.py`) come later, pointed at a model on the M1.

### athena-mcp and cloud AI

Anything returned to a cloud model such as Claude leaves the owner's machines. athena-mcp is read-only and follows
the portal's tiers: cloud clients see **public** collections only, members collections need an authenticated local
client, and private collections (a household's records) are never exposed. Everyday questions go to a local model
on the M1. Its MCP name is `athena`, so it can't be confused with the work `myvect` connector.

### Sieve: the OCR worker on the M1 (derived from the HaberDash workers)

- **In-process mlx-vlm** instead of an HTTP call to a vLLM server, using the Phase 0 loader. One model loaded,
  about 5.5 GB.
- **Subjects** `ocr.page` (request/reply, queue group `sieve`) and `ocr.jobs` (JetStream work queue, Object Store
  buckets `pages-in` / `text-out`). A job references a capture bundle's pages rather than a single PDF.
- **Tile budget per page**: 0 tiles for sparse pages, 6 for dense or tabular ones, chosen from tier-0 text density.
- **Failure handling**: the HaberDash worker acks failed jobs, so they're silently dropped. Instead, nak with a
  delay, cap deliveries, and move exhausted jobs to a dead-letter subject the review queue shows.
- **Loop guard**: keep the 3,000-token cap and the repeated-line detector from Phase 0.

## Build order

Revised 2026-11-20 for the first deployment, the Branford Garden Club archive
([epic](epics/garden-club-archive/README.md#change-to-the-build-order)): capture moves ahead of the library,
visibility and the publisher come before extraction, and the search service waits. Everything server-side runs
in containers with PostgreSQL as each library's database ([ADR 0001](adr/0001-containers-and-postgres.md)).

1. **Sieve on the M1**: derive the workers, MLX in process, `ocr.page` first. Check it against the Phase 0 set.
2. **Home NATS on the Ubuntu mini**: nsc operator and accounts (per deployment: `CAPTURE`, `OCR`, `LIBRARY`, and
   `FINANCE` for a household), a container in the site's compose stack, TLS, JetStream.
3. **Capture bundle spec and a capture tool**: archival description (collection → series → item → page); a folder
   importer for iPhone, flatbed and desk-camera images. OCRX's "Send to library" and a Linux station come later.
4. **athena-library MVP** (containers, [ADR 0001](adr/0001-containers-and-postgres.md)): bundle intake, originals
   store on a volume, PostgreSQL with provenance and full-text search, `library.events` through an outbox, the
   portal's visibility fields and policies in the schema from the start, and libraries as named instances (one
   compose project, later one namespace, each with its own database, volumes and config), so one machine or
   cluster can run several. `ocr.jobs` alongside it.
5. **Review UI**: metadata, OCR checks, visibility choices and redaction.
6. **Publisher and static site**: feeds built from a library and a static site with per-tier search; first
   needed for the Branford Garden Club archive. Library-to-library feeds (a guest library, a car) follow.
7. **Search service**: derive from myvect, index the library's text in `pgvector`, search combining full text and
   vectors.
8. **Extraction and review queue thresholds**: tier-2 fields, confidence thresholds, confirm-and-lock.
9. **athena-mcp**: read-only, gated by visibility tier.
10. **Live portal** with member logins.

## Notes

- **Dependency licenses**: onnxruntime, nats-py, FastAPI and the MCP SDK are Apache-2.0 or MIT; PostgreSQL and
  pgvector use the PostgreSQL License, psycopg 3 is LGPL-3.0, all compatible with GPLv3. PyMuPDF
  (only needed if Athena@Home ingests PDFs) is AGPL-3.0, which combines with GPLv3; its network clause is
  irrelevant on a home LAN but worth noting for the public release. For the extraction model, prefer an
  Apache-2.0 one; myvect's default `llama3.2:3b` uses the Llama Community License, which isn't free software.
- **Tests come along**: myvect has about 170 unit tests for ingest, chunking, the store and the MCP tools. Derive
  the relevant ones with the code; the HaberDash workers have none, so Sieve gets new tests (the Phase 0 set is a
  ready-made fixture).
