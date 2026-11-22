# D06 · Capture method pilot: ~50 pages, OCR scoring, decision

| | |
|---|---|
| **ID** | D06 |
| **Type** | ops + human |
| **Owner role** | ops agent (fixtures, scorer, report); author and archivist (capture, transcriptions) |
| **Size** | 3 days agent work, plus about two hours of capture and two hours of transcription |
| **Depends on** | D01 (pilot sample), D03 (Sieve on the M1), D05 (bundle layout) |
| **Week** | W1 (fixtures, scorer, capture if the sample is at hand) – W2 (scores and **decision by Wed Dec 2**) |
| **Branch** | `feat/capture-pilot` (from `feat/capture-tool`, the top of the stack: bundles come from the capture tool) |

> **Kit built** on `feat/capture-pilot` (2026-11-21); how to run it is in `docs/pilot.md` on that branch. **The
> pilot itself is not done**: no real page has been captured, transcribed or scored, and there is no decision note.
> - `phase0/gen_testset.py` has the five historical styles (mimeograph, typewriter, three-column newsprint, a
>   program with a photograph, a script-font "hand"), with ground truth.
> - `python -m pilot.run | pilot.score | pilot.report` over capture bundles: both engines, DeepSeek at Sieve's
>   tile choice and at six tiles; CER, word F1, exact key items; agreement between engines on untranscribed pages.
> - Labels come from what the capture tool already records: material is the describe form's "Made how"; method is
>   the station name given at import; the ten comparison pages are items tagged `comparison`.
> - The report applies the criteria below and holds numbers and material, method and engine names only. Results
>   and transcriptions are refused inside the repository.
> - Running the capture tool on the historical styles found three faults in it, fixed on this branch: a scan with a
>   large picture was cropped to the picture; sparse regions read as "soft"; a picture read as uneven light.
> - A dry run with the real engines on synthetic captures (M3, not the M1) is written up in `docs/pilot.md`. Two
>   things to look for on real pages: Sieve's tile policy read a sparse program page from the overview and lost its
>   small caption (word F1 0.89; 0.98 with six tiles), and Apple Vision reads multi-column pages across the columns.
>
> **For the people, next:** the pilot sample (D01); capture it and import each method under its own `--station`
> name; set "Made how" and tag the comparison items; type about fifteen transcriptions as `pages.tsv` names them.

## Why

OCR is validated only on clean synthetic print. The club's records are mimeograph, old typefaces, newsprint,
photos and some handwriting. And the capture method (iPhone 48 MP stills, a flatbed, or the PIXY) sets how long
capture takes and what quality the archive keeps. Both are measured on real pages before anyone captures 2,500 of
them.

## Inputs

- D01's pilot folder (~50 pages across decades and materials).
- Equipment: iPhone (48 MP mode), a flatbed if one is available (borrowed is fine), the EMEET PIXY on its arm,
  a printed scale or ruler, a grey card, two lamps at 45° for the camera, a sheet of glass or a book cradle for
  curled pages.
- Sieve on the M1 (`sieve local` or `ocr.page`), Apple Vision on a Mac.
- `phase0/bench.py` scoring (`normalize`, `compact`, word F1, CER).

## Outputs

1. **Synthetic historical fixtures**: `phase0/gen_testset.py` extended with styles: mimeograph (purple or grey
   ink, bleed, uneven inking), typewriter, two- and three-column newsprint clipping on yellowed paper, a program
   or yearbook page with a photo block and caption, and a cursive "handwritten" page from a script font. Ground
   truth as before. These test the scorer in CI and give a synthetic baseline.
2. **`pilot/`**: bundles come from `athena capture import` (D07 is built, so no stand-in is needed); `run.py`
   (OCR every page with Apple Vision and DeepSeek-OCR-2 at auto and 6 tiles), `score.py`, `report.py`.
3. **Scores** (run locally; real outputs stay on the library disk):
   - ~15 pages with **ground truth**: typed by the archivist or author from the paper (not by correcting OCR
     output, which biases toward the engine). CER, word F1, and "key items exact" (names, dates, places).
   - All ~50 pages **without ground truth**: agreement between the two engines (word F1 between them), loop and
     cap flags, tile choices, seconds per page.
   - Per **material** (typeset, typewritten, mimeograph, photocopy, newsprint, handwriting, photo captions).
4. **Capture comparison** on the same 10 pages: iPhone vs flatbed vs PIXY: measured ppi (from the scale), OCR
   scores, edge-to-edge sharpness, glare, seconds per page including handling, and handling notes.
5. **Decision note** `docs/epics/garden-club-archive/pilot-decision.md`, aggregate numbers only:
   - capture method per material (e.g. iPhone on a stand for documents; flatbed at 600 ppi for photo prints;
     camera only for bound or brittle items);
   - OCR policy per material: OCR as text of record / OCR labelled uncorrected / image-only;
   - D12's session estimate (pages per hour → number of sessions);
   - whether bf16 or 6 fixed tiles are worth it on any material;
   - storage estimate for D10/D17.

6. **Capture-tool settings** for the deployment's `[capture]` section, from the pilot pages: `min_sharpness` and
   `min_sharpness_photo` (compare with OCRX's Laplacian figure, which `quality.json` records beside them),
   `min_ppi`, `max_glare`, `min_evenness`, `min_brightness`. Also confirm on real captures: the page finder on the
   actual table surface, the kind guess per material, and that 48 MP HEIC files from the iPhone are read.

## Decision criteria (agreed before the scores come in)

- **Documents**: the fastest method that reaches ≥ 400 ppi measured across the whole page, with no visible glare
  or focus fall-off at the edges, and word F1 within 0.02 of the best method.
- **Photo prints**: ≥ 600 ppi; colour; flatbed unless glossy reflections are worse there than with the camera.
- **OCR as text of record** for a material when word F1 ≥ 0.95 and key items exact ≥ 95% on the ground-truth pages;
  **OCR labelled uncorrected** when word F1 ≥ 0.80; **image-only** below that.
- Sieve's OCR input is a ~200 ppi JPEG derivative of the page (DeepSeek-OCR-2 discards detail above that), so
  preservation resolution doesn't slow OCR.

## Steps

1. Day 1 (agent): synthetic historical fixtures; scorer and report with tests on the fixtures.
2. Capture (people): the 50 pages with the method most at hand; the 10-page comparison with all three. Use the
   folder-per-item layout D05 describes (or separator sheets).
3. Day 2 (agent): bundle the captures on the library disk; OCR them on the M1; score.
4. Transcription (people): type ~15 pages' ground truth into plain text files beside the captures (private).
5. Day 3 (agent): score against ground truth; write the decision note; report to the author.

## Acceptance criteria

- Scorer tests pass in CI on the synthetic historical fixtures (including handwriting, which is expected to score
  badly; the test checks the scorer, not the engine).
- Every pilot page OCR'd by both engines; per-material table complete; capture comparison complete.
- Decision note committed with aggregate numbers only, and the author agrees with it by Wed Dec 2.
- No real page image, OCR text or transcription in git (D02's guard; plus `git grep` for three distinctive words
  from the pilot's ground truth returns nothing).

## Test plan

Unit tests for `score.py` and `report.py` on synthetic fixtures; a dry run of `run.py` with Sieve's `FakeEngine`;
the real run is the experiment.

## Privacy and rights

- [ ] Real captures, OCR output and transcriptions stay on the library disk (encrypted), never in git, never sent
      to any cloud service.
- [ ] The decision note contains numbers, material names and method names, not content.
- [ ] Records are handled as the archivist directs; pilot pages go back to their marked places.

## Touches

`phase0/gen_testset.py`, `pilot/` (new), `tests/test_pilot*.py`, `docs/epics/garden-club-archive/pilot-decision.md`.

## Done means

The author has a decision note, with numbers, that fixes the capture method and OCR policy per material, by
Wed Dec 2, and D12 can schedule sessions from it.
