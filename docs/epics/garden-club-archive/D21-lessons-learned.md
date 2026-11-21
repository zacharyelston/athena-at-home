# D21 · Lessons learned and an example deployment config

| | |
|---|---|
| **ID** | D21 |
| **Type** | doc |
| **Owner role** | ops agent (gathers numbers, drafts); author (judgements, final text) |
| **Size** | 1 day |
| **Depends on** | D19 (launch); notes from every deliverable's report |
| **Week** | W8 (by Fri Jan 15) |
| **Branch** | `docs/lessons-garden-club` (from `main`) |

## Why

This deployment is an experiment. Its value is as much in what it teaches the next deployment (a household, a
car, another club or library) as in the club's site. Numbers measured on a real collection replace guesses.

## Inputs

- Every deliverable's report back (agents note surprises, time spent vs estimate, what they'd change).
- D06 pilot decision, D12 capture log totals, Sieve and library timings, D18 review hours, D19 launch and takedown
  drill, D20 training questions.
- The club's `library.toml` (to sanitize into an example).

## Outputs

1. `docs/lessons/garden-club-2027.md` (aggregate numbers, no content):
   - **Measured**: pages and items; capture pages/hour per method; OCR seconds per page on the M1 by tile budget;
     OCR quality per material (from the pilot); review minutes per item; storage (masters, derivatives, site
     size and file count); build and deploy times; restore time.
   - **Estimates vs actuals** per deliverable (days), and which risks happened.
   - **What worked, what didn't, what was missing** in each module (capture, Sieve, library, visibility,
     publisher, site, backups).
   - **What the club chose** in general terms (e.g. "publications public, directories members-only, clippings by
     rights date"), as patterns other clubs might reuse.
   - **Changes for the next deployment**: concrete issues or backlog items, and any change to the design docs or
     build order.
2. `deploy/examples/club/library.toml`: a sanitized example of a club deployment (series list, type vocabulary,
   policies, feeds, site config) with made-up names ("Example Garden Club"), so the next club starts from a
   working configuration.
3. Proposed updates to `docs/design.md`, `docs/portal.md`, `docs/backend.md` if the experiment changed anything.

## Steps

1. Collect numbers (queries against the library and logs; agents' reports).
2. Draft; the author adds judgements and decides what goes in the backlog.
3. Sanitize the config: no real names, addresses, contacts, account ids or URLs.

## Acceptance criteria

- Every measured number listed above is present (or marked "not measured" with why).
- Each risk in the epic marked happened / didn't, with what was done.
- The example config validates (`athena library init --config deploy/examples/club/library.toml` in a temp dir)
  and builds a site from the canary fixture library.
- `git grep` for the club's real contact details, member names or domain (other than in this epic's docs) returns
  nothing.

## Test plan

A test that loads and validates the example config and builds a fixture site from it (so it can't rot).

## Privacy and rights

- [ ] Aggregate numbers only; no record content, member names or personal details.
- [ ] The club is named only where the epic already names it; the example config is fictional.

## Touches

`docs/lessons/garden-club-2027.md`, `deploy/examples/club/library.toml`, `tests/test_examples.py`, and proposed
edits to the design docs.

## Done means

The next deployment can start from a working example config and a page of measured numbers and lessons, written
by Fri Jan 15.
