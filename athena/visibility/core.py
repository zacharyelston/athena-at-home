# SPDX-License-Identifier: GPL-3.0-or-later
"""Effective visibility: what audience an item or page has, and why. Pure logic, no I/O.

Rules (docs/visibility.md; portal.md):

1. Tiers are ordered private < members < public. Everything is private by default: a collection with no setting
   is private, and series, items and pages with no setting inherit from their parent.
2. A setting may be wider than its parent's (that's how a series of a private collection becomes public), but
   widening is logged and reviewed by the caller (athena.visibility.library).
3. **Policies only cap.** Effective tier = min(own-or-inherited setting, every matching policy's cap).
4. A page can be narrower than its item, never wider; an item whose pages are all private is private.
5. Unknown data is treated conservatively: an undated item counts as recent, a date's latest possible year is
   used, and a clipping with no date is treated as still in copyright.
6. The US public-domain cutoff is computed from ``today``, never hardcoded: published in or before
   ``today.year - 96`` (1931 from Jan 1, 2027).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import IntEnum
from typing import Iterable


class Tier(IntEnum):
    PRIVATE = 0
    MEMBERS = 1
    PUBLIC = 2

    @classmethod
    def parse(cls, value: "str | Tier | None") -> "Tier | None":
        if value is None or isinstance(value, Tier):
            return value
        try:
            return cls[str(value).upper()]
        except KeyError:
            raise ValueError(f"visibility must be private, members or public, not {value!r}") from None

    def __str__(self) -> str:
        return self.name.lower()


def pd_cutoff_year(today: date) -> int:
    """US works published in or before this year are in the public domain (95-year term, entering each Jan 1)."""
    return today.year - 96


# --- policies --------------------------------------------------------------------------------------------------

CONDITIONS = {"status_not", "status_in", "type_in", "tags_any", "series_in", "newer_than_years",
              "published_after_pd_cutoff"}
REQUIRED = {
    "workflow": ({"status_not", "status_in"}, "status_not or status_in"),
    "age": ({"newer_than_years"}, "newer_than_years"),
    "content": ({"type_in", "tags_any", "series_in"}, "type_in, tags_any or series_in"),
    "rights": ({"published_after_pd_cutoff"}, "published_after_pd_cutoff"),
}


class PolicyError(ValueError):
    pass


@dataclass(frozen=True)
class Policy:
    """A cap on items matching every condition in ``when``. The deployment's choice, written in library.toml."""
    name: str
    kind: str
    cap: Tier
    when: dict
    enabled: bool = True

    @classmethod
    def from_dict(cls, d: dict) -> "Policy":
        unknown = set(d) - {"name", "kind", "cap", "when", "enabled", "description"}
        if unknown:
            raise PolicyError(f"policy {d.get('name')!r}: unknown keys {sorted(unknown)}")
        name, kind = d.get("name"), d.get("kind")
        if not name or not isinstance(name, str):
            raise PolicyError("a policy needs a name")
        if kind not in REQUIRED:
            raise PolicyError(f"policy {name!r}: kind must be one of {sorted(REQUIRED)}, not {kind!r}")
        try:
            cap = Tier.parse(d.get("cap"))
        except ValueError as e:
            raise PolicyError(f"policy {name!r}: {e}") from None
        if cap is None or cap == Tier.PUBLIC:
            raise PolicyError(f"policy {name!r}: cap must be private or members (a policy can only narrow)")
        when = dict(d.get("when") or {})
        bad = set(when) - CONDITIONS
        if bad:
            raise PolicyError(f"policy {name!r}: unknown conditions {sorted(bad)}; use {sorted(CONDITIONS)}")
        need, label = REQUIRED[kind]
        if not need & set(when):
            raise PolicyError(f"policy {name!r}: a {kind} policy needs {label}")
        if "newer_than_years" in when and (not isinstance(when["newer_than_years"], int)
                                           or when["newer_than_years"] < 0):
            raise PolicyError(f"policy {name!r}: newer_than_years must be a whole number of years")
        for key in ("status_in", "type_in", "tags_any", "series_in"):
            if key in when and not (isinstance(when[key], list) and all(isinstance(x, str) for x in when[key])):
                raise PolicyError(f"policy {name!r}: {key} must be a list of strings")
        return cls(name, kind, cap, when, bool(d.get("enabled", True)))


@dataclass(frozen=True)
class ItemFacts:
    """What policies look at."""
    status: str = "new"
    type: str | None = None
    tags: tuple[str, ...] = ()
    series: str | None = None
    latest: date | None = None          # latest possible date (EDTF); None = undated


def _match(p: Policy, facts: ItemFacts, today: date) -> tuple[bool, str]:
    """Whether every condition holds, and a readable why."""
    why = []
    w = p.when
    if "status_not" in w:
        if facts.status == w["status_not"]:
            return False, ""
        why.append(f"status is {facts.status}")
    if "status_in" in w:
        if facts.status not in w["status_in"]:
            return False, ""
        why.append(f"status is {facts.status}")
    if "type_in" in w:
        if facts.type not in w["type_in"]:
            return False, ""
        why.append(f"type is {facts.type}")
    if "tags_any" in w:
        hit = sorted(set(facts.tags) & set(w["tags_any"]))
        if not hit:
            return False, ""
        why.append(f"tagged {', '.join(hit)}")
    if "series_in" in w:
        if facts.series not in w["series_in"]:
            return False, ""
        why.append(f"in series {facts.series}")
    if "newer_than_years" in w:
        threshold = today.year - w["newer_than_years"]
        if facts.latest is not None and facts.latest.year <= threshold:
            return False, ""
        why.append("undated, so treated as recent" if facts.latest is None
                   else f"latest date {facts.latest.year} is within {w['newer_than_years']} years")
    if w.get("published_after_pd_cutoff"):
        cutoff = pd_cutoff_year(today)
        if facts.latest is not None and facts.latest.year <= cutoff:
            return False, ""
        why.append("undated, so treated as in copyright" if facts.latest is None
                   else f"latest date {facts.latest.year} is after the public-domain cutoff {cutoff}")
    return True, "; ".join(why)


# --- effective visibility --------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Level:
    """One level of the hierarchy: its kind, id and own setting (None = inherit)."""
    kind: str               # collection | series | item | page
    id: str
    setting: Tier | None


@dataclass
class Decision:
    tier: Tier
    reasons: list[str] = field(default_factory=list)
    policies: list[str] = field(default_factory=list)     # names of the policies that capped it
    cutoff_year: int | None = None

    def explain(self) -> str:
        return " → ".join(self.reasons + [str(self.tier)])


def _inherited(levels: list[Level]) -> tuple[Tier, list[str]]:
    """The nearest explicit setting walking up from the last level; a collection with none is private."""
    reasons = []
    tier = None
    for lv in levels:                       # root first
        if lv.setting is not None:
            tier = lv.setting
            reasons.append(f"{lv.kind} {lv.id}: {lv.setting}")
        elif lv.kind == "collection":
            tier = Tier.PRIVATE
            reasons.append(f"collection {lv.id}: private (default)")
    if tier is None:                        # an unsorted item with no collection
        tier = Tier.PRIVATE
        reasons.insert(0, "no collection: private (default)")
    return tier, reasons


def effective_item(levels: list[Level], facts: ItemFacts, policies: Iterable[Policy], today: date,
                   page_settings: Iterable[Tier | None] = ()) -> Decision:
    """Effective tier of an item. ``levels``: collection (if any), series (if any), then the item itself."""
    tier, reasons = _inherited(levels)
    d = Decision(tier, reasons)
    for p in policies:
        if not p.enabled:
            continue
        matched, why = _match(p, facts, today)
        if p.when.get("published_after_pd_cutoff"):
            d.cutoff_year = pd_cutoff_year(today)
        if matched and p.cap < d.tier:
            d.tier = p.cap
            d.policies.append(p.name)
            d.reasons.append(f"policy {p.name} caps to {p.cap} ({why})")
    pages = list(page_settings)
    if pages and d.tier > Tier.PRIVATE and all(s == Tier.PRIVATE for s in pages):
        d.tier = Tier.PRIVATE
        d.reasons.append("every page is private")
    return d


def effective_page(item: Decision, page: Level) -> Decision:
    """A page takes its own setting or its item's, and is never wider than its item."""
    if page.setting is None:
        return Decision(item.tier, item.reasons + [f"page {page.id}: inherits"], list(item.policies),
                        item.cutoff_year)
    if page.setting > item.tier:
        return Decision(item.tier, item.reasons + [f"page {page.id}: {page.setting}, but never wider than its item"],
                        list(item.policies), item.cutoff_year)
    return Decision(page.setting, item.reasons + [f"page {page.id}: {page.setting}"], list(item.policies),
                    item.cutoff_year)


# --- feeds -----------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Feed:
    name: str
    tiers: frozenset[Tier]

    @classmethod
    def from_dict(cls, d: dict) -> "Feed":
        name = d.get("name")
        if not name or not isinstance(name, str):
            raise PolicyError("a feed needs a name")
        try:
            tiers = frozenset(Tier.parse(t) for t in d.get("tiers") or [])
        except ValueError as e:
            raise PolicyError(f"feed {name!r}: {e}") from None
        if not tiers:
            raise PolicyError(f"feed {name!r}: list the tiers it carries")
        if Tier.PRIVATE in tiers:
            raise PolicyError(f"feed {name!r}: a feed never carries private material")
        return cls(name, tiers)


def feeds_for(tier: Tier, feeds: Iterable[Feed]) -> list[str]:
    return [f.name for f in feeds if tier in f.tiers]


# --- effect preview --------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class ItemInput:
    """Everything needed to decide one item: its levels (collection, series, item), facts and page settings."""
    id: str
    levels: tuple[Level, ...]
    facts: ItemFacts
    page_settings: tuple[Tier | None, ...] = ()


@dataclass
class Change:
    item: str
    before: Tier
    after: Tier


@dataclass
class Preview:
    changes: list[Change]
    counts: dict[str, int]          # "public → members": n

    def summary(self) -> str:
        if not self.changes:
            return "no item changes audience"
        return "; ".join(f"{n} item{'s' if n != 1 else ''} {k}" for k, n in sorted(self.counts.items()))


def diff(items: Iterable[ItemInput], before: Iterable[Policy], after: Iterable[Policy], today: date) -> Preview:
    """What changing the policies from ``before`` to ``after`` does to each item's effective tier."""
    before, after = list(before), list(after)
    changes, counts = [], {}
    for it in items:
        b = effective_item(list(it.levels), it.facts, before, today, it.page_settings).tier
        a = effective_item(list(it.levels), it.facts, after, today, it.page_settings).tier
        if a != b:
            changes.append(Change(it.id, b, a))
            key = f"{b} → {a}"
            counts[key] = counts.get(key, 0) + 1
    return Preview(changes, counts)
