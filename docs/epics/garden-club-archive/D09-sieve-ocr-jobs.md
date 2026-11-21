# D09 · Sieve `ocr.jobs`: durable queue, retry, dead-letter

| | |
|---|---|
| **ID** | D09 |
| **Type** | code |
| **Owner role** | ops agent |
| **Size** | 3 days |
| **Depends on** | D03 (Sieve on the M1), D04 (home NATS with JetStream) |
| **Week** | W2 |
| **Branch** | `feat/sieve-ocr-jobs` (from `feat/sieve`) |

## Why

OCRing ~2,500 pages takes 7–10 hours on the M1. That's overnight work, and it has to survive a sleeping laptop, a
Sieve restart or a bad page without losing or silently dropping anything. HaberDash's `pdf_ocr_worker.py` acks
failed jobs, so they vanish; Sieve retries and dead-letters instead ([backend.md](../../backend.md)).

## Inputs

- Sieve's `service.py` (the `ocr.page` handler), `engine.py`, `tiles.py`, `guard.py`.
- HaberDash `deploy/nats/worker/pdf_ocr_worker.py` (pattern: JetStream pull consumer, Object Store in/out,
  `MAX_ACK_PENDING` as the GPU budget). Derived files get the SPDX header and a provenance line.
- D04's `OCR` account with JetStream.

## Outputs

- **Contract** in `docs/sieve.md`:
  - Stream `OCR_JOBS`, subject `ocr.jobs.submit`, work-queue retention. One message = one page:
    `{"job": "<bundle-id>/<n>", "library": "<name>", "page_key": "<sha256>", "tiles": "auto", "kind": "text",
    "tier0": true}`.
  - Page images in Object Store bucket `pages-in` keyed by the OCR derivative's sha256 (~200 ppi JPEG, made by the
    library). Results in bucket `text-out` keyed `<page sha256>.<engine>.<engine version>.json`.
  - Completion event on `ocr.jobs.done.<library>` with the job id, result key and status; the library subscribes.
  - Failure: `nak` with delay (backoff 30 s, 2 min, 10 min), `max_deliver: 4`; after that the worker publishes
    `ocr.jobs.dead.<library>` with the reason and terms the message. A max-deliveries advisory catches crashes.
- `sieve/jobs.py` (pull consumer, one page at a time, `max_ack_pending` = 1 per worker) and `sieve jobs` CLI
  (`serve`, `submit <bundle>`, `status`, `dead`).
- **Idempotence**: if `text-out` already holds the result for this page and engine version, ack without OCR.
- `ocr.page` and `ocr.jobs` served by the same process, sharing one loaded model and one page at a time.

## Steps

1. Stream, consumer and buckets created idempotently at worker start (or by D04's setup script).
2. Worker loop: fetch 1, load page from `pages-in`, OCR (same code path as `ocr.page`), put result, publish done,
   ack. Heartbeat with `in_progress()` during long pages so the ack wait doesn't expire.
3. Error classes: transient (NATS timeouts, Metal hiccup) → nak with backoff; permanent (undecodable image,
   page too large) → dead-letter immediately; loop or cap hit → success with `looped`/`hit_cap` flags (the library
   shows them for review).
4. `submit`: from a bundle directory, put OCR derivatives in `pages-in` and publish one job per page (skipping
   `kind: photo`/`blank` per policy).
5. Second worker: the M3 laptop can join the same consumer; document it.

## Acceptance criteria

- With `FakeEngine` and a throwaway JetStream `nats-server`:
  - 20 jobs submitted → 20 results in `text-out`, 20 done events, stream empty.
  - Killing the worker mid-job → the job is redelivered and completes once; no duplicate result.
  - A job that fails transiently twice then succeeds → one result; delivery count 3.
  - A permanently failing job → on `ocr.jobs.dead.<library>` with a reason after ≤ 4 deliveries; never silently
    dropped.
  - A resubmitted, already-done page → acked without calling the engine.
  - `ocr.page` requests are answered while a job is running (served between pages).
- On the M1 with the real model: the Phase 0 set submitted as jobs produces the same text as `ocr.page`.
- CI green; MLX tests skip on Linux.

## Test plan

pytest with the `nats-server -js` fixture (extend `tests/conftest.py`), fault injection through `FakeEngine(fail=…)`;
one manual overnight run of the synthetic set ×20 on the M1 to check for leaks (memory flat, no stuck messages).

## Privacy and rights

- [ ] Object Store buckets live on the Ubuntu mini's encrypted disk; results carry page text, so no logging of
      results' content.
- [ ] Buckets have a TTL or are cleaned after the library confirms ingestion (text-out stays until ingested;
      pages-in is deleted after success).
- [ ] No outside NATS; derived code keeps SPDX and provenance headers, no Ancera specifics.

## Touches

`sieve/jobs.py`, `sieve/__main__.py`, `tests/test_jobs.py`, `tests/conftest.py`, `docs/sieve.md`.

## Done means

A bundle submitted to `ocr.jobs` is OCR'd exactly once even across crashes, failures end on a dead-letter subject
the library can show. (The fallback that runs pages through `ocr.page` before this lands lives in D10.)
