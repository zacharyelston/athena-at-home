# Athena@Home: design

Status: **draft for discussion**, 2026-11-20.

Athena@Home is a **capture and records system** built from core modules: any camera (or file, or phone) produces
page images, one OCR pipeline reads them, and the results land in one or more **libraries** on the owner's
machines. The modules:

- **Capture stations**: cameras and importers that produce *capture bundles*. The desk camera station grows out
  of [OCRX](https://github.com/zacharyelston/OCRX), which is inspiration, not a dependency.
- **Sieve**: the OCR and classification pipeline (a fast local pass, then DeepSeek-OCR-2 and a local LLM).
- **Library** (HomeLibrarian in a household): store, search, review queue, retention, visibility.
- **Publisher and portal**: feeds from one library to another library or to a website, on the owner's terms
  ([portal.md](portal.md)).

FamilyFinances is a second consumer of the same pipeline and will also run on the home server as a background
service.

## Core modules, composed per deployment

Nothing in the modules assumes a household. A **deployment** decides how many libraries it runs, where each one
lives on the network, what each holds, and which feeds connect them. The same modules cover, for example:

| Deployment | Libraries |
|---|---|
| **Household** | a private library on the home LAN; a guest library on the guest wifi (wifi details, house rules, what's nearby) fed from it |
| **Car** | a small offline library in the car (owner's manual, repair guide, its settings, the registration and insurance cards it must carry), synced from the home library when the car is home |
| **Club** (the Branford Garden Club) | the archivist's private library; a public site and a members site fed from it |
| **Library or company** | a staff library; a public catalog portal fed from it |

Rules for the modules that follow from this:

- A library is a **named instance** with its own data directory, config and network address; any number can run
  on one machine, and none assumes which network it's on.
- Names such as `family`, `home` or a hostname are **configuration defaults**, never hardcoded.
- Libraries share only through **feeds** built by the publisher (filtered, redacted, logged); a receiving library
  never reads another library's store.
- Services that are shared by a site (NATS, Sieve) are addressed by configuration, so a car library that is offline
  simply queues its OCR until it's back on the home network.

The platform below is **one deployment**: the author's household.

## Platform (reference deployment: the author's household)

The home hardware, and what each machine does. The NAS is defined at release; until then nothing may assume
NAS-only features. Because the store is content-addressed and every service talks over NATS, moving the
infrastructure role to a NAS later is a copy plus a redeploy, not a redesign.

| Machine | Role |
|---|---|
| **M1 Mac mini** | **Sieve**: the house OCR engine. DeepSeek-OCR-2 through **MLX** (Apple's ML framework; no NVIDIA GPU here, so not vLLM), the local LLM for extraction, and Apple Vision tier-0 for bundles that arrive without it. **16 GB unified memory**: the 8-bit model should leave room for a small extraction LLM; bf16 is worth measuring. Phase 0 decides. |
| **Ubuntu Intel mini (2017, 16 GB)** | Always-on infrastructure: NATS server, HomeLibrarian (ingest, store, search UI), later FamilyFinances. This is the role the NAS takes over at release. Can also host a Linux camera station. |
| **Omarchy + Home Assistant mini (16 GB)** | Not in the critical path. Optional Home Assistant integration: notifications ("statement filed"), a scan button. |
| **Mac laptops** | Development; desk camera stations (OCRX-style) with Apple Vision locally. |
| **iPhones** | Capture stations: VisionKit document camera (edge detection and perspective correction built in) plus Apple Vision locally. |

**OCR availability by platform.** Apple Vision exists only on macOS and iOS, so stations there attach tier-0 text
to the bundle. **Linux stations do no OCR**: they send page images and rely on Sieve for every tier (Sieve runs
tier 0 too, so their captures become searchable as quickly as the M1 can turn them around). Page detection,
deskew and quality checks are OpenCV and run everywhere. Camera pan/tilt on Linux uses `v4l2-ctl` (UVC controls
are exposed directly; the IOKit helper OCRX needed is macOS-only).

## Principles

1. **Private by default, published by choice.** Nothing leaves the owner's machines unless the owner publishes it.
   Processing (OCR, extraction, search) never uses a cloud API or anyone else's infrastructure. What a deployment
   makes public is its decision, made explicitly per collection through the [portal](portal.md): a household
   keeps its records private (tax returns, Social Security statements and medical papers stay home) but might
   share a guest page on the home network, while a library, a club or a company can run public and private sides
   from the same archive.
2. **Capture anywhere, store once.** Any capture source produces the same *capture bundle*; the home server keeps one
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
| DeepSeek-OCR-2 on Apple Silicon: [mlx-vlm](https://pypi.org/project/mlx-vlm) lists it as supported; [`mlx-community/DeepSeek-OCR-2-bf16`](https://huggingface.co/mlx-community/DeepSeek-OCR-2-bf16) and an 8-bit build are published | mlx-vlm | **Not tested here.** The only memory/speed figures found (~39 GB peak, ~6 tok/s) are from one unclear source |
| Vision-token retrieval (DeepEncoder page tokens as a ColPali-style index) | HaberDash branch `claude/deepseekocr2-encoder-retrieval-*` | Hypothesis; synthetic test only |
| `~/code/DSOCR2` | — | Finance-only scaffold; its "DeepSeek" path calls a text chat API, not the OCR model. Not reused. |

The **Hydra/Ancera NATS hub is work infrastructure**. Athena@Home reuses its *contracts and code*, never its
servers, accounts or GPUs.

## Architecture

```
 capture stations                     Ubuntu mini (NAS at release)                   consumers
 ────────────────                     ────────────────────────────                   ─────────
 Mac desk camera  ─┐                 ┌─────────────────────────────────────┐
 iPhone scan      ─┼─ capture  ───►  │ ingest ─► library store              │ ─►  HomeLibrarian (search,
 Linux camera     ─┤  bundle         │   │       originals (sha256, RO)     │      browse, review queue,
 email (later)    ─┘                 │   │       derivatives, SQLite+FTS5   │      retention reminders)
                                     │   ▼                                  │
                                     │ home NATS (own server, own accounts) │ ─►  FamilyFinances service
                                     │   ocr.page   (req/reply)             │      (statements → transactions)
                                     │   ocr.jobs   (JetStream, durable)    │
                                     │   library.events                     │
                                     └──────────────┬──────────────────────┘
                                                    │ ocr.page / ocr.jobs
                                             Sieve (M1 Mac mini)
                                         DeepSeek-OCR-2 (MLX) + local LLM
                                         for classification / extraction
```

### Capture bundle (the one contract every source produces)

A folder (or Object Store entry) per capture, extending what OCRX already writes to `scans/<ts>/`:

```
<capture-id>/
  manifest.json      source, station, camera, captured_at, operator, page count, sha256 of each file
  pages/001.jpg      deskewed page image(s), lossless-enough JPEG (q95) or PNG
  raw/001.jpg        the unprocessed camera frame (kept for re-processing)
  quick/001.json     Apple Vision lines + confidences (tier-0 OCR; macOS/iOS stations only, else added by Sieve)
  quality.json       sharpness, glare, evenness, DPI (from the AprilTag), skew, page size, paper guess
```

Files imported from disk (PDFs, phone photos) produce the same bundle; a PDF with a usable text layer skips OCR
for those pages, as `OcrClient` already does.

### Sieve: OCR tiers

| Tier | Engine | Where | Purpose |
|---|---|---|---|
| 0 | Apple Vision (`VNRecognizeTextRequest`) | macOS/iOS stations; Sieve on the M1 for Linux captures | instant text for the quality check and first search; ~1 s/page |
| 1 | DeepSeek-OCR-2, markdown mode | Sieve (MLX on the M1 Mac mini) | layout, tables, reading order; the text of record |
| 2 | local LLM over tier-1 markdown | Sieve (MLX on the M1 Mac mini) | document type, date, institution, people, amounts, account last-4 |

Tier-0 text makes a document searchable the moment it's captured; tier 1 replaces it when the job finishes.

### Image reduction: matching capture DPI to DeepSeek-OCR-2's token budget

DeepSeek-OCR-2 encodes a page as one 1024² overview (256 vision tokens) plus 0–6 local 768² tiles (144 tokens
each), so **257–1121 image tokens per page**. Measured in Phase 0 ([results](phase0-results.md)):

- **The tile grid follows the page's aspect ratio, not its resolution.** A Letter page gets 2×3 = 6 tiles and is
  resized to 1536×2304, so a 150 DPI capture is mildly upscaled and detail above ~180–200 DPI is discarded. The
  PIXY at the arm's full height (~150 DPI) is in the right range.
- **The tile budget is a speed lever.** Overview only (0 tiles) halves OCR time and stays exact on sparse pages
  (letters, forms, receipts, bills); dense tabular pages (bank statements) need all 6 tiles.

So Sieve picks a tile budget per page from tier-0 text density and table detection, and records it in the
manifest.

### HomeLibrarian: store and schema

- **Originals**: `originals/<sha256[:2]>/<sha256>.<ext>`, write-once. Never edited, never deleted by software.
- **Index**: SQLite with FTS5 to start (single writer = the ingest service). Postgres only if multi-writer needs
  appear.
- Core tables: `document` (id, title, type, date, institution, people, tags, retention_until, status),
  `page` (document, n, image sha256, dpi, quality), `text` (page, engine, engine_version, markdown, confidence),
  `field` (document, name, value, source engine, confidence, confirmed_by), `capture` (bundle provenance).
- **Review queue**: any field below a confidence threshold, or any new document type, waits for a person to
  confirm. Confirmed fields are never overwritten by a later engine run.

### Security

- Encrypted at rest: LUKS on the Ubuntu mini's library disk, FileVault on the M1 (it holds page images while
  OCRing), NAS dataset encryption at release. 3-2-1 backups with an encrypted off-site copy.
- Home NATS (on the Ubuntu mini) with its own nsc operator and accounts (`CAPTURE`, `OCR`, `LIBRARY`, `FINANCE`), TLS, no leaf to any
  outside hub.
- Per-person logins in the HomeLibrarian UI; an audit log of who viewed or exported what.
- No document content in logs, URLs or notifications.

## Phases

**Phase 0: measure on the M1 Mac mini.**
**Done on a 16 GB M3 laptop, 2026-11-20: see [phase0-results.md](phase0-results.md).** 8-bit DeepSeek-OCR-2 via
mlx-vlm read every key field exactly at ~5.5 GB peak, 7–16 s/page; Apple Vision is the right tier 0 but silently
dropped amounts on one page; bf16 still needs a run on the M1 itself. Original plan: run DeepSeek-OCR-2 through
mlx-vlm, bf16 and 8-bit, next to Apple Vision. Using non-sensitive test documents
first, compare crop budgets 0/2/4/6 and capture DPI 100/150/200 on: character accuracy against typed ground truth,
tables kept intact, seconds per page and **peak memory** (on the 16 GB M1 this decides bf16 vs 8-bit, and
whether the local LLM can stay loaded alongside it). Watch for **repetition loops**: on vLLM they needed the `NGramPerReqLogitsProcessor`, which
mlx-vlm doesn't have, so check whether a repetition penalty or max-token cap is enough. Output: a results table in
`docs/`, and a decision on model precision and what stays resident on the M1.

**Phase 1: library MVP on the Ubuntu mini.**
Capture bundle format; OCRX "Send to library" (writes the bundle to a share on the Ubuntu mini); an ingest service
that hashes, stores, indexes tier-0 text in FTS5, and serves a simple search/browse page. Bulk-import
`~/FamilyFinances` as the first real collection.

**Phase 2: background OCR and classification.**
Home NATS on the Ubuntu mini (`nats-server` as a systemd service); Sieve on the M1 subscribes over the LAN; port `gpu_worker.py` / `pdf_ocr_worker.py` as `ocr.page` / `ocr.jobs`; tier-1 markdown
replaces tier-0 text; tier-2 extraction feeds the review queue.

**Phase 3: more sources and FamilyFinances.**
iPhone station (VisionKit capture + Apple Vision, posting a bundle), a Linux camera station, watched folders,
email attachments; optional Home Assistant notifications; FamilyFinances runs
as a service on the home server subscribed to `library.events` for statements.

## Open questions

1. **What stays resident on the M1 (16 GB)**: OCR and the extraction LLM loaded together, or taking turns? Set by
   Phase 0's peak-memory numbers.
2. **NAS at release**: model and role. Most likely it replaces the Ubuntu mini's infrastructure role while the M1
   (or a successor) keeps doing OCR.
3. **Which family members** use it, and from which devices (home network only, or remote access via a VPN such
   as Tailscale)?
4. **Retention rules** per document type (e.g. tax returns 7 years), and whether paper originals are kept after
   scanning.
5. **Capture station code**: port the OCRX pipeline into `stations/desk` here, or keep OCRX as the station and
   only share the capture-bundle schema?
