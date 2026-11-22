# D18 · The archivist's review pass and the club's visibility choices

| | |
|---|---|
| **ID** | D18 |
| **Type** | human-archivist |
| **Owner role** | archivist (review); the club's board (choices); author (supports, prepares the choices sheet) |
| **Size** | review: about 10–15 hours over several sittings (≈ 2 minutes per item for ~300–500 items, plus OCR checks); choices: one board discussion |
| **Depends on** | D08 (permission, rights inventory), D12 (items captured), D14 (visibility UI), D16 (preview) |
| **Week** | review W4–W7; **choices recorded by Fri Jan 8** |
| **Branch** | none (choices are library configuration and audit rows; nothing in git) |

## Why

Two of the success criteria are human: every item reviewed, and the club's choices made. The system makes both easy
(D13, D14) but can't make them. They are the launch's critical path.

## Inputs

- The review UI (D13, D14) on the home network.
- The preview site (D16), from Dec 18.
- D08's permission status and rights inventory; D06's OCR policy per material.

## Outputs

1. **Every item `reviewed`**: title, date (or "undated"), series, type; OCR checked per page (good / usable /
   image only); names and dates on key pages corrected where they matter.
2. **The choices sheet** (prepared by the author from the series overview in D14, printed or as a PDF): one row per
   series with item counts, decades, a few thumbnails, the rights notes, and the options, with no option
   pre-selected:

   | Series | Items | Decades | Rights notes | Public / Members / Private | Exceptions |
   |---|---|---|---|---|---|

   plus the policy questions, each with its effect preview ("items newer than __ years members-only: affects N
   items"; "directories: members-only / private / public with contact details redacted"; "clippings still in
   copyright: members-only / private"; "unreviewed items: private"), and the site questions (Q5, Q6, Q7, Q12).
3. **The club's decisions entered** in the library by the archivist (or the author with the archivist, recorded
   with the archivist's reason text): series settings, policies, exceptions, redactions, rights statement per
   series.
4. A short **decision record** (private, in the library's administrative series): what was decided, by whom, when.

## Steps

1. W4: the archivist reviews the first captured series in the UI with the author, then continues alone.
2. Dec 18: preview site and the choices sheet go to the archivist; the archivist shares them with the board as
   the club prefers (Q1).
3. W5–W6: optional; the archivist may review or browse the preview over the holidays, nothing is due.
4. W7: board decision (meeting or email); the archivist enters the choices; the author checks the "what's public"
   view with the archivist; anything undecided stays private (the launch goes ahead with what's decided).
5. Every widened item cleared from the review queue.

## Acceptance criteria

- All items `reviewed`, no OCR failures outstanding, no items in "unsorted" (or each explained).
- Every series has a setting or is covered by a policy; the board's decision is recorded with a date.
- The "what's public" view has been walked through by the archivist and author together, and the archivist
  confirms it matches what the club decided.
- No item is public because of a default: every public item traces (via D11's reasons) to a series, item or
  page setting someone chose.

## Test plan

A report from the library (counts by status and by effective tier per series, and the reasons for each public item
grouped by the choice that made it public) reviewed by the author and archivist together.

## Privacy and rights checklist

- [ ] Directories and contact details: the club's choice recorded (Q9); redactions done where chosen.
- [ ] Living people in photos: the club's choice recorded (Q8).
- [ ] Clippings: rights policy set; any exceptions have a reason.
- [ ] Permission to publish recorded (D08).
- [ ] Undecided means private.

## Touches

Library configuration and data only.

## Done means

The archive is reviewed, the club has made its choices, those choices are in the library, and the archivist has
confirmed the public view matches them, by Fri Jan 8.
