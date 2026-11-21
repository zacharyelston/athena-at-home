# D12 · Capture sessions: runbook, then the whole shoebox

| | |
|---|---|
| **ID** | D12 |
| **Type** | ops + human |
| **Owner role** | ops agent (runbook, 1 day); author and archivist (sessions) |
| **Size** | 1 day runbook + about 6–8 sessions of 2–3 hours (set by D06's pages per hour) |
| **Depends on** | D06 (method), D07 (capture tool). Sessions may start before D07 lands, using separator sheets and plain folders. |
| **Week** | runbook W2; sessions W2–W4, catch-up W7 |
| **Branch** | `docs/capture-runbook` (from `main`) for the runbook |

## Why

The whole shoebox has to be captured with consistent quality, in original order, with nothing damaged and nothing
lost between the table and the library. A short runbook makes each session the same, and a capture log reconciles
against D01's count.

## Inputs

- D06 decision note (method per material, ppi targets, pages per hour).
- D07 tool (`separator`, `import`, `check`, `describe`).
- D01 survey (groups, counts, condition notes, original order photo).

## Outputs

- `docs/capture-runbook.md` (generic, for any deployment) with a club-specific checklist kept privately.
- A **capture log** (private, in the library's data dir): per session, the groups and folders captured, page
  counts, re-shoots, anything skipped and why.
- All items ingested into the library, raw images copied to a second disk after each session.

## Runbook contents

1. **Before**: charge the iPhone, clean the lens, set 48 MP (HEIF Max or ProRAW per D06), lock exposure and focus
   on a stand; lamps at 45° on both sides, no overhead light (glare); scale and grey card in frame; print
   separator sheets; clean, dry hands (no lotion); clear table; the archivist chooses the groups for the session.
2. **During**: one group at a time in original order; separator sheet before each item; an item slip (title,
   date if known) photographed after the separator; versos captured when they carry anything; bound items open
   no wider than they want to; no unfolding of brittle folds (note it and move on); photos on the flatbed per
   D06; return each item to its folder before taking the next.
3. **Before packing up**: `athena capture check` on the session folder; re-shoot anything flagged; copy the
   session folder to the second disk (R5); fill in the capture log.
4. **After**: `athena capture import` into the library's intake; `describe` the items (now, or later in the review
   UI); OCR runs overnight; next morning, check the library for rejected bundles and OCR failures.

## Session plan

| Week | Sessions | Content (adjust to D01) |
|---|---|---|
| W2 (Dec 3–4) | 1 | first group, to shake out the runbook |
| W3 | 2–3 | publications (yearbooks, programs, newsletters): the likely public core |
| W4 | 2–3 | records, directories, clippings, photos |
| W5–W6 | optional | only if the people involved want to; nothing depends on it |
| W7 | 1 | catch-up, re-shoots, anything found late |

## Acceptance criteria

- Runbook reviewed by the archivist (readable without the author present).
- After the last session: the capture log's page counts per group reconcile with D01's (within ±5%, every
  difference explained); every item is in the library; no bundle sits rejected; every page is OCR'd or marked
  image-only per D06's policy.
- Every session's raw folder exists on the second disk.
- No item damaged; the box goes back in its original order (compare with D01's photo).

## Test plan

A dry run of the runbook with synthetic printouts (a few pages from `gen_testset.py`) before the first real
session; reconciliation is a query against the library compared with the survey sheet.

## Privacy and rights

- [ ] Session folders live on encrypted disks only (the Mac's FileVault disk, the library disk, the backup disk);
      iPhone photos are deleted from the phone and from iCloud Photos after import (or iCloud Photos is off for
      the sessions; check, since iCloud would upload records to the cloud).
- [ ] Capture continues only for preservation until D08's permission to digitize is recorded.
- [ ] The capture log has group names and counts, not contents.

## Touches

`docs/capture-runbook.md`. Everything else is private data.

## Done means

The whole shoebox is in the library, reconciled with the count, with a copy of every session on a second disk and
the paper back in its original order.
