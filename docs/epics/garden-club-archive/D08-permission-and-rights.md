# D08 · Permission to digitize and publish; rights inventory

| | |
|---|---|
| **ID** | D08 |
| **Type** | human-archivist |
| **Owner role** | archivist (with the club's president or board); author drafts the request |
| **Size** | 1 day of effort spread over W1–W3, plus waiting |
| **Depends on** | D01 (what's in the box) |
| **Week** | request out by **Wed Nov 25**; permission to digitize by **Fri Dec 4**; permission to publish by **Fri Jan 8** |
| **Branch** | none (permission is a private record in the library; the template below is the only repo content) |

## Why

The records belong to the club, and some contain other people's work (newspaper clippings, photographers' prints)
or personal information (members' addresses and phone numbers). The club has to say yes to digitizing, and decide
what may be published. This is the launch's critical path, so it starts in week 1.

This deliverable lists questions to put to the club. It is **not legal advice**; if the club is unsure about
something, it stays private or members-only, which is always safe.

## Inputs

- D01's survey (groups, decades, sensitive groups).
- portal.md's tiers and policies (to explain the choices).

## Outputs

1. **Permission request** sent by the archivist (template below), and the reply kept as a private record in the
   library (scanned or saved email), series "administrative", visibility private.
2. **Rights inventory** per series: who created it (the club, a newspaper, a photographer, a member), when it was
   published, whether living people are identifiable, whether it carries personal contact details.
3. **Answers to Q4, Q8, Q9, Q12** in the epic.

## Steps

1. Author drafts, archivist adapts and sends the request (W1).
2. Archivist fills in the rights inventory from the survey (W2), with the author's help on the questions below.
3. Archivist brings the rights questions to the board with D18's choices sheet (W4–W7).

### Permission request (template)

> The club's historical records are being digitized to preserve them and, with the board's agreement, to share
> some of them on a club website. Everything stays private until the club decides otherwise, and the paper goes
> back exactly as it came.
>
> We ask the board for:
> 1. permission to digitize the club's records and keep the digital copies on the archivist's private system;
> 2. agreement in principle to publish a selection on a public website and, if wanted, a members-only section,
>    with the board choosing what goes where before anything is published;
> 3. a contact for removal requests once the site is live.

### Rights questions per series

| Question | Why it matters |
|---|---|
| Did the club create it (newsletters, programs, yearbooks, minutes)? | The club can authorize publishing its own works. |
| Is it a **newspaper clipping**? Year of publication? | US works published **1931 or earlier** are in the public domain from Jan 1, 2027 (the cutoff moves each January; D11 computes it). Later clippings need permission or stay members-only/private. |
| Is it a **photograph**? Who took it? Are living people identifiable? | Photographers hold copyright; people in photos may not want them online. |
| Does it carry **addresses, phone numbers, emails** (directories, membership lists)? | Personal data; usually members-only or redacted. |
| Is it **correspondence** or **minutes** naming individuals in personal matters? | Privacy, even when the club owns the record. |
| Was anything given to the club **with conditions**? | Donor restrictions. |

## Acceptance criteria

- Permission to digitize recorded before the first full capture session (D12), or capture continues only for
  preservation with nothing shown outside the archivist and author.
- Permission to publish (or a clear "not yet") recorded by Fri Jan 8.
- Every series in the draft list has a rights row.

## Test plan

The author checks the rights inventory covers every series in D01's list and every sensitive group D01 flagged.

## Privacy and rights checklist

- [ ] The permission record and rights inventory are private records in the library, not in git.
- [ ] No member's personal details appear in the request or in any repo file.
- [ ] Uncertain rights default to private or members-only, never public.

## Touches

The repo gets nothing beyond this file. Answers feed D11 (policy config), D18 (choices) and D19 (takedown contact).

## Done means

The club's permission to digitize is on record, the publish question is with the board along with a complete
rights inventory, and its answer is recorded by Fri Jan 8.
