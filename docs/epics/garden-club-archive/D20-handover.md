# D20 · Handover guide and training for the archivist and the club

| | |
|---|---|
| **ID** | D20 |
| **Type** | doc |
| **Owner role** | ops agent (drafts); author (training session with the archivist) |
| **Size** | 2 days (draft W7, training and fixes W8) |
| **Depends on** | D14 (the UI it documents), D17 (backups and the BagIt export), D19 (hosting, accounts, takedown) |
| **Week** | W7–W8 |
| **Branch** | `docs/handover` (from `main`) |

## Why

The archive and the site have to outlive this project and not depend on one person. The archivist needs to keep
working in the library; the club needs to know what it owns, what it costs, and what to do if something goes wrong
or someone asks for removal.

## Inputs

- The review UI (D13, D14), the publisher and site (D15, D16), backups and export (D17), hosting and takedown
  (D19), the club's decisions (D18).

## Outputs

Generic guides in the repo (no club names; deployment-specific details in a private appendix kept with the
library):

1. `docs/guides/archivist.md`: **for the archivist**, with screenshots from the synthetic fixture library:
   - add new items (a capture session, from the runbook, in short);
   - review an item; correct OCR; mark reviewed;
   - change what's public or members-only (series, item, page), redact, see "what's public";
   - publish an update (or ask the author to);
   - handle a removal request.
2. `docs/guides/club.md`: **for the club's board**:
   - what Athena@Home is, in a paragraph; where the records and copies live;
   - what the club owns (host account, domain, renewal date and cost) and who has access;
   - what is public, what is members-only, and how those choices are changed;
   - the takedown promise and contact;
   - the club's own copy (BagIt export on a disk): what it is and how to open it without any special software;
   - what happens if the author is unavailable: the site keeps running as-is; anyone with the host account can
     take it down; the BagIt copy holds everything.
3. **Private appendix** (in the library's administrative series, not in git): the actual accounts, who holds which
   credentials, renewal dates, the backup locations and passphrase holder.
4. **Training session** with the archivist (about an hour), working through the archivist's guide on the real
   library; fix whatever confused them in the guide the same week.

## Steps

1. Draft both guides from the UI as built (W7); screenshots from the fixture library only.
2. Private appendix filled in with the club after D19's accounts exist.
3. Training (W8, after launch); note every question asked; update the guides; questions feed D21.

## Acceptance criteria

- The archivist, using only the guide, can: review an item, change a series from members to public and back, and
  explain how a removal request is handled.
- The board has the club guide and the private appendix's account details are with the club.
- The guides contain no real records, names or account details (screenshots from fixtures; checked by the author).

## Test plan

The training session is the test: the archivist does the three tasks with the guide in hand; anything they can't do
from the guide is a bug in the guide (or in D13/D14) and gets fixed or listed in D21.

## Privacy and rights

- [ ] No credentials or personal details in repo docs; the appendix is private.
- [ ] Screenshots from synthetic fixtures only.

## Touches

`docs/guides/archivist.md`, `docs/guides/club.md`, `docs/guides/img/` (fixture screenshots).

## Done means

The archivist can run the archive day to day from the guide, and the club knows what it owns, what it publishes,
and how to keep or remove it without the author.
