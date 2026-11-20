# Phase 0 results: Apple Vision vs DeepSeek-OCR-2 on Apple Silicon

Run 2026-11-20 on a **MacBook Pro M3, 16 GB** (same memory as the M1 Mac mini; faster chip, so absolute speeds
here are optimistic for the M1). Code, test generator and raw outputs: [`phase0/`](../phase0).

## Verdict

1. **DeepSeek-OCR-2 runs well on 16 GB Apple Silicon in 8-bit through mlx-vlm**: every amount, date and ID exact
   on all test pages at 100–200 DPI, proper markdown tables, **~5.5 GB peak memory**, 7–16 s per page, and no
   repetition loops at temperature 0 with a 3,000-token cap. That leaves ~10 GB on the M1 for an extraction LLM.
2. **Apple Vision is excellent as tier 0** (~0.15 s/page, word F1 ≈ 0.99) **but not as the text of record**: on one
   page (bank statement, camera, 200 DPI) it **silently dropped three balance amounts**, while reading them fine at
   other DPIs. Omissions, not misreads, are what make it unsafe for financial records.
3. **The tile budget is a real speed lever**: overview only (0 tiles) halves the time and stays perfect on sparse
   pages (letter, form, receipt, bill); the dense bank statement needs all 6 tiles. Sieve should choose the budget
   per page.
4. **bf16 could not run on the dev laptop** (Metal GPU timeout on the first page, at every tile budget) while the
   machine was carrying ~20 GB of swap from other apps. Not a verdict on the M1 mini, which will run Athena@Home alone;
   retest there. 8-bit already reads every key field exactly, so bf16 is only worth it if real documents show gaps.

## Test set

Six synthetic household documents (all names and numbers made up), rendered at US Letter with exact ground truth
by [`phase0/gen_testset.py`](../phase0/gen_testset.py): bank statement with a 14-row transaction table, utility
bill, insurance letter (prose), thermal-style receipt (light Courier), medical intake form (boxed fields), W-2 style
form (two-column boxes). 88 key fields in total (amounts, dates, IDs, account numbers).

Each page at 100/150/200 DPI in two conditions: **clean** (resampled only) and **camera** (mild blur, uneven
light, sensor noise, JPEG q88), roughly what a deskewed desk-camera capture looks like.

Scores: **word F1** (order-free word overlap), **CER** (character error rate; order-sensitive, so the W-2's
two-column reading order costs ~0.22 CER for both engines even when every word is right), and **fields exact**
(each key field must appear character-for-character).

## Head to head: camera capture, 150 DPI

| engine | word F1 | fields exact | s/page | peak memory |
|---|---|---|---|---|
| Apple Vision (accurate, language correction) | 0.992 | 88/88 | 0.2 | n/a |
| DeepSeek-OCR-2 8-bit, 6 tiles | 0.996 | 88/88 | 11.3 | 5.5 GB |

## Apple Vision across conditions

| cond | dpi | word F1 | fields exact | missed |
|---|---|---|---|---|
| camera | 100 | 0.992 | 100% | 0 |
| camera | 150 | 0.992 | 100% | 0 |
| camera | 200 | 0.981 | 96.6% | **3** (bank statement balances, dropped) |
| clean | 100 | 0.990 | 98.9% | 1 |
| clean | 150 | 0.991 | 100% | 0 |
| clean | 200 | 0.993 | 100% | 0 |

## DeepSeek-OCR-2 8-bit across DPI and tile budget

| cond | dpi | tiles | word F1 | fields exact | s/page | peak GB |
|---|---|---|---|---|---|---|
| camera | 100 | 6 | 0.980 | 100% | 10.1 | 5.5 |
| camera | 150 | 6 | 0.996 | 100% | 11.3 | 5.5 |
| camera | 200 | 6 | 1.000 | 100% | 10.1 | 5.5 |
| clean | 150 | 6 | 1.000 | 100% | 9.1 | 5.5 |
| camera | 150 | 2 | 0.971 | 98.9% (1 missed) | 6.0 | 5.2 |
| camera | 150 | 0 (overview only) | 0.949 | 98.9% (1 missed) | 5.8 | 5.5 |

Per page at 150 DPI camera: sparse pages take 3.6–5 s with 0 tiles and stay perfect; the bank statement takes
14–16 s with 6 tiles, and with 0–2 tiles drops to F1 ≈ 0.88 and misses one amount. 100 DPI is still exact on key
fields with 6 tiles but loses some words on the dense table (bank statement F1 0.89).

## What this changes in the design

- **Tile count follows aspect ratio, not resolution.** DeepSeek-OCR-2's processor picks the tile grid whose shape
  best matches the page (a Letter page gets 2×3 = 6 tiles of 768 px, i.e. it is resized to 1536×2304) and resizes
  the page to it. So a 150 DPI capture is mildly upscaled and detail above ~180–200 DPI is discarded. The
  150–200 DPI target stands, for that reason.
- **Adaptive tile budget in Sieve**: use tier-0 text density (and table detection) to pick 0 tiles for sparse
  pages and 6 for dense or tabular ones. On this set that would halve average OCR time with no loss.
- **Model**: 8-bit DeepSeek-OCR-2 on the M1. bf16 only if real documents show gaps and it runs on the M1.
- **Repetition loops**: none seen with a 3,000-token cap at temperature 0, without the NGram logits processor the
  vLLM deployment needed. Keep the cap and the loop detector; these pages were short and clean.

## Integration notes (mlx-vlm 0.8.0)

- The model repo ships custom Python (`auto_map`). `mlx_vlm.load()` goes through `AutoProcessor`, which asks to run
  it and fails non-interactively. Don't: load `DeepseekOCR2Processor` (with `trust_remote_code=False`) and
  `mlx_vlm.utils.load_model` directly, and attach the streaming detokenizer yourself (see `bench.py`).
- The prompt must end with a **trailing space** (`"<image>\nConvert the document to markdown. "`), matching the
  chat template. The processor drops the final token in inference mode; without the space it drops the `.` and the
  model stops after one token.
- Tile budget is controlled through the processor: `cropping=False` (overview only, 257 image tokens) or
  `max_patches=N` (up to 6 tiles, 1,121 image tokens).

## Not yet covered

- Real documents: handwriting, photos and logos, multi-column layouts, faded thermal receipts, crumpled or glossy
  pages. Next step: a local set captured with the OCRX/PIXY station, scored without committing any content.
- M1 Mac mini timings (expect slower than the M3) and bf16 there.
- The tier-2 extraction LLM and whether it fits alongside OCR in 16 GB.
- Multi-page PDFs and pages that already have a text layer.
