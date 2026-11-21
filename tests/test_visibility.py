# SPDX-License-Identifier: GPL-3.0-or-later
"""Visibility rules, pure: inheritance, policies only cap, pages never wider than items, conservative on unknowns."""
from datetime import date

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from athena.visibility import (Feed, ItemFacts, ItemInput, Level, Policy, PolicyError, Tier, diff, effective_item,
                               effective_page, feeds_for, pd_cutoff_year)

P, M, U = Tier.PRIVATE, Tier.MEMBERS, Tier.PUBLIC
TODAY = date(2026, 12, 1)


def levels(coll=None, series=None, item=None, with_series=True):
    out = [Level("collection", "archive", coll)]
    if with_series:
        out.append(Level("series", "newsletters", series))
    out.append(Level("item", "i1", item))
    return out


def policy(**d):
    d.setdefault("name", "p")
    return Policy.from_dict(d)


WORKFLOW = policy(name="unreviewed-private", kind="workflow", cap="private", when={"status_not": "reviewed"})
AGE50 = policy(name="recent-members", kind="age", cap="members", when={"newer_than_years": 50})
DIRECTORY = policy(name="directories", kind="content", cap="members", when={"type_in": ["directory"]})
RIGHTS = policy(name="clippings-in-copyright", kind="rights", cap="members",
                when={"type_in": ["clipping"], "published_after_pd_cutoff": True})
REVIEWED = ItemFacts(status="reviewed")


# --- inheritance ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("lv, want", [
    (levels(), P),                                   # private by default
    (levels(coll=P, series=U), U),                   # a series of a private collection made public
    (levels(coll=P, series=U, item=M), M),           # an item narrower than its series
    (levels(coll=P, series=M, item=U), U),           # an item wider than its series: allowed (and reviewed)
    (levels(coll=U), U),
    (levels(coll=U, series=P), P),
    (levels(coll=U, with_series=False), U),          # unsorted item inherits its collection
    ([Level("item", "i1", None)], P),                # no collection at all: private
])
def test_inheritance(lv, want):
    assert effective_item(lv, REVIEWED, [], TODAY).tier == want


def test_reasons_say_why():
    d = effective_item(levels(series=U), ItemFacts(status="new", latest=date(1990, 12, 31)), [WORKFLOW, AGE50], TODAY)
    text = d.explain()
    assert "collection archive: private (default)" in text and "series newsletters: public" in text
    assert "policy unreviewed-private caps to private (status is new)" in text and text.endswith("→ private")
    assert d.policies == ["unreviewed-private"]


# --- policies ------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("facts, want", [
    (ItemFacts(status="reviewed", latest=date(1980, 12, 31)), M),     # within 50 years of 2026
    (ItemFacts(status="reviewed", latest=date(1976, 12, 31)), U),     # exactly 50 years: not newer
    (ItemFacts(status="reviewed", latest=date(1970, 6, 30)), U),
    (ItemFacts(status="reviewed", latest=None), M),                    # undated counts as recent
])
def test_age_policy(facts, want):
    assert effective_item(levels(series=U), facts, [AGE50], TODAY).tier == want


def test_workflow_and_content_policies():
    lv = levels(series=U)
    assert effective_item(lv, ItemFacts(status="new"), [WORKFLOW], TODAY).tier == P
    assert effective_item(lv, ItemFacts(status="reviewed"), [WORKFLOW], TODAY).tier == U
    assert effective_item(lv, ItemFacts(status="reviewed", type="directory"), [DIRECTORY], TODAY).tier == M
    tagged = policy(kind="content", cap="members", when={"tags_any": ["contact-details"]})
    assert effective_item(lv, ItemFacts(status="reviewed", tags=("contact-details", "x")), [tagged], TODAY).tier == M
    assert effective_item(lv, ItemFacts(status="reviewed", tags=("x",)), [tagged], TODAY).tier == U


def test_conditions_combine_with_and():
    lv = levels(series=U)
    news_1990 = ItemFacts(status="reviewed", type="newsletter", latest=date(1990, 1, 1))
    assert effective_item(lv, news_1990, [RIGHTS], TODAY).tier == U       # in copyright, but not a clipping


@pytest.mark.parametrize("today, cutoff, clipping_1931", [(date(2026, 12, 31), 1930, M), (date(2027, 1, 1), 1931, U)])
def test_rights_cutoff_moves_each_january(today, cutoff, clipping_1931):
    assert pd_cutoff_year(today) == cutoff
    facts = ItemFacts(status="reviewed", type="clipping", latest=date(1931, 12, 31))
    d = effective_item(levels(series=U), facts, [RIGHTS], today)
    assert d.tier == clipping_1931 and d.cutoff_year == cutoff


def test_undated_clipping_is_treated_as_in_copyright():
    d = effective_item(levels(series=U), ItemFacts(status="reviewed", type="clipping"), [RIGHTS], TODAY)
    assert d.tier == M and "treated as in copyright" in d.explain()


def test_a_policy_never_widens():
    assert effective_item(levels(), ItemFacts(status="new"), [AGE50, DIRECTORY], TODAY).tier == P


def test_disabled_policies_are_ignored():
    off = Policy("off", "workflow", P, {"status_not": "reviewed"}, enabled=False)
    assert effective_item(levels(series=U), ItemFacts(status="new"), [off], TODAY).tier == U


# --- pages ---------------------------------------------------------------------------------------------------

def test_pages_inherit_narrow_and_never_widen():
    item = effective_item(levels(series=M), REVIEWED, [], TODAY)
    assert effective_page(item, Level("page", "i1/1", None)).tier == M
    assert effective_page(item, Level("page", "i1/1", P)).tier == P
    wide = effective_page(item, Level("page", "i1/1", U))
    assert wide.tier == M and "never wider than its item" in wide.explain()


def test_an_item_whose_pages_are_all_private_is_private():
    assert effective_item(levels(series=U), REVIEWED, [], TODAY, page_settings=[P, P]).tier == P
    assert effective_item(levels(series=U), REVIEWED, [], TODAY, page_settings=[P, None]).tier == U


# --- feeds ---------------------------------------------------------------------------------------------------

def test_feeds():
    feeds = [Feed.from_dict({"name": "public", "tiers": ["public"]}),
             Feed.from_dict({"name": "members", "tiers": ["public", "members"]})]
    assert feeds_for(U, feeds) == ["public", "members"]
    assert feeds_for(M, feeds) == ["members"] and feeds_for(P, feeds) == []


@pytest.mark.parametrize("d, match", [
    ({"name": "x", "tiers": ["private"]}, "never carries private"),
    ({"name": "x", "tiers": []}, "list the tiers"),
    ({"name": "x", "tiers": ["everyone"]}, "private, members or public"),
])
def test_feed_errors(d, match):
    with pytest.raises(PolicyError, match=match):
        Feed.from_dict(d)


# --- policy config errors ------------------------------------------------------------------------------------

@pytest.mark.parametrize("d, match", [
    ({"kind": "age", "cap": "public", "when": {"newer_than_years": 5}}, "only narrow"),
    ({"kind": "mood", "cap": "members", "when": {}}, "kind must be"),
    ({"kind": "age", "cap": "members", "when": {"newer_than": 5}}, "unknown conditions"),
    ({"kind": "rights", "cap": "members", "when": {"type_in": ["clipping"]}}, "needs published_after_pd_cutoff"),
    ({"kind": "age", "cap": "members", "when": {"newer_than_years": "fifty"}}, "whole number"),
    ({"kind": "content", "cap": "members", "when": {"type_in": "directory"}}, "list of strings"),
    ({"kind": "age", "cap": "members", "when": {"newer_than_years": 5}, "extra": 1}, "unknown keys"),
])
def test_policy_errors(d, match):
    with pytest.raises(PolicyError, match=match):
        policy(**d)


# --- effect preview ------------------------------------------------------------------------------------------

def test_diff_counts_changes():
    items = [ItemInput(f"i{y}", tuple(levels(series=U)), ItemFacts(status="reviewed", latest=date(y, 1, 1)))
             for y in (1950, 1985, 1990, 2000)]
    p = diff(items, [], [AGE50], TODAY)
    assert [c.item for c in p.changes] == ["i1985", "i1990", "i2000"]
    assert p.counts == {"public → members": 3} and p.summary() == "3 items public → members"
    assert diff(items, [AGE50], [AGE50], TODAY).summary() == "no item changes audience"


# --- properties ----------------------------------------------------------------------------------------------

tiers = st.sampled_from([None, P, M, U])
facts_st = st.builds(
    ItemFacts,
    status=st.sampled_from(["new", "ocr_done", "reviewed"]),
    type=st.sampled_from([None, "newsletter", "clipping", "directory", "photograph"]),
    tags=st.lists(st.sampled_from(["contact-details", "flower-show", "board"]), max_size=3).map(tuple),
    series=st.sampled_from([None, "newsletters", "clippings"]),
    latest=st.one_of(st.none(), st.dates(min_value=date(1900, 1, 1), max_value=date(2030, 12, 31))),
)
policy_st = st.sampled_from([
    WORKFLOW, AGE50, DIRECTORY, RIGHTS,
    policy(name="young", kind="age", cap="private", when={"newer_than_years": 10}),
    policy(name="board", kind="content", cap="private", when={"tags_any": ["board"]}),
    policy(name="clips", kind="content", cap="members", when={"series_in": ["clippings"]}),
])
level_st = st.tuples(tiers, tiers, tiers, st.booleans()).map(
    lambda t: levels(coll=t[0], series=t[1], item=t[2], with_series=t[3]))
today_st = st.dates(min_value=date(2020, 1, 1), max_value=date(2035, 12, 31))


@settings(max_examples=400, deadline=None)
@given(lv=level_st, facts=facts_st, base=st.lists(policy_st, max_size=4), extra=policy_st, today=today_st,
       pages=st.lists(tiers, max_size=3))
def test_adding_a_policy_never_widens_and_removing_never_narrows(lv, facts, base, extra, today, pages):
    without = effective_item(lv, facts, base, today, pages).tier
    with_extra = effective_item(lv, facts, base + [extra], today, pages).tier
    assert with_extra <= without


@settings(max_examples=300, deadline=None)
@given(lv=level_st, facts=facts_st, ps=st.lists(policy_st, max_size=4), today=today_st, page=tiers)
def test_a_page_is_never_wider_than_its_item(lv, facts, ps, today, page):
    item = effective_item(lv, facts, ps, today)
    assert effective_page(item, Level("page", "x/1", page)).tier <= item.tier


@settings(max_examples=300, deadline=None)
@given(lv=level_st, facts=facts_st, ps=st.lists(policy_st, max_size=4), today=today_st)
def test_effective_is_never_wider_than_the_settings(lv, facts, ps, today):
    no_policies = effective_item(lv, facts, [], today).tier
    assert effective_item(lv, facts, ps, today).tier <= no_policies
