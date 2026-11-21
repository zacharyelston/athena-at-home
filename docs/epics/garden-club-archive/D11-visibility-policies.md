# D11 · Visibility tiers, policies and effective visibility

| | |
|---|---|
| **ID** | D11 |
| **Type** | code |
| **Owner role** | ops agent |
| **Size** | 3 days |
| **Depends on** | D05 (the hierarchy and EDTF dates). Integrates with D10's schema (published on D10's day 1). |
| **Week** | W2–W3 (in parallel with D10) |
| **Branch** | `feat/visibility` (from `feat/library-core` once its schema commit exists; before that from `feat/capture-bundle`) |

## Why

The club decides what is public, and those decisions must be easy to make and easy to change. Settings per
collection, series, item and page, plus a few policies, cover a whole archive without page-by-page decisions
([portal.md](../../portal.md#where-visibility-is-set)). This deliverable is the logic, as a pure module with heavy
tests, so every consumer (review UI, publisher, the choices sheet) gets the same answer.

## Inputs

- portal.md: tiers (private < members < public), inheritance, narrowing vs widening, policy kinds, effective
  visibility, feeds defined by tiers.
- D05's EDTF parser (`earliest`, `latest`).
- D10's schema (`visibility`, `redaction` and `policy` columns; the append-only `assertion` table as the audit log).

## Outputs

- `athena/visibility/` (no I/O in the core; an adapter for D10's PostgreSQL schema):
  - `effective(node, ancestors, policies, today) -> Decision(tier, reasons)` where `reasons` is a readable chain:
    *"series Newsletters: public → policy age-50 caps to members (latest date 1980 is within 50 years) → members"*.
  - `feeds_for(decision, feeds)`: which feeds an item or page goes to.
  - `diff(before_config, after_config, items) -> counts and lists per tier change`: the **effect preview** for the
    club ("turning on this policy moves 112 items from public to members").
  - `set_visibility(node, tier, who, why)`: writes the setting and an audit row; widening is allowed but flagged
    `needs_review` and listed in the review UI.
- **Policy config** in `library.toml` (the deployment's choice, not code):

  ```toml
  [[policy]]
  name = "unreviewed-private"   # workflow: nothing shared until reviewed
  kind = "workflow"
  cap = "private"
  when = { status_not = "reviewed" }

  [[policy]]
  name = "recent-members"       # age
  kind = "age"
  cap = "members"
  when = { newer_than_years = 50 }

  [[policy]]
  name = "directories-members"  # content
  kind = "content"
  cap = "members"
  when = { type_in = ["directory"], tags_any = ["contact-details"] }

  [[policy]]
  name = "clippings-in-copyright"  # rights
  kind = "rights"
  cap = "members"
  when = { type_in = ["clipping"], published_after_pd_cutoff = true }
  ```

- **Feed config** in `library.toml`: `[[feed]] name = "public", tiers = ["public"]`;
  `[[feed]] name = "members", tiers = ["public", "members"]`. Feeds are definitions, not code.

## Rules (the tests pin these down)

1. Default is private at every level. A child with no setting inherits its parent's; the collection's default is
   private.
2. **Policies only cap**; they never widen. Effective tier = min(own-or-inherited setting, every matching
   policy's cap).
3. A page can be narrower than its item, never wider; an item with all pages private is effectively private.
4. **Unknown data is treated conservatively**: an undated item fails "older than" tests; an uncertain date uses
   its latest possible year for age and rights; a clipping with no date is treated as in copyright.
5. **Rights cutoff is computed, not hardcoded**: published in or before `today.year - 96` is public domain in the
   US (1931 from Jan 1, 2027). The decision records the cutoff used.
6. Redaction is orthogonal: it removes regions and text from what a feed carries; it never changes tier.
7. Every set, widen, narrow, policy change and feed change writes an audit row (who, what, old, new, why, when).

## Steps

1. Core types and `effective` with reasons; table-driven tests for every rule.
2. Policy kinds (workflow, age, content, rights), config parsing with clear errors for unknown keys.
3. `diff` effect preview; `set_visibility` writing a `person:<login>` assertion with the reason; PostgreSQL adapter on
   D10's schema (effective visibility can also be a SQL view for the publisher's queries, tested against the Python).
4. CLI: `athena library visibility explain <item>`, `set <path> <tier> --why`, `policies check` (prints the
   effect preview for a changed `library.toml` before it's applied).

## Acceptance criteria

- Every rule above has tests; a property test over random trees and policy sets checks that adding a policy never
  widens any node and removing one never narrows any node.
- `effective` returns readable reasons for every decision.
- The rights cutoff on 2026-12-31 is 1930 and on 2027-01-01 is 1931 (test with a fixed `today`).
- `policies check` on a changed config lists exactly the nodes whose tier changes.
- Audit rows are written for every change; widening rows are flagged.

## Test plan

pytest: table-driven rule tests, Hypothesis property tests (Hypothesis is MPL-2.0, a dev-only dependency, fine
with GPLv3), PostgreSQL adapter tests on D10's schema (skipped without a test database) with fixture items, a time-travel test for the rights cutoff.

## Privacy and rights

- [ ] Conservative on missing data (rule 4) and on uncertain rights.
- [ ] Policies are configuration; the example config marks every policy as an example for the club to choose,
      except `unreviewed-private`, a safety default the club may still turn off.
- [ ] Not legal advice: the rights policy implements the club's choice; the cutoff rule is documented with a
      pointer to D08.

## Touches

`athena/visibility/` (new), `athena/library/` (adapter, CLI), `docs/visibility.md` (rules and config reference),
`tests/test_visibility*.py`, `pyproject.toml` (dev: hypothesis).

## Done means

One function tells every part of Athena@Home what tier an item or page has and why, policies are config with an
effect preview, and every change is audited.
