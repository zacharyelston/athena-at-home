# GNUmon: design

Status: **draft for discussion**, 2026-11-20.

GNUmon is a home **capture system**: any camera (or file, or phone) produces page images, one OCR pipeline
reads them, and the results land on the **home NAS**. It has three parts:

- **Capture stations**: cameras and importers that produce *capture bundles*. The desk camera station grows out
  of [OCRX](https://github.com/zacharyelston/OCRX), which is inspiration, not a dependency.
- **Sieve**: the OCR and classification pipeline (a fast local pass, then DeepSeek-OCR-2 and a local LLM).
- **HomeLibrarian**: the family-records library: store, search, review queue, retention.

FamilyFinances is a second consumer of the same pipeline and will also run on the NAS as a background service.

## Principles

1. **Family records stay in the house.** Tax returns, Social Security statements, loans and medical papers never
   go to a cloud API and never travel over work infrastructure. Every component here runs on home hardware.
2. **Capture anywhere, store once.** Any capture source produces the same *capture bundle*; the NAS keeps one
   immutable original per document, addressed by content hash.
3. **Two OCR passes, not one.** A fast local pass at capture time (quality check, instant search), and a
   high-quality layout-aware pass in the background. Every extracted field records which engine produced it.
4. **Reuse what's proven.** The NATS OCR contracts and the DeepSeek-OCR-2 worker already work in HaberDash;
   copy their shape onto a separate home deployment instead of inventing new ones.
5. **Measure before committing hardware.** None of the DeepSeek-OCR accuracy, speed or tokens-per-page figures in
   earlier experiments were measured locally (the terramiso numbers are simulated). Phase 0 fixes that.

## What already exists (verified)

| Piece | Where | State |
|---|---|---|
| Camera capture station: page detection, deskew, lighting flatten, Apple Vision OCR, quality gates, AprilTag DPI, pan/tilt (PIXY) | `OCRX` repo | Working, tested on real hardware |
| NATS OCR request/reply: `gpu.ocr`, queue group `ocr`, `{"image_b64","mode"}` → `{"text","model","worker"}` | HaberDash `deploy/nats/worker/gpu_worker.py` | Implemented and verified |
| Durable OCR jobs: JetStream `IMG_JOBS` on `gpu.img.pipeline.pdf-ocr`, Object Store `pdf-in`/`pdf-out`, `MAX_ACK_PENDING` = GPU budget | HaberDash `deploy/nats/worker/pdf_ocr_worker.py` | Implemented and verified (4-page paper → 14,480 chars) |
| OCR client (OpenAI-compatible URL or NATS; renders pages at 150 dpi; OCRs only pages with no text layer) | HaberDash `myvect/src/myvect/ocr.py` (`OcrClient`) | Working |
| DeepSeek-OCR-2 serving: vLLM + `NGramPerReqLogitsProcessor` (`ngram_size 30, window_size 90, whitelist 128821/128822`, `skip_special_tokens:false`), or output loops | HaberDash deploy YAML | Working on 48 GB Ada/A6000; CUDA only |
| Vision-token retrieval (DeepEncoder page tokens as a ColPali-style index) | HaberDash branch `claude/deepseekocr2-encoder-retrieval-*` | Hypothesis; synthetic test only |
| `~/code/DSOCR2` | — | Finance-only scaffold; its "DeepSeek" path calls a text chat API, not the OCR model. Not reused. |

The **Hydra/Ancera NATS hub is work infrastructure**. GNUmon reuses its *contracts and code*, never its
servers, accounts or GPUs.

## Architecture

```
 capture stations                     home NAS                                      consumers
 ────────────────                     ────────                                      ─────────
 OCRX desk camera ─┐                 ┌─────────────────────────────────────┐
 phone photo/scan ─┼─ capture  ───►  │ ingest ─► library store              │ ─►  HomeLibrarian (search,
 watched folders  ─┤  bundle         │   │       originals (sha256, RO)     │      browse, review queue,
 email (later)   ─┘                  │   │       derivatives, SQLite+FTS5   │      retention reminders)
                                     │   ▼                                  │
                                     │ home NATS (own server, own accounts) │ ─►  FamilyFinances service
                                     │   ocr.page   (req/reply)             │      (statements → transactions)
                                     │   ocr.jobs   (JetStream, durable)    │
                                     │   library.events                     │
                                     └──────────────┬──────────────────────┘
                                                    │ ocr.page / ocr.jobs
                                             home GPU worker
                                         DeepSeek-OCR-2 (vLLM) + local LLM
                                         for classification / extraction
```

### Capture bundle (the one contract every source produces)

A folder (or Object Store entry) per capture, extending what OCRX already writes to `scans/<ts>/`:

```
<capture-id>/
  manifest.json      source, station, camera, captured_at, operator, page count, sha256 of each file
  pages/001.jpg      deskewed page image(s), lossless-enough JPEG (q95) or PNG
  raw/001.jpg        the unprocessed camera frame (kept for re-processing)
  quick/001.json     Apple Vision lines + confidences (tier-0 OCR, done at the station)
  quality.json       sharpness, glare, evenness, DPI (from the AprilTag), skew, page size, paper guess
```

Files imported from disk (PDFs, phone photos) produce the same bundle; a PDF with a usable text layer skips OCR
for those pages, as `OcrClient` already does.

### Sieve: OCR tiers

| Tier | Engine | Where | Purpose |
|---|---|---|---|
| 0 | Apple Vision (`VNRecognizeTextRequest`) | capture station (Mac) | instant text for the quality check and first search; ~1 s/page |
| 1 | DeepSeek-OCR-2, markdown mode | home GPU worker | layout, tables, reading order; the text of record |
| 2 | local LLM over tier-1 markdown | home GPU worker | document type, date, institution, people, amounts, account last-4 |

Tier-0 text makes a document searchable the moment it's captured; tier 1 replaces it when the job finishes.

### Image reduction: matching capture DPI to DeepSeek-OCR-2's token budget

DeepSeek-OCR-2 encodes a page as one 1024² global view (256 vision tokens) plus 0–6 local 768² crops
(144 tokens each), so **256–1120 tokens per page**. That ties directly to capture resolution:

- A Letter page at **~150 DPI** is about 1275×1650 px, which tiles into roughly 2×3 = 6 crops of 768 px: the
  model's maximum. Above ~150 DPI the extra pixels are downscaled away unless the page is split.
- The PIXY at the arm's full height delivers ~150 DPI, which is right at that sweet spot.
- Sparse pages (receipts, notes, envelopes) can run with fewer crops; dense statements and forms want all 6.

So the capture station can choose a crop budget per page from the quality stats (text density from tier 0,
page size from the AprilTag) and record it in the manifest. Phase 0 measures whether that trade-off holds; this
is a hypothesis until then. The exact tiling rule should be read from the model's preprocessing code, not assumed.

### HomeLibrarian: store and schema (NAS)

- **Originals**: `originals/<sha256[:2]>/<sha256>.<ext>`, write-once. Never edited, never deleted by software.
- **Index**: SQLite with FTS5 to start (single writer = the ingest service). Postgres only if multi-writer needs
  appear.
- Core tables: `document` (id, title, type, date, institution, people, tags, retention_until, status),
  `page` (document, n, image sha256, dpi, quality), `text` (page, engine, engine_version, markdown, confidence),
  `field` (document, name, value, source engine, confidence, confirmed_by), `capture` (bundle provenance).
- **Review queue**: any field below a confidence threshold, or any new document type, waits for a person to
  confirm. Confirmed fields are never overwritten by a later engine run.

### Security

- NAS dataset encrypted at rest; snapshots on; 3-2-1 backups with an encrypted off-site copy.
- Home NATS with its own nsc operator and accounts (`CAPTURE`, `OCR`, `LIBRARY`, `FINANCE`), TLS, no leaf to any
  outside hub.
- Per-person logins in the HomeLibrarian UI; an audit log of who viewed or exported what.
- No document content in logs, URLs or notifications.

## Phases

**Phase 0: measure (decides the hardware).**
Stand up DeepSeek-OCR-2 on the candidate home GPU, or try Apple Silicon via MLX, which is documented but untested.
Using non-sensitive test documents first, compare Apple Vision vs DeepSeek-OCR-2 at crop budgets 0/2/4/6 and
capture DPI 100/150/200: character accuracy against typed ground truth, tables kept intact, seconds and tokens
per page. Output: a results table in `docs/`, and a hardware decision.

**Phase 1: library MVP on the NAS.**
Capture bundle format; OCRX "Send to library" (writes the bundle to an SMB share on the NAS); an ingest service
that hashes, stores, indexes tier-0 text in FTS5, and serves a simple search/browse page. Bulk-import
`~/FamilyFinances` as the first real collection.

**Phase 2: background OCR and classification.**
Home NATS on the NAS; port `gpu_worker.py` / `pdf_ocr_worker.py` as `ocr.page` / `ocr.jobs`; tier-1 markdown
replaces tier-0 text; tier-2 extraction feeds the review queue.

**Phase 3: more sources and FamilyFinances.**
Phone capture (shortcut or small app posting a bundle), watched folders, email attachments; FamilyFinances runs
as a NAS service subscribed to `library.events` for statements.

## Open questions

1. **NAS hardware**: model, CPU/RAM, can it run containers, does it have (or can it take) a GPU?
   DeepSeek-OCR-2 needs CUDA today; 16 GB VRAM is the commonly quoted floor, and the verified deployment used 48 GB.
2. **Where tier-1 OCR runs** if the NAS has no GPU: a separate home GPU box, or a Mac via MLX (if Phase 0 shows
   it works).
3. **Which family members** use it, and from which devices (home network only, or remote access via a VPN such
   as Tailscale)?
4. **Retention rules** per document type (e.g. tax returns 7 years), and whether paper originals are kept after
   scanning.
5. **Capture station code**: port the OCRX pipeline into `stations/desk` here, or keep OCRX as the station and
   only share the capture-bundle schema?
