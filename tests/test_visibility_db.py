# SPDX-License-Identifier: GPL-3.0-or-later
"""Visibility on a real library: settings with audit, widening reviews, policies from config, effect preview."""
import json
from datetime import date

import pytest
from fixtures.make_bundles import make_fixture_bundles
from test_library import CONFIG

from athena.library import Library, intake, records
from athena.library import config as config_mod
from athena.visibility import Policy, Tier
from athena.visibility.library import (confirm_review, decide_item, open_reviews, preview_policies, set_visibility,
                                       sync_policies)

TODAY = date(2026, 12, 1)
POLICIES = [
    {"name": "unreviewed-private", "kind": "workflow", "cap": "private", "when": {"status_not": "reviewed"}},
    {"name": "recent-members", "kind": "age", "cap": "members", "when": {"newer_than_years": 50}},
]
FEEDS = [{"name": "public", "tiers": ["public"]}, {"name": "members", "tiers": ["public", "members"]}]


def make(pg_url, root, policies=(), feeds=FEEDS):
    raw = json.loads(json.dumps(CONFIG))
    raw["policy"], raw["feed"] = list(policies), list(feeds)
    cfg = config_mod.from_dict(raw, env={"ATHENA_DATABASE_URL": pg_url, "ATHENA_FILES": str(root / "files"),
                                         "ATHENA_INTAKE": str(root / "intake")})
    lib = Library(cfg)
    lib.setup()
    return lib


@pytest.fixture
def lib(pg_url, tmp_path):
    lib = make(pg_url, tmp_path / "lib")
    make_fixture_bundles(lib.intake, tmp_path / "work")
    intake.scan(lib)
    return lib


def item_in(lib, series):
    with lib.connect() as conn:
        return conn.execute("SELECT id FROM item WHERE series = %s", (series,)).fetchone()["id"]


def tier(lib, item_id, today=TODAY):
    with lib.connect() as conn:
        return decide_item(conn, lib.cfg, item_id, today)


def test_everything_ingested_is_private_and_in_no_feed(lib):
    with lib.connect() as conn:
        ids = [r["id"] for r in conn.execute("SELECT id FROM item")]
    for i in ids:
        d = tier(lib, i)
        assert d.item.tier == Tier.PRIVATE and d.feeds == []
        assert all(p.tier == Tier.PRIVATE for p in d.pages.values())


def test_making_a_series_public_widens_with_a_reason_and_a_review(lib):
    news = item_in(lib, "newsletters")
    with lib.connect() as conn:
        with pytest.raises(ValueError, match="needs a reason"):
            set_visibility(conn, lib.name, "series", "newsletters", "public", "archivist")
        assert conn.execute("SELECT visibility FROM series WHERE slug = 'newsletters'").fetchone()["visibility"] is None
        r = set_visibility(conn, lib.name, "series", "newsletters", "public", "archivist", "board decision, Jan 5")
        assert r["widened"] and r["review"] and (r["before"], r["after"]) == (Tier.PRIVATE, Tier.PUBLIC)
        a = conn.execute("SELECT * FROM assertion WHERE id = %s", (r["assertion"],)).fetchone()
        assert a["source"] == "person:archivist" and a["value"]["why"] == "board decision, Jan 5"
        assert conn.execute("SELECT count(*) n FROM outbox WHERE payload->>'event' = 'visibility.changed'"
                            ).fetchone()["n"] == 1
        [rev] = open_reviews(conn)
        confirm_review(conn, rev["id"], "author")
        assert open_reviews(conn) == []
        with pytest.raises(KeyError):
            confirm_review(conn, rev["id"], "author")
    d = tier(lib, news)
    assert d.item.tier == Tier.PUBLIC and d.feeds == ["public", "members"]
    assert "series newsletters: public" in d.item.explain()
    assert tier(lib, item_in(lib, "clippings")).item.tier == Tier.PRIVATE   # other series untouched


def test_narrowing_needs_no_review(lib):
    with lib.connect() as conn:
        set_visibility(conn, lib.name, "series", "clippings", "public", "archivist", "ok")
        r = set_visibility(conn, lib.name, "item", item_in(lib, "clippings"), "members", "archivist")
        assert not r["widened"] and r["review"] is None
        with pytest.raises(ValueError, match="needs a reason"):    # back to inherit widens it again
            set_visibility(conn, lib.name, "item", item_in(lib, "clippings"), None, "archivist")
        r = set_visibility(conn, lib.name, "item", item_in(lib, "clippings"), None, "archivist", "as the series")
        assert r["widened"] and r["after"] == Tier.PUBLIC


def test_pages_can_be_narrower_and_all_private_pages_make_the_item_private(lib):
    news = item_in(lib, "newsletters")
    with lib.connect() as conn:
        set_visibility(conn, lib.name, "series", "newsletters", "public", "archivist", "ok")
        set_visibility(conn, lib.name, "page", f"{news}/2", "private", "archivist")
    d = tier(lib, news)
    assert d.item.tier == Tier.PUBLIC and d.pages[f"{news}/2"].tier == Tier.PRIVATE
    assert d.pages[f"{news}/1"].tier == Tier.PUBLIC
    with lib.connect() as conn:
        set_visibility(conn, lib.name, "page", f"{news}/1", "private", "archivist")
    assert tier(lib, news).item.tier == Tier.PRIVATE


def test_policies_cap_and_follow_review_status(pg_url, tmp_path):
    lib = make(pg_url, tmp_path / "lib", policies=POLICIES)
    make_fixture_bundles(lib.intake, tmp_path / "work")
    intake.scan(lib)
    news = item_in(lib, "newsletters")   # dated spring 1962
    with lib.connect() as conn:
        set_visibility(conn, lib.name, "series", "newsletters", "public", "archivist", "ok")
    d = tier(lib, news)
    assert d.item.tier == Tier.PRIVATE and d.item.policies == ["unreviewed-private"]
    with lib.connect() as conn:
        records.set_item(conn.cursor(), lib.name, news, {"status": "reviewed"}, "person:archivist")
    assert tier(lib, news).item.tier == Tier.PUBLIC                     # 1962 is older than 50 years
    with lib.connect() as conn:
        records.set_item(conn.cursor(), lib.name, news, {"date": "1990"}, "person:archivist")
    d = tier(lib, news)
    assert d.item.tier == Tier.MEMBERS and "recent-members" in d.item.explain()


def test_policies_are_mirrored_from_config(pg_url, tmp_path):
    lib = make(pg_url, tmp_path / "lib", policies=POLICIES)
    with lib.connect() as conn:
        rows = {r["name"]: r for r in conn.execute("SELECT * FROM policy")}
        assert set(rows) == {"unreviewed-private", "recent-members"} and rows["recent-members"]["cap"] == "members"
        assert sync_policies(conn, lib.cfg) == 0                          # unchanged config: nothing to do
    lib2 = make(pg_url, tmp_path / "lib", policies=POLICIES[:1])       # same database, one policy removed
    with lib2.connect() as conn:
        rows = {r["name"]: r["enabled"] for r in conn.execute("SELECT name, enabled FROM policy")}
        assert rows == {"unreviewed-private": True, "recent-members": False}
        removed = conn.execute("SELECT count(*) n FROM assertion WHERE predicate = 'policy.removed'").fetchone()["n"]
        assert removed == 1


def test_effect_preview_matches_the_decisions(lib):
    with lib.connect() as conn:
        for s in ("newsletters", "clippings"):
            set_visibility(conn, lib.name, "series", s, "public", "archivist", "ok")
        for r in conn.execute("SELECT id FROM item").fetchall():
            records.set_item(conn.cursor(), lib.name, r["id"], {"status": "reviewed"}, "person:archivist")
        new = [Policy.from_dict(p) for p in POLICIES]
        p = preview_policies(conn, [], new, TODAY)
    # the newsletter (1962) and the clipping (1958~) are older than 50 years; the unsorted photo item was private
    assert p.changes == [] and p.summary() == "no item changes audience"
    with lib.connect() as conn:
        records.set_item(conn.cursor(), lib.name, item_in(lib, "clippings"), {"date": "1999"}, "person:x")
        p = preview_policies(conn, [], new, TODAY)
    assert p.counts == {"public → members": 1}


def test_refusals(lib):
    with lib.connect() as conn:
        with pytest.raises(ValueError, match="nothing to inherit"):
            set_visibility(conn, lib.name, "collection", "archive", None, "archivist")
        with pytest.raises(KeyError):
            set_visibility(conn, lib.name, "series", "nope", "members", "archivist")
        with pytest.raises(ValueError, match="say who"):
            set_visibility(conn, lib.name, "series", "clippings", "members", "")
        with pytest.raises(ValueError, match="private, members or public"):
            set_visibility(conn, lib.name, "series", "clippings", "everyone", "archivist")
        with pytest.raises(ValueError, match="can't set"):
            set_visibility(conn, lib.name, "text", "1", "public", "archivist")


def test_cli_explain_set_reviews_check(lib, tmp_path, monkeypatch, capsys):
    from athena.__main__ import main

    cfg = tmp_path / "library.toml"
    cfg.write_text('[library]\nname = "testlib"\n[[collection]]\nslug = "archive"\ntitle = "A"\n'
                   '[[series]]\nslug = "newsletters"\ncollection = "archive"\n'
                   '[[series]]\nslug = "clippings"\ncollection = "archive"\n'
                   '[[feed]]\nname = "public"\ntiers = ["public"]\n')
    newer = tmp_path / "new.toml"
    newer.write_text(cfg.read_text() + '[[policy]]\nname = "unreviewed-private"\nkind = "workflow"\n'
                     'cap = "private"\nwhen = { status_not = "reviewed" }\n')
    monkeypatch.setenv("ATHENA_CONFIG", str(cfg))
    monkeypatch.setenv("ATHENA_DATABASE_URL", lib.cfg.database_url)
    monkeypatch.setenv("ATHENA_FILES", str(lib.cfg.files))
    monkeypatch.setenv("ATHENA_INTAKE", str(lib.cfg.intake))
    news = item_in(lib, "newsletters")
    assert main(["library", "set-visibility", "series", "newsletters", "public", "--as", "archivist"]) == 1
    assert main(["library", "set-visibility", "series", "newsletters", "public", "--as", "archivist",
                 "--why", "board said yes"]) == 0
    assert main(["library", "explain", news]) == 0
    assert main(["library", "reviews"]) == 0
    assert main(["library", "check-policies", str(newer)]) == 0
    out = capsys.readouterr()
    assert "needs a reason" in out.err
    assert "private -> public; widened, waiting for review" in out.out
    assert f"item {news}: public" in out.out and "feeds: public" in out.out
    assert "board said yes" in out.out and "1 item public → private" in out.out
