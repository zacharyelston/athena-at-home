# Visibility: tiers, policies and effective visibility

Status: **D11**, 2026-11-21. Code: [`athena/visibility/`](../athena/visibility) (pure rules in `core.py`; the
library adapter in `library.py`). Design: [portal.md](portal.md#visibility-tiers).

Whoever owns a collection decides what is public. Athena@Home supplies the mechanism and records every choice;
it never makes one.

## Tiers and inheritance

| Tier | Who sees it |
|---|---|
| `private` | the people who run this library (**default for everything**) |
| `members` | a group the owner admits (club members, staff, guests) |
| `public` | anyone the receiving side serves |

Settings live on **collection → series → item → page**. A collection with no setting is private; anything below
with no setting inherits from its parent. A setting may be wider than its parent's (that's how one series of a
private collection becomes public) or narrower.

- **Widening** (any change that makes a node's own-or-inherited tier wider, including clearing a narrower setting
  back to "inherit") **needs a reason** and goes into a **review queue** until someone confirms it.
- **A page can be narrower than its item, never wider.** An item whose pages are all set private is private.

## Policies

Policies cap whole classes of items so nobody decides page by page. They are written in the library's
`library.toml`, so they are the deployment's choice, and they **only ever narrow**:

> effective tier = min(own-or-inherited setting, the cap of every matching enabled policy)

```toml
[[policy]]
name = "unreviewed-private"      # nothing is shared until someone has reviewed it
kind = "workflow"
cap = "private"
when = { status_not = "reviewed" }

[[policy]]
name = "recent-members"          # the last 50 years are for members
kind = "age"
cap = "members"
when = { newer_than_years = 50 }

[[policy]]
name = "directories-members"
kind = "content"
cap = "members"
when = { type_in = ["directory"] }

[[policy]]
name = "contact-details-members"
kind = "content"
cap = "members"
when = { tags_any = ["contact-details"] }

[[policy]]
name = "clippings-in-copyright"
kind = "rights"
cap = "members"
when = { type_in = ["clipping"], published_after_pd_cutoff = true }
```

Every condition in `when` must hold (they combine with **and**; write two policies for "or").

| Condition | Matches when |
|---|---|
| `status_not`, `status_in` | the item's review status is not / is one of these (`new`, `ocr_done`, `reviewed`) |
| `type_in` | the item's type is one of these |
| `tags_any` | the item has any of these tags |
| `series_in` | the item is in one of these series |
| `newer_than_years = N` | the item's **latest possible** year is after `this year − N`; **undated items count as recent** |
| `published_after_pd_cutoff = true` | the latest possible year is after the US public-domain cutoff; **undated items count as in copyright** |

| Kind | Needs |
|---|---|
| `workflow` | `status_not` or `status_in` |
| `age` | `newer_than_years` |
| `content` | `type_in`, `tags_any` or `series_in` |
| `rights` | `published_after_pd_cutoff` |

A policy's `cap` is `private` or `members` (never `public`); `enabled = false` turns one off without deleting it.

**The public-domain cutoff is computed, not hardcoded**: US works published in or before `this year − 96` are in
the public domain (1930 during 2026, 1931 from Jan 1, 2027). Each decision records the cutoff it used. This is a
rule of thumb for a policy the owner chooses, not legal advice; uncertain rights belong in private or members.

## Feeds

A feed is the set of tiers a receiving site or library gets:

```toml
[[feed]]
name = "public"
tiers = ["public"]

[[feed]]
name = "members"
tiers = ["public", "members"]
```

No feed carries private material. The publisher builds each feed from these definitions ([publisher.md](publisher.md)).

## Using it

```bash
athena library explain ITEM                      # tier, the reasons, the cutoff used, and the feeds it goes to
athena library set-visibility series newsletters public --as archivist --why "board decision, Jan 5"
athena library set-visibility page ITEM/2 private --as archivist
athena library set-visibility item ITEM inherit --as archivist --why "as the series"
athena library reviews                           # widened settings waiting for a second look
athena library reviews --confirm 3 --as author
athena library check-policies new-library.toml   # what a changed config's policies would do, before applying it
```

An explanation reads like:

```
collection archive: private (default) → series newsletters: public
  → policy recent-members caps to members (latest date 1980 is within 50 years) → members
```

## Audit

Every setting change is an assertion (predicate `visibility`, source `person:<login>`, with the tier before and
after, whether it widened, and the reason) written in the same transaction as the change, plus a
`visibility.changed` event. Confirming a review is an assertion too. Policies are mirrored from the config into the
`policy` table at `athena library setup`, each change asserted with source `config`.

## Guarantees the tests hold

Property tests over random hierarchies, facts, dates and policy sets check that adding a policy never widens
anything (so removing one never narrows), that a page is never wider than its item, and that effective visibility
is never wider than the settings alone. Table tests pin each rule above, including the cutoff moving on Jan 1.
