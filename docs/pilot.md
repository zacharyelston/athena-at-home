# The capture pilot

Status: **D06 kit**, 2026-11-21. Code: [`pilot/`](../pilot). The pilot itself (capturing, transcribing, deciding)
is still to do; this is what it runs on.

OCR has only been measured on clean synthetic print. An archive's records are mimeograph, typewriter, newsprint,
photographs and handwriting, and the capture method (a phone on a stand, a flatbed, a desk camera) sets how long
capture takes and what quality the archive keeps. The pilot measures both on about fifty real pages before anyone
captures thousands, and ends in a short decision note.

Everything here runs on the owner's machines: Apple Vision and DeepSeek-OCR-2 through [Sieve](sieve.md), nothing in
any cloud.

## The steps

```bash
# 1. Capture and import the sample, one session folder per method, each under its own station name
athena capture import ~/Captures/pilot-phone   --station phone   --staging /Volumes/Library/pilot/bundles
athena capture import ~/Captures/pilot-flatbed --station flatbed --staging /Volumes/Library/pilot/bundles
athena capture describe --staging /Volumes/Library/pilot/bundles

# 2. OCR every page with both engines (on the Mac that runs Sieve, or through its worker)
python -m pilot.run /Volumes/Library/pilot/bundles --out /Volumes/Library/pilot/results
python -m pilot.run /Volumes/Library/pilot/bundles --out /Volumes/Library/pilot/results --nats-url nats://HOST:4222

# 3. Type about fifteen transcriptions, then score and report
python -m pilot.score  /Volumes/Library/pilot/results --truth /Volumes/Library/pilot/truth
python -m pilot.report /Volumes/Library/pilot/results --total-pages 2500
```

In the describe form, three things carry the pilot's labels:

| In the form | Means, for the pilot |
|---|---|
| **Made how** | the page's **material**: typeset, typewritten, mimeograph, photocopy, newsprint, handwriting, photo print |
| the tag **comparison** | this item is one of the ten captured with every method |
| *(the station name given at import)* | the capture **method** |

`pilot.run` needs the `sieve` extra on Apple Silicon for an in-process run; anywhere else, give it a Sieve worker
with `--nats-url`. `--list` only lists the pages. Running again continues where it stopped. A second model is a
second run with its own name: `--model mlx-community/DeepSeek-OCR-2-bf16 --tag bf16`.

Each page is sent as the library sends it (a JPEG at about 200 ppi). One request takes Sieve's own tile choice and
Apple Vision's text; a second asks for six tiles, unless the first already used six.

## Transcriptions

`results/pages.tsv` lists every page with its title, material, method, the page image, and the **name of the
transcription file to type**. For about fifteen pages across the materials:

- type the page **from the paper** into a plain text file with that name, in a private folder. Don't start from
  OCR output and correct it: that favours the engine it came from;
- optionally, beside it, `<same name>.keys.txt` with the names, dates and places that must come out exactly, one
  per line. Without it nothing is claimed about key items, and the material can't qualify as text of record.

Pages without a transcription are still measured: by how far the two engines agree.

## What stays private

`results/` and the transcriptions hold the text of real records. They live on the library's disk; `pilot.run`
refuses to write inside this repository. The **report** is the one output meant to leave that disk: numbers,
material names, method names and engine names, with labels cut to 24 plain characters. It carries no titles, text,
ids or file names (a test plants all of those and looks for them).

## The report

- **OCR by material and engine**: word F1, CER and exact key items against the transcriptions; agreement with Apple
  Vision on every page; loops and cap hits; seconds a page; how often Sieve's tile policy chose six.
- **OCR policy** by the criteria agreed beforehand, judged on the setting Sieve actually runs:

  | Policy | When |
  |---|---|
  | text of record | word F1 ≥ 0.95 and key items exact ≥ 95% |
  | shown, labelled uncorrected | word F1 ≥ 0.80 |
  | image only | below that |

  and whether six fixed tiles would be worth it (a gain of 0.02 or more).
- **Capture methods**, over the comparison pages: measured ppi, sharpness, glare and soft pages, OCR, seconds a
  page from the files' own clocks, megabytes a page. Then the criteria: documents need every page at ≥ 400 ppi, no
  glare, no soft pages and OCR within 0.02 of the best method, and the fastest such method wins; photographs need
  ≥ 600 ppi. A flatbed's files carry no capture times, so time a few pages by hand.
- **Sessions and storage** for the whole archive (`--total-pages`).
- **What the capture tool measured**: the weakest 5% and the median of each measure per method, for setting the
  `[capture]` thresholds ([capture.md](capture.md#the-re-shoot-list)).

The report applies the criteria; the decision is the author's, written up as
`docs/epics/garden-club-archive/pilot-decision.md` from the report's numbers.

## A synthetic baseline

The whole chain was run once on made-up pages, to prove it and to have something to compare the real pilot with:
the five historical styles of `phase0/gen_testset.py`, each "captured" three ways (a sharp camera, a deliberately
blurred camera, a clean 300 ppi scan), imported with the capture tool, and read by Apple Vision and DeepSeek-OCR-2
8-bit in process on a 16 GB M3. Fifteen pages; word F1 against the exact ground truth, averaged over the three
captures:

| Style | Apple Vision | DeepSeek, Sieve's tile choice | DeepSeek, 6 tiles | |
|---|---|---|---|---|
| typewritten | 1.000 | 0.997 | 1.000 | |
| mimeograph | 0.997 | 0.983 | 0.995 | |
| newsprint, three columns | 0.991 | 0.971 | 0.971 | Vision's CER is 0.64: it reads across the columns |
| typeset program with a photograph | 0.994 | 0.892 | 0.983 | Sieve chose the overview every time |
| "handwriting" | 0.955 | 0.923 | 0.968 | a script font, not a hand |

What this is worth: it shows the kit measures what it should, and it turned up three things to watch for on real
pages.

- **Sieve's tile policy** took the program page for a sparse one and read it from the overview alone, losing most
  of the small italic caption (0.89; six tiles give 0.98). The policy was calibrated on the Phase 0 pages only. If
  real programs and yearbooks show the same, the policy needs a rule for small type on an otherwise sparse page.
- **Apple Vision reads multi-column pages across the columns.** Every word is there (F1 0.99) in the wrong order
  (CER 0.64), which matters for search snippets and not at all for finding a page.
- **The blurred camera** was flagged on all five pages by the capture tool and cost about 0.05 of word F1.

What it is not worth: any prediction for real records. Synthetic ink on synthetic paper is far kinder than a
sixty-year-old stencil, and a script font is nothing like a person's hand. DeepSeek took 1.5 to 6 seconds a page
here.

## Tests

`tests/test_pilot.py`: the scorer on every historical style (a perfect reading scores perfectly however the model
dresses it in markdown; dropped lines, garbling and one wrong digit each cost what they should), then a whole
synthetic pilot with a stand-in for the engines whose errors are known in advance, so every number in the report
can be checked. One test drives Sieve's real request handler with its fake model. Nothing in CI loads a model.

```bash
python tests/fixtures/synthetic_pilot.py /tmp/pilot --ppi 300     # bundles and transcriptions to try by hand
```

## Not done yet

- **The pilot.** No real page has been captured, transcribed or scored. That needs the sample from the survey
  (D01), about two hours of capture and two of typing, then the decision note (due Wed Dec 2).
- **Sieve on the M1** hasn't run these pages; the dry run above was on the M3 laptop.
- **Edge-to-edge sharpness as its own column**: the report counts soft pages from the capture tool's flags rather
  than tabulating each ninth.
- **Handling notes** (how each method treats bound or brittle items) are for a person to write, not measure.
