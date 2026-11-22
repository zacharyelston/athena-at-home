# SPDX-License-Identifier: GPL-3.0-or-later
"""The describe form: what it saves, what it refuses, and that it answers only this computer."""
from __future__ import annotations

import io
import re
import sys
from pathlib import Path

import pytest

pytest.importorskip("cv2")
pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))
import synthetic_session as ss  # noqa: E402

from athena.bundle import read_bundle, validate  # noqa: E402
from athena.capture import session, staging, web  # noqa: E402
from athena.capture.config import CaptureConfig  # noqa: E402

BASE = "http://127.0.0.1:8090"
ITEMS = [{"slip": "Spring newsletter 1962", "pages": [("text", {}), ("text", {"blur": 3.0})]},
         {"slip": "", "pages": [("photo", {})]},                # a separator sheet with nothing written on it
         {"slip": "Program", "pages": [("text", {})]}]


@pytest.fixture(scope="module")
def imported(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("describe")
    ss.make_session(root / "session", ITEMS)
    ss.make_session(root / "session" / "loose", [{"pages": [("text", {})]}], layout="folders", first=60)   # no slip
    cfg = CaptureConfig(staging=root / "staging", collection="archive", min_ppi=100, min_ppi_photo=100, quick=False)
    session.import_session(root / "session", cfg)
    return root / "staging"


@pytest.fixture
def cfg(imported, tmp_path) -> CaptureConfig:
    import shutil

    shutil.copytree(imported, tmp_path / "staging")
    return CaptureConfig(staging=tmp_path / "staging", collection="archive", station="desk-1",
                         known_collections=("archive", "loans"),
                         known_series=(("newsletters", "Newsletters", "archive"), ("on-loan", "On loan", "loans")),
                         types=("newsletter", "program", "photograph"))


@pytest.fixture
def client(cfg):
    app = web.create_app(cfg)
    c = TestClient(app, base_url=BASE, follow_redirects=False)
    c.token = app.state.token
    c.ids = [b.name for b in staging.staged(cfg.staging)]
    return c


def post(client, bundle_id, **fields):
    data = {"token": client.token, "series": "newsletters", **fields}
    return client.post(f"/item/{bundle_id}", data=data, headers={"origin": BASE})


def test_list_and_form_show_the_items_and_load_nothing_from_elsewhere(client):
    r = client.get("/")
    assert r.status_code == 200 and "4 items, 4 without a title" in r.text and "desk-1" in r.text
    assert f'href="/item/{client.ids[0]}"' in r.text and "Describe the next one" in r.text
    form = client.get(f"/item/{client.ids[0]}")
    assert form.status_code == 200 and "Shoot again: blurred" in form.text and "slip.jpg" in form.text
    assert '<option value="newsletters">Newsletters</option>' in form.text and ">unsorted</option>" in form.text
    assert "slip.jpg" not in client.get(f"/item/{client.ids[3]}").text               # that item has no slip
    for page in (r, form):
        assert not re.search(r"<script|\son\w+=|https?://|//\w", page.text)          # no script, nothing external
        assert "default-src 'none'" in page.headers["content-security-policy"]
        assert page.headers["cache-control"] == "no-store" and page.headers["referrer-policy"] == "same-origin"
        assert page.headers["x-content-type-options"] == "nosniff"
        for url in re.findall(r'(?:src|href|action)="([^"]+)"', page.text):
            assert url.startswith("/"), url
            assert client.get(url).status_code == 200, url
    assert client.get("/static/describe.css").headers["content-type"].startswith("text/css")
    assert client.get("/docs").status_code == 404 and client.get("/openapi.json").status_code == 404


def test_saving_a_description(client, cfg):
    bid = client.ids[0]
    r = post(client, bid, title="  Spring newsletter ", date="about April 1962", type="Newsletter",
             creator="Riverside Garden Society", container="box 1, folder 3", format="mimeograph", extent="",
             tags="Flower Show, plant sale, flower show", notes="", side_2="verso", kind_2="handwriting")
    assert r.status_code == 303 and r.headers["location"] == f"/item/{bid}?saved=1"
    b = read_bundle(cfg.staging / bid)
    assert b.manifest["describe"] == {
        "collection": "archive", "series": "newsletters",
        "item": {"title": "Spring newsletter", "date": "1962-04~", "type": "newsletter",
                 "creator": "Riverside Garden Society", "physical": {"container": "box 1, folder 3",
                                                                    "format": "mimeograph"},
                 "tags": ["flower-show", "plant-sale"]}}
    assert [(p.side, p.kind) for p in b.pages] == [("recto", "text"), ("verso", "handwriting")]
    assert validate(b.path, series={"newsletters"}, types={"newsletter"}).ok
    shown = client.get(r.headers["location"]).text
    assert "Date understood as <strong>circa April 1962</strong>" in shown and 'value="1962-04~"' in shown
    assert 'value="Spring newsletter"' in shown and "<option selected>verso</option>" in shown
    assert "4 items, 3 without a title" in client.get("/").text and "circa April 1962" in client.get("/").text

    assert post(client, bid, title="Spring newsletter", date="", series="on-loan").status_code == 303
    again = read_bundle(cfg.staging / bid).manifest["describe"]                      # an emptied field is removed
    assert again == {"collection": "loans", "series": "on-loan", "item": {"title": "Spring newsletter"}}


def test_a_date_that_cannot_be_read_is_refused_readably_and_nothing_changes(client, cfg):
    bid = client.ids[0]
    before = read_bundle(cfg.staging / bid).manifest
    for date, message in (("4/5/1962", "could be read two ways"), ("sometime", "write a date like 1962"),
                          ("April 62", "write the year in full"), ("1965-1960", "ends before it starts")):
        r = post(client, bid, title="Kept <b>as typed</b>", date=date)
        assert r.status_code == 422 and "Not saved" in r.text and message in r.text
        assert "Kept &lt;b&gt;as typed&lt;/b&gt;" in r.text and "<b>as typed" not in r.text
    assert read_bundle(cfg.staging / bid).manifest == before
    r = post(client, bid, kind_1="poster")
    assert r.status_code == 422 and "Page 1: choose" in r.text
    assert post(client, bid, series=" ").status_code == 422


def test_save_and_next_goes_to_the_next_undescribed_item(client):
    r = post(client, client.ids[0], title="One", then="next")
    assert (r.status_code, r.headers["location"]) == (303, f"/item/{client.ids[1]}")
    post(client, client.ids[1], title="Two")
    post(client, client.ids[3], title="Four")
    assert post(client, client.ids[2], title="Three", then="next").headers["location"] == "/"
    assert "0 without a title" in client.get("/").text and "Describe the next one" not in client.get("/").text


def test_a_series_typed_by_hand_when_the_station_knows_none(imported, tmp_path):
    import shutil

    shutil.copytree(imported, tmp_path / "staging")
    cfg = CaptureConfig(staging=tmp_path / "staging", collection="archive")
    app = web.create_app(cfg)
    c = TestClient(app, base_url=BASE, follow_redirects=False)
    bid = staging.staged(cfg.staging)[0].name
    assert '<input id="series" name="series"' in c.get(f"/item/{bid}").text
    r = c.post(f"/item/{bid}", data={"token": app.state.token, "series": "Year Books", "type": "Year book"})
    assert r.status_code == 303
    d = read_bundle(cfg.staging / bid).manifest["describe"]
    assert (d["collection"], d["series"], d["item"]) == ("archive", "year-books", {"type": "year-book"})


def test_only_this_computer_and_only_its_own_form(client, cfg):
    bid = client.ids[0]
    before = read_bundle(cfg.staging / bid).manifest
    for host in ("evil.example", "evil.example:8090", "192.168.1.20:8090", "127.0.0.1.evil.example"):
        r = client.get("/", headers={"host": host})
        assert r.status_code == 403 and "only available on the computer" in r.text, host
    assert client.get("/", headers={"host": "localhost:8090"}).status_code == 200
    assert client.get("/", headers={"host": "[::1]:8090"}).status_code == 200
    data = {"token": client.token, "series": "newsletters", "title": "Changed by a stranger"}
    for origin in ("null", "http://evil.example", "http://127.0.0.1:3000", "http://localhost:8090"):
        r = client.post(f"/item/{bid}", data=data, headers={"origin": origin})
        assert r.status_code == 403 and "its own page" in r.text, origin
    for token in ("", "guess", client.token[:-1]):
        r = client.post(f"/item/{bid}", data={**data, "token": token}, headers={"origin": BASE})
        assert r.status_code == 403 and "out of date" in r.text
    assert client.post(f"/item/{bid}", data={**data, "notes": "x" * 70_000}, headers={"origin": BASE}).status_code == 413
    assert read_bundle(cfg.staging / bid).manifest == before
    assert client.post(f"/item/{bid}", data=data).status_code == 303          # no Origin header at all: the token decides


def test_images_and_unknown_things(client, cfg):
    bid = client.ids[0]
    thumb = client.get(f"/item/{bid}/page/1.jpg")
    view = client.get(f"/item/{bid}/page/1.jpg?size=view")
    assert thumb.headers["content-type"] == "image/jpeg" and view.headers["cache-control"] == "no-store"
    with Image.open(io.BytesIO(thumb.content)) as t, Image.open(io.BytesIO(view.content)) as v:
        assert max(t.size) == 360 and 1000 < max(v.size) <= 2000 and not t.getexif()
    slip = client.get(f"/item/{bid}/slip.jpg")
    assert slip.status_code == 200 and Image.open(io.BytesIO(slip.content)).size[0] > 1000
    assert client.get(f"/item/{client.ids[3]}/slip.jpg").status_code == 404
    for url in (f"/item/{bid}/page/9.jpg", f"/item/{bid}/page/1.jpg?size=huge", "/item/01ARZ3NDEKTSV4RRFFQ69G5FAV",
                "/item/..%2F..%2Fetc", "/item/imported.jsonl", "/static/../web.py", "/nothing"):
        assert client.get(url).status_code == 404, url
    assert client.post("/item/01ARZ3NDEKTSV4RRFFQ69G5FAV", data={"token": client.token}).status_code == 404
    import json
    manifest = cfg.staging / client.ids[1] / "manifest.json"       # a manifest pointing outside its bundle
    m = json.loads(manifest.read_text())
    m["pages"][0]["page"] = f"../{bid}/pages/001.jpg"
    manifest.write_text(json.dumps(m))
    assert client.get(f"/item/{client.ids[1]}/page/1.jpg").status_code == 404
    gone = client.ids[2]
    staging.discard(cfg.staging, gone)
    r = client.get(f"/item/{gone}")
    assert r.status_code == 404 and "sent already" in r.text and r.headers["content-type"].startswith("text/plain")


def test_no_staging_no_form():
    with pytest.raises(ValueError, match="no staging directory"):
        web.create_app(CaptureConfig())
