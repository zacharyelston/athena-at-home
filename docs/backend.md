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
| **Search service** (semantic index, document catalog) | myvect `store.py`, `chunking.py`, `ingest.py`, `enrich.py` | Text comes from athena-library, not watched folders; one service with FTS5 alongside the vectors; document ids are Athena's, not file paths |
| **MCP server** (`athena-mcp`) | myvect `mcp_server.py` | Read-only tools over the records; only collections marked shareable; family records never exposed to cloud clients by default |
| **OCR request/reply** (`ocr.page`) | HaberDash `deploy/nats/worker/gpu_worker.py` | In-process mlx-vlm instead of an HTTP call to vLLM; per-page tile budget |
| **Durable OCR jobs** (`ocr.jobs`) | HaberDash `deploy/nats/worker/pdf_ocr_worker.py` | Capture-bundle pages as well as PDFs; retry and dead-letter instead of ack-on-error |
| **OCR client** for stations and intake | myvect `src/myvect/ocr.py` (`OcrClient`) | NATS only; no vLLM `extra_body`; tile budget parameter |
| **Library intake** | myvect `ingest.py` (hash dedupe), `watcher.py` | Watches the bundle share; stores originals write-once |
| **Home NATS auth** | HaberDash `docs/nats-fabric-for-other-projects.md` (nsc recipe) | New home operator and accounts; no link to any outside hub |
| **DeepSeek-OCR-2 loading** | Athena@Home `phase0/bench.py` | Becomes Sieve's model loader |
| Write-once originals, fields, review queue, records UI, per-person login and audit | nothing comparable | Written new |

## Architecture

```
 capture stations ──bundle──► Ubuntu mini ──────────────────────────────────────────────┐
                              │ athena-library                                           │
                              │   intake ─► originals/ (sha256, write-once)              │
                              │          ─► library.sqlite: documents, pages, fields,    │
                              │             FTS5 full-text                                │
                              │          ─► search index: chunks + embeddings (Chroma)   │
                              │   search = FTS5 (exact) + vectors (meaning), merged      │
                              │   review queue, records UI, logins, audit log            │
                              │   athena-mcp (read-only, local clients by default)       │
                              │ nats-server (home operator)                              │
                              └──────────────┬───────────────────────────────────────────┘
                                             │ ocr.page (req/reply) · ocr.jobs (JetStream)
                                       M1 Mac mini: Sieve
                                       DeepSeek-OCR-2 8-bit (mlx-vlm, in process)
                                       Apple Vision tier 0 for Linux captures
                                       extraction LLM (later)
```

### Search service (derived from myvect)

- **Same core**: paragraph-aware chunks (1,200 characters, 200 overlap), ChromaDB, ONNX all-MiniLM-L6-v2 on the
  CPU, which runs on the Intel mini. Set `ANONYMIZED_TELEMETRY=False`.
- **Different input**: athena-library hands it each document's tier-1 markdown with its document id, type, date
  and people, instead of the service walking folders. It never sees images, so myvect's PDF/OCR path isn't needed.
- **Exact plus semantic**: family-records questions are often exact ("account ending 4821", "$1,486.00"), which
  vector search handles badly. FTS5 answers exact terms, the vectors answer meaning, and the two rankings are
  merged (reciprocal rank fusion) into a list of documents, not chunks.
- **Collections instead of boxes**: `family` by default, and later `finance` for FamilyFinances' text. Each
  collection is marked shareable or not; that flag gates MCP.
- **Leave behind** from myvect: Azure AD / oauth2-proxy identity, the dashboard's cross-box endpoints (known
  leaks: `/api/events`, `/api/image`, `/metrics`), git and URL boxes, the amygdala memory tools.
- **Document summaries** (from `enrich.py`) come later, pointed at a model on the M1.

### athena-mcp and cloud AI

Anything returned to a cloud model such as Claude leaves the house. athena-mcp is read-only, serves local clients
by default, and exposes only collections explicitly marked shareable; `family` never is. Everyday questions go to a
local model on the M1. Its MCP name is `athena`, so it can't be confused with the work `myvect` connector.

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

1. **Sieve on the M1**: derive the workers, MLX in process, `ocr.page` first. Check it against the Phase 0 set.
2. **Home NATS on the Ubuntu mini**: nsc operator and accounts (`CAPTURE`, `OCR`, `LIBRARY`, `FINANCE`), systemd
   service, TLS.
3. **athena-library MVP**: bundle intake from a share, originals store, SQLite + FTS5, `library.events`.
4. **Search service**: derive from myvect, index the library's text, first search page combining FTS5 and vectors.
5. **Capture**: OCRX "Send to library" writes bundles; a Linux station sends pages through `ocr.page`.
6. **Extraction and review queue**: tier-2 fields, confidence thresholds, confirm-and-lock.
7. **athena-mcp**: read-only, shareable collections only.

## Notes

- **Dependency licenses**: ChromaDB, onnxruntime, nats-py, FastAPI and the MCP SDK are Apache-2.0 or MIT. PyMuPDF
  (only needed if Athena@Home ingests PDFs) is AGPL-3.0, which combines with GPLv3; its network clause is
  irrelevant on a home LAN but worth noting for the public release. For the extraction model, prefer an
  Apache-2.0 one; myvect's default `llama3.2:3b` uses the Llama Community License, which isn't free software.
- **Tests come along**: myvect has about 170 unit tests for ingest, chunking, the store and the MCP tools. Derive
  the relevant ones with the code; the HaberDash workers have none, so Sieve gets new tests (the Phase 0 set is a
  ready-made fixture).
