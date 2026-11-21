# D14 · Review UI: visibility choices, policies view, redaction

| | |
|---|---|
| **ID** | D14 |
| **Type** | code |
| **Owner role** | ops agent |
| **Size** | 3 days |
| **Depends on** | D11 (visibility logic), D13 (review UI), D15 (its dry-run build, for the "what's public" view) |
| **Week** | W4 |
| **Branch** | `feat/visibility-ui` (from `feat/review-ui`, rebased on `feat/visibility`) |

## Why

The club's choices have to be easy to make and easy to change: by series in a few clicks, by policy for whole
classes of items, and by item or page for the exceptions, with redaction for the page that is fine to share except
for one name or phone number. And everyone has to be able to see what will be public before it is.

## Inputs

- D11: `effective` with reasons, `feeds_for`, `diff` effect preview, `set_visibility` with audit.
- D13: the review UI and its auth.
- D10: `redaction` column on `page`.

## Outputs

- **Visibility panel** on collection, series, item and page: current setting (or "inherits"), effective tier with
  the reasons chain, and which feeds it goes to. Change it with a reason (free text, required for widening).
- **Series overview** ("choices board"): every series with item counts per effective tier, a thumbnail strip, its
  setting and the policies that touch it. This screen is what the archivist shows the board (D18).
- **Bulk actions**: select items by search or filter and set a tier or a tag.
- **Policies view**: the policies in `library.toml`, each with what it currently affects; editing a policy shows
  the effect preview (D11 `diff`) before saving; saving writes the config and an audit row. (Editing raw TOML is
  not required of the archivist.)
- **"What's public" view**: everything currently in the public feed, and everything in the members feed, as the
  site would show it, including redactions.
- **Redaction**:
  - *Region*: drag rectangles on the page image; stored as normalized coordinates on the page; preview with black
    boxes.
  - *Text*: select text in the OCR panel (or a regex, e.g. phone-number pattern, with matches to confirm) to mark
    spans; stored as character ranges against a specific `text` row. If the text row changes, its spans are
    re-checked and flagged for review instead of silently shifting.
  - A page with a region redaction but no matching text redaction (or the reverse) is flagged: "region covers
    text the OCR still contains?" with the overlapping OCR lines highlighted (uses tier-0 line boxes).
- **Widening needs review**: any widened node appears in a queue until a second look (by either login) confirms it.

## Steps

1. Visibility panel and series overview (read: D11; write: `set_visibility`).
2. Bulk actions; policies view with effect preview.
3. Redaction tools and the region/text consistency check.
4. "What's public" view rendered from the same feed builder D15 uses (a dry run without writing files), so the UI
   and the site can't disagree.

## Acceptance criteria

- Setting a series to public changes the effective tier of its items to public except where a policy caps them,
  and the panel shows why for each.
- Editing a policy shows the correct count of affected items before saving (matches D11 `diff`).
- Bulk set by search updates exactly the matched items and writes one audit row per item.
- A region drawn over a fixture phone number, plus the matching text span, removes it from the "what's public"
  view's image and text; drawing only the region raises the consistency flag.
- Widening without a reason is refused; widened items appear in the review queue.
- All actions keyboard-accessible; region drawing also possible by entering coordinates (for accessibility and
  tests).

## Test plan

TestClient tests for every action and its audit rows; fixture library with known phone numbers and names at known
positions (synthetic pages from `gen_testset.py` with a "directory" style added); a test that the "what's public"
view and D15's dry run produce identical lists.

## Privacy and rights

- [ ] Default remains private; nothing in this UI publishes. Publishing is D15/D16 and a deliberate deploy (D19).
- [ ] Every change audited with who and why.
- [ ] Redaction is non-destructive in the library (originals untouched) and burned in only in feeds (D15).

## Touches

`athena/library/web/` (visibility, policies, redaction routes and templates), `athena/visibility/` (adapters if
needed), `tests/test_web_visibility*.py`, `phase0/gen_testset.py` (directory-style page).

## Done means

The archivist can set the club's choices by series, policy, item and page, redact what needs it, and see exactly
what the public and members sites would show, before anything is built.
