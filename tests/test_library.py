# SPDX-License-Identifier: GPL-3.0-or-later
"""The library core against a real PostgreSQL: setup, intake, provenance, OCR, search, events."""
import asyncio
import json
import os
import shutil
import stat
import threading
from pathlib import Path

import psycopg
import pytest
from conftest import FakeEngine, nats_server_running
from fixtures.make_bundles import draw_page, example_items, make_bundle, make_fixture_bundles

from athena.library import Library
from athena.library import config as config_mod
from athena.library import intake, ocr, outbox, records, search
from athena.library.store import FileStore, StoreError

CONFIG = {
    "library": {"name": "testlib"},
    "collection": [{"slug": "archive", "title": "The archive"}],
    "series": [{"slug": "newsletters", "collection": "archive", "title": "Newsletters"},
               {"slug": "clippings", "collection": "archive", "title": "Clippings"}],
    # "photographs" is deliberately not defined, so that fixture item lands in "unsorted"
    "vocabulary": {"types": ["newsletter", "clipping", "photograph"]},
}


def make_library(pg_url, root: Path, name="testlib") -> Library:
    raw = json.loads(json.dumps(CONFIG))
    raw["library"]["name"] = name
    cfg = config_mod.from_dict(raw, env={"ATHENA_DATABASE_URL": pg_url, "ATHENA_FILES": str(root / "files"),
                                         "ATHENA_INTAKE": str(root / "intake")})
    lib = Library(cfg)
    lib.setup()
    return lib


@pytest.fixture
def lib(pg_url, tmp_path):
    return make_library(pg_url, tmp_path / "lib")


@pytest.fixture
def ingested(lib, tmp_path):
    bundles = make_fixture_bundles(lib.intake, tmp_path / "work")
    results = intake.scan(lib)
    return bundles, results


def rows(lib, sql, *args):
    with lib.connect() as conn:
        return conn.execute(sql, args).fetchall()


class FakeClient:
    """Stands in for SieveClient: returns a reply shaped like Sieve's, or fails for chosen calls."""

    def __init__(self, text="# Page\nflower show volunteers", fail_on=()):
        self.text, self.fail_on, self.calls = text, set(fail_on), 0

    def ocr(self, image, **kw):
        from sieve.client import SieveError

        self.calls += 1
        if self.calls in self.fail_on:
            raise SieveError("worker said no")
        return {"text": self.text, "model": "fake/deepseek-ocr-2", "worker": "test", "tiles": 0, "looped": False,
                "hit_cap": False, "tier0": {"text": "quick text", "chars": 10}}


# --- setup and named instances ---------------------------------------------------------------------------------

def test_setup_is_idempotent_and_seeds_the_arrangement(lib):
    assert lib.setup() == []   # already applied
    assert {r["slug"] for r in rows(lib, "SELECT slug FROM series")} == {"newsletters", "clippings"}
    [c] = rows(lib, "SELECT visibility FROM collection")
    assert c["visibility"] == "private"
    seeded = rows(lib, "SELECT count(*) AS n FROM assertion WHERE source = 'config'")[0]["n"]
    lib.setup()
    assert rows(lib, "SELECT count(*) AS n FROM assertion WHERE source = 'config'")[0]["n"] == seeded


def test_two_named_instances_share_nothing(pg_admin_url, tmp_path):
    import uuid

    urls = []
    for i in range(2):
        name = f"athena_two_{uuid.uuid4().hex[:8]}"
        with psycopg.connect(pg_admin_url, autocommit=True) as c:
            c.execute(f'CREATE DATABASE "{name}"')
        urls.append(psycopg.conninfo.make_conninfo(pg_admin_url, dbname=name))
    a = make_library(urls[0], tmp_path / "a", name="alpha")
    b = make_library(urls[1], tmp_path / "b", name="beta")
    make_fixture_bundles(a.intake, tmp_path / "work")
    intake.scan(a)
    assert rows(a, "SELECT count(*) AS n FROM item")[0]["n"] == 3
    assert rows(b, "SELECT count(*) AS n FROM item")[0]["n"] == 0
    assert not any((tmp_path / "b" / "files").rglob("*.jpg"))
    subjects = {r["subject"] for r in rows(a, "SELECT subject FROM outbox")}
    assert subjects and all(s.startswith("library.alpha.") for s in subjects)


# --- intake ----------------------------------------------------------------------------------------------------

def test_intake_stores_items_pages_files_and_text(lib, ingested):
    bundles, results = ingested
    assert [r.status for r in results] == ["ingested"] * 3
    assert all(r.moved_to and r.moved_to.parent.name == "ingested" for r in results)
    assert rows(lib, "SELECT count(*) AS n FROM page")[0]["n"] == 5
    for p in rows(lib, "SELECT * FROM page"):
        orig = lib.store.path_for(p["original_sha256"], p["original_ext"])
        assert orig.is_file() and not (orig.stat().st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))
        assert lib.store.path_for(p["page_sha256"], p["page_ext"]).is_file()
        if p["kind"] == "photo":
            assert p["ocr_sha256"] is None      # photos aren't OCR'd
        else:
            assert lib.store.derivative_path("ocr", p["ocr_sha256"], "jpg").is_file()
    tier0 = rows(lib, "SELECT count(*) AS n FROM text WHERE tier = 0")[0]["n"]
    assert tier0 == 3   # the text pages that came with Apple Vision lines
    [news] = rows(lib, "SELECT * FROM item WHERE series = 'newsletters'")
    assert news["title"] == "Newsletter, spring issue" and news["date_edtf"] == "1962-21"
    assert str(news["date_earliest"]) == "1962-03-01" and news["status"] == "new"
    assert news["tags"] == ["flower-show"] and news["physical"]["format"] == "mimeograph"


def test_unknown_series_lands_in_unsorted_with_a_warning(lib, ingested):
    [photo] = rows(lib, "SELECT * FROM item WHERE claimed_series = 'photographs'")
    assert photo["series"] is None and photo["collection"] == "archive"
    [cap] = rows(lib, "SELECT warnings FROM capture WHERE item = %s", photo["id"])
    assert any("photographs" in w for w in cap["warnings"])


def test_reingesting_a_bundle_is_a_no_op(lib, ingested):
    _, results = ingested
    shutil.copytree(results[0].moved_to, lib.intake / results[0].bundle.name)
    before = rows(lib, "SELECT (SELECT count(*) FROM assertion) a, (SELECT count(*) FROM page) p")[0]
    [again] = intake.scan(lib)
    assert again.status == "duplicate"
    assert rows(lib, "SELECT (SELECT count(*) FROM assertion) a, (SELECT count(*) FROM page) p")[0] == before


def test_corrupt_bundle_is_rejected_and_nothing_is_stored(lib, tmp_path):
    b = make_bundle(lib.intake, example_items()[1], tmp_path / "work")
    raw_sha = json.loads((b / "manifest.json").read_text())["files"]["raw/001.jpg"]["sha256"]
    (b / "raw/001.jpg").write_bytes(b"tampered")
    [r] = intake.scan(lib)
    assert r.status == "rejected" and ("sha256" in r.reason or "size" in r.reason)
    assert (lib.intake / "rejected" / b.name).is_dir()
    assert (lib.intake / "rejected" / f"{b.name}.reason.txt").read_text()
    assert rows(lib, "SELECT count(*) AS n FROM item")[0]["n"] == 0
    assert not any(lib.store.root.rglob(f"{raw_sha}.*"))


def test_partial_and_hidden_directories_are_left_alone(lib, tmp_path):
    b = make_bundle(lib.intake, example_items()[1], tmp_path / "work")
    b.rename(b.with_name(b.name + ".partial"))
    (lib.intake / ".hidden").mkdir()
    assert intake.scan(lib) == []


def test_everything_starts_private(lib, ingested):
    assert all(r["visibility"] is None for r in rows(lib, "SELECT visibility FROM item"))
    assert all(r["visibility"] is None for r in rows(lib, "SELECT visibility FROM page"))
    assert all(r["visibility"] is None for r in rows(lib, "SELECT visibility FROM series"))
    assert {r["visibility"] for r in rows(lib, "SELECT visibility FROM collection")} == {"private"}


# --- provenance ------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("sql", ["UPDATE assertion SET source = 'x'", "DELETE FROM assertion",
                                 "TRUNCATE assertion CASCADE", "UPDATE text SET plain = 'x'", "DELETE FROM text"])
def test_assertions_and_text_are_append_only(lib, ingested, sql):
    with lib.connect() as conn, pytest.raises(psycopg.errors.InsufficientPrivilege, match="append-only"):
        conn.execute(sql)


def test_current_state_rebuilds_from_assertions(lib, ingested):
    with lib.connect() as conn:
        cur = conn.cursor()
        item_id = cur.execute("SELECT id FROM item WHERE series = 'newsletters'").fetchone()["id"]
        changed = records.set_item(cur, lib.name, item_id, {"title": "Spring newsletter", "date": "1962-04"},
                                   "person:archivist")
        assert changed == ["title", "date"]
        assert records.set_item(cur, lib.name, item_id, {"title": "Spring newsletter"}, "person:archivist") == []
        for r in cur.execute("SELECT id FROM item").fetchall():
            assert records.rebuild_item_fields(cur, r["id"]) == records.item_fields(cur, r["id"])
        sources = [r["source"] for r in cur.execute(
            "SELECT source FROM assertion WHERE subject_id = %s AND predicate = 'item.title' ORDER BY id", (item_id,))]
        assert sources == ["capture:test-desk", "person:archivist"]


def test_a_bad_date_changes_nothing(lib, ingested):
    from athena.bundle import EdtfError

    with lib.connect() as conn:
        cur = conn.cursor()
        item_id = cur.execute("SELECT id FROM item LIMIT 1").fetchone()["id"]
        n = cur.execute("SELECT count(*) AS n FROM assertion").fetchone()["n"]
        with pytest.raises(EdtfError):
            records.set_item(cur, lib.name, item_id, {"title": "New", "date": "spring 1962"}, "person:x")
        assert cur.execute("SELECT count(*) AS n FROM assertion").fetchone()["n"] == n


# --- events ----------------------------------------------------------------------------------------------------

def test_events_carry_ids_only(lib, ingested):
    events = rows(lib, "SELECT subject, payload FROM outbox ORDER BY id")
    assert {e["payload"]["event"] for e in events} >= {"item.created", "item.described"}
    titles = {r["title"] for r in rows(lib, "SELECT title FROM item")}
    for e in events:
        assert set(e["payload"]) <= {"event", "library", "item", "fields"}
        assert not any(t and t in json.dumps(e["payload"]) for t in titles)


def test_outbox_publishes_once_to_jetstream(lib, ingested):
    with nats_server_running(1024 * 1024, jetstream=True) as url:
        async def go():
            import nats
            nc = await nats.connect(url)
            js = nc.jetstream()
            await outbox.ensure_stream(js, lib.name)
            sent = await outbox.publish_pending(lib, js)
            again = await outbox.publish_pending(lib, js)
            # a crash after publishing but before marking: the row is published again, JetStream drops it
            with lib.connect() as conn:
                conn.execute("UPDATE outbox SET published_at = NULL WHERE id = (SELECT min(id) FROM outbox)")
            redo = await outbox.publish_pending(lib, js)
            info = await js.stream_info(outbox.stream_name(lib.name))
            await nc.drain()
            return sent, again, redo, info.state.messages
        sent, again, redo, in_stream = asyncio.run(go())
    total = rows(lib, "SELECT count(*) AS n FROM outbox")[0]["n"]
    assert sent == total and again == 0 and redo == 1 and in_stream == total
    assert rows(lib, "SELECT count(*) AS n FROM outbox WHERE published_at IS NULL")[0]["n"] == 0


# --- OCR -------------------------------------------------------------------------------------------------------

def test_ocr_via_page_stores_text_and_marks_items_done(lib, ingested):
    client = FakeClient()
    run = ocr.run_via_page(lib, client)
    ocrable = rows(lib, "SELECT count(*) AS n FROM page WHERE ocr_sha256 IS NOT NULL")[0]["n"]
    assert run.done == ocrable == client.calls and run.failed == 0
    assert len(run.items_ocr_done) == 3
    t = rows(lib, "SELECT * FROM text WHERE tier = 1 LIMIT 1")[0]
    assert t["engine"] == "fake/deepseek-ocr-2" and t["source"] == "ocr:fake/deepseek-ocr-2"
    assert ocr.run_via_page(lib, client).done == 0          # resumable: nothing left to do
    assert {r["status"] for r in rows(lib, "SELECT status FROM item")} == {"ocr_done"}
    assert rows(lib, "SELECT count(*) AS n FROM outbox WHERE payload->>'event' = 'item.ocr_done'")[0]["n"] == 3


def test_ocr_failure_is_recorded_and_the_rest_continue(lib, ingested):
    run = ocr.run_via_page(lib, FakeClient(fail_on={1}))
    assert run.failed == 1 and run.done >= 1
    [f] = rows(lib, "SELECT * FROM ocr_failure")
    assert "worker said no" in f["reason"]
    assert ocr.run_via_page(lib, FakeClient()).done == 0     # the failed page waits for review, not a blind retry
    assert len(rows(lib, "SELECT id FROM item WHERE status = 'new'")) == 1


def test_ocr_through_a_real_sieve_worker_over_nats(lib, ingested):
    from sieve.client import SieveClient
    from sieve.worker import serve

    engine = FakeEngine(text="Flower show results\n\nThe town green")
    with nats_server_running(8 * 1024 * 1024) as url:
        started = threading.Event()

        def worker():
            async def main():
                ready = asyncio.Event()
                task = asyncio.create_task(serve(url, "ocr.page", "sieve", engine.model_id, engine=engine,
                                                 ready=ready))
                await ready.wait()
                started.set()
                await task
            asyncio.run(main())

        threading.Thread(target=worker, daemon=True).start()
        assert started.wait(10)
        run = ocr.run_via_page(lib, SieveClient(url, timeout_s=10))
    assert run.done == len(engine.calls) > 0 and run.failed == 0
    assert all(c["size"][0] <= lib.cfg.ocr_max_edge for c in engine.calls)


# --- search ----------------------------------------------------------------------------------------------------

def test_search_finds_ocr_text_titles_and_near_misses(lib, ingested):
    ocr.run_via_page(lib, FakeClient(text="Report of the dahlia committee"))
    with lib.connect() as conn:
        hits = search.search(conn, "dahlia committee")
        assert len(hits) == 3 and all(h.pages for h in hits)
        [h] = search.search(conn, "town green planting")
        assert h.title.startswith("Article on the town green")
        assert search.search(conn, "Newsleter spring")[0].title == "Newsletter, spring issue"   # trigram
        assert search.search(conn, "zeppelin") == [] and search.search(conn, "  ") == []


# --- file store ------------------------------------------------------------------------------------------------

def test_file_store_is_write_once(tmp_path):
    store = FileStore(tmp_path / "files")
    src = tmp_path / "a.jpg"
    draw_page(src, ["x"])
    sha = store.put(src, "jpg")
    path = store.path_for(sha, "jpg")
    assert store.put(src, "jpg") == sha                       # same content again: no-op
    assert oct(path.stat().st_mode & 0o777) == "0o444"
    with pytest.raises(StoreError, match="doesn't match"):
        store.put(src, "png", expect_sha256="0" * 64)
    assert not store.path_for("0" * 64, "png").exists() and not store.path_for(sha, "png").exists()
    assert not [p for p in (tmp_path / "files").rglob(".incoming-*")]
    assert not any(hasattr(store, name) for name in ("delete", "remove", "unlink", "overwrite"))
    with pytest.raises(StoreError):
        store.path_for("../../etc/passwd", "jpg")


def test_ocr_derivative_is_sized_for_ocr(tmp_path):
    page = tmp_path / "p.png"
    draw_page(page, ["x"], size=(3400, 4400), fmt="PNG")
    from PIL import Image
    import io

    at_400 = Image.open(io.BytesIO(intake.ocr_derivative(page, 400, 200, 2400)))
    assert at_400.size == (1700, 2200)
    unknown = Image.open(io.BytesIO(intake.ocr_derivative(page, None, 200, 2400)))
    assert max(unknown.size) == 2400
    small = tmp_path / "s.png"
    draw_page(small, ["x"], size=(850, 1100), fmt="PNG")
    assert Image.open(io.BytesIO(intake.ocr_derivative(small, 100, 200, 2400))).size == (850, 1100)  # never upscaled


# --- config ----------------------------------------------------------------------------------------------------

def test_config_from_file_and_environment(tmp_path):
    p = tmp_path / "library.toml"
    p.write_text('[library]\nname = "garden"\ndatabase_url = "postgresql:///from-file"\nfiles = "/data/files"\n'
                 'intake = "/data/intake"\n[[collection]]\nslug = "archive"\ntitle = "Archive"\n'
                 '[[series]]\nslug = "minutes"\ncollection = "archive"\n[vocabulary]\ntypes = ["minutes"]\n')
    cfg = config_mod.load(p, env={"ATHENA_DATABASE_URL": "postgresql:///from-env", "ATHENA_NATS_URL": "nats://x"})
    assert (cfg.name, cfg.database_url, cfg.nats_url) == ("garden", "postgresql:///from-env", "nats://x")
    assert cfg.series_slugs() == {"minutes"} and cfg.types == ("minutes",) and cfg.subject_prefix == "library.garden"


@pytest.mark.parametrize("raw, match", [
    ({"library": {"name": "Garden Club"}}, "slug"),
    ({"library": {"name": "g"}, "colection": []}, "unknown config sections"),
    ({"library": {"name": "g"}, "collection": [{"slug": "a"}], "series": [{"slug": "s", "collection": "b"}]},
     "unknown collection"),
    ({"library": {"name": "g", "files": "/f", "intake": "/i"}}, "ATHENA_DATABASE_URL"),
])
def test_config_errors(raw, match):
    env = {"ATHENA_DATABASE_URL": "postgresql:///x", "ATHENA_FILES": "/f", "ATHENA_INTAKE": "/i"}
    if match == "ATHENA_DATABASE_URL":
        env = {}
    with pytest.raises(config_mod.ConfigError, match=match):
        config_mod.from_dict(raw, env=env)


# --- command line ----------------------------------------------------------------------------------------------

def test_cli_setup_intake_search_status(pg_url, tmp_path, monkeypatch, capsys):
    from athena.__main__ import main

    cfg = tmp_path / "library.toml"
    cfg.write_text('[library]\nname = "cli"\n[[collection]]\nslug = "archive"\ntitle = "Archive"\n'
                   '[[series]]\nslug = "clippings"\ncollection = "archive"\n')
    monkeypatch.setenv("ATHENA_CONFIG", str(cfg))
    monkeypatch.setenv("ATHENA_DATABASE_URL", pg_url)
    monkeypatch.setenv("ATHENA_FILES", str(tmp_path / "files"))
    monkeypatch.setenv("ATHENA_INTAKE", str(tmp_path / "intake"))
    assert main(["library", "setup"]) == 0
    make_bundle(tmp_path / "intake", example_items()[1], tmp_path / "work")
    assert main(["library", "intake"]) == 0
    assert main(["library", "search", "town green"]) == 0
    assert main(["library", "status"]) == 0
    out = capsys.readouterr().out
    assert "ingested:" in out and "Article on the town green" in out and "items 1" in out
    monkeypatch.setenv("ATHENA_DATABASE_URL", "postgresql://nobody@127.0.0.1:1/none?connect_timeout=1")
    assert main(["library", "status"]) == 1
