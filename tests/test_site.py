# SPDX-License-Identifier: GPL-3.0-or-later
"""The static site (D16): config, build, the site check, and the leak tests on the canary library's feeds."""
from __future__ import annotations

import gzip
import hashlib
import json
import threading
import urllib.error
import urllib.request
from importlib import resources
from pathlib import Path

import pytest

pytest.importorskip("jinja2")

from athena.__main__ import main  # noqa: E402
from athena.publish.leakcheck import scan_for_strings  # noqa: E402
from athena.site import build as site_build  # noqa: E402
from athena.site.build import SiteError, build, load_feed  # noqa: E402
from athena.site.check import check_site  # noqa: E402
from athena.site.cli import make_server  # noqa: E402
from athena.site.config import SiteConfigError, contrast, from_dict, load_site  # noqa: E402
from fixtures.synthetic_feed import make_feed  # noqa: E402

HAVE_PAGEFIND = site_build.pagefind_version() is not None
needs_pagefind = pytest.mark.skipif(not HAVE_PAGEFIND, reason="Pagefind isn't installed (the site extra)")
OSD_SHA256 = "14d587cd3ee7c4861f1a41077ce642029364a21029956ba635bd8fb1202cf9ad"     # openseadragon 6.1.1

SITE = {"title": "Riverside Garden Society Archive", "tagline": "Made-up records for tests",
        "about": "First paragraph.\n\nSecond paragraph.", "contact": "archive@example.org"}


def cfg(name="public", **over):
    return from_dict(name, {**SITE, **over})


@pytest.fixture(scope="module")
def feed(tmp_path_factory):
    root = tmp_path_factory.mktemp("feed") / "public"
    made = make_feed(root, items=14, seed=7, pool=4)
    return root, made


@pytest.fixture(scope="module")
def site(feed, tmp_path_factory):
    """The synthetic feed built once, with search when Pagefind is installed."""
    out = tmp_path_factory.mktemp("site") / "public"
    result = build(feed[0], cfg(), out, search=HAVE_PAGEFIND)
    return out, result


def fresh(feed_dir, tmp_path, **over):
    out = tmp_path / "site"
    return out, build(feed_dir, cfg(**over), out, search=False)


def replace(path: Path, old: str, new: str) -> None:
    """Rewrite a file as a new file (site images are hard links into the feed; never edit one in place)."""
    text = path.read_text("utf-8")
    assert old in text
    path.unlink()
    path.write_text(text.replace(old, new, 1), "utf-8")


# ------------------------------------------------------------------------------------------------------- config

def test_config_defaults_are_private():
    c = cfg()
    assert c.feed == "public" and c.index is False and c.preview is False
    assert c.about == ("First paragraph.", "Second paragraph.")
    assert c.contact_href == "mailto:archive@example.org"


@pytest.mark.parametrize("change, message", [
    ({"title": ""}, "title is required"),
    ({"colour": "#fff"}, "unknown keys"),
    ({"colors": {"accent": "green"}}, "must look like"),
    ({"colors": {"accent": "#c8e6c9"}}, "contrast"),
    ({"colors": {"text": "#bbbbbb", "background": "#ffffff"}}, "contrast"),
    ({"contact": "javascript:alert(1)"}, "contact must be"),
    ({"base_url": "ftp://example.org"}, "base_url"),
    ({"index": "yes"}, "true or false"),
    ({"lang": "en\"><script>"}, "lang"),
    ({"featured": ["../../etc"]}, "featured"),
    ({"logo": "logo.exe"}, "logo must be"),
])
def test_config_rejects(change, message):
    with pytest.raises(SiteConfigError, match=message):
        cfg(**change)


def test_config_details(tmp_path):
    assert cfg(base_url="https://example.org/archive").base_url == "https://example.org/archive/"
    assert cfg(contact="https://example.org/contact").contact_href == "https://example.org/contact"
    assert round(contrast("#000000", "#ffffff")) == 21 and contrast("#777", "#777") == 1
    (tmp_path / "library.toml").write_text('[site.members]\ntitle = "T"\nfeed = "members"\nlogo = "art/logo.svg"\n')
    c = load_site(tmp_path / "library.toml", "members")
    assert c.feed == "members" and c.logo == tmp_path / "art" / "logo.svg"
    with pytest.raises(SiteConfigError, match=r"no \[site.public\]"):
        load_site(tmp_path / "library.toml", "public")


def test_library_config_checks_site_sections():
    from athena.library import config as config_mod
    from fixtures.canary import CONFIG

    env = {"ATHENA_DATABASE_URL": "postgresql:///x", "ATHENA_FILES": "/tmp/f", "ATHENA_INTAKE": "/tmp/i"}
    config_mod.from_dict({**CONFIG, "site": {"public": SITE}}, env=env)
    with pytest.raises(config_mod.ConfigError, match="title is required"):
        config_mod.from_dict({**CONFIG, "site": {"public": {"tagline": "no title"}}}, env=env)


# -------------------------------------------------------------------------------------------------------- build

def test_build_writes_every_kind_of_page(feed, site):
    out, result = site
    made = feed[1]
    assert result.items == len(made["items"]) and result.pages == made["pages"]
    for rel in ("index.html", "about/index.html", "404.html", "robots.txt", "site.json", "static/site.css",
                "static/theme.css", "static/viewer.js", "static/vendor/openseadragon/openseadragon.min.js",
                "static/vendor/openseadragon/LICENSE.txt"):
        assert (out / rel).is_file(), rel
    assert {d.name for d in (out / "item").iterdir()} == set(made["items"])
    assert (out / "series" / "newsletters" / "index.html").is_file()
    assert any((out / "decade").iterdir())
    _, items = load_feed(feed[0])
    first = items[0]
    html = (out / "item" / first["id"] / "index.html").read_text("utf-8")
    assert first["title"] in html and "Cite as" in html and "Rights" in html
    assert "Ask for this item to be removed or corrected" in html and "mailto:archive@example.org?subject=" in html
    assert html.count('<section class="page"') == len(first["pages"])
    assert "sitemap.xml" not in {f.name for f in out.iterdir()}             # not indexable by default
    marker = json.loads((out / "site.json").read_text())
    assert marker["feed"] == "public" and marker["indexable"] is False and "library" not in marker


def test_site_check_is_clean(feed, site):
    assert check_site(site[0], feed_dir=feed[0]) == []


def test_text_is_labelled_by_how_it_was_made(feed, site):
    _, items = load_feed(feed[0])
    labels = {p["text_label"] for i in items for p in i["pages"] if p["text"]}
    assert labels == {"Text of this page, checked by a person",
                      "Text of this page, read by software and not corrected"}
    photo = next(i for i in items if i["type"] == "photograph")
    assert "No text is available for this page." in (site[0] / photo["url"] / "index.html").read_text("utf-8")


def test_noindex_unless_public_and_opted_in(feed, tmp_path, tmp_path_factory):
    out, result = fresh(feed[0], tmp_path)                                  # default: not indexable
    assert not result.indexable
    assert all('name="robots" content="noindex' in f.read_text("utf-8") for f in out.rglob("*.html"))
    assert "Disallow: /" in (out / "robots.txt").read_text()

    out, result = fresh(feed[0], tmp_path, index=True, base_url="https://example.org/a/")
    assert result.indexable and not any("noindex" in f.read_text("utf-8") for f in out.rglob("*.html"))
    assert "Sitemap: https://example.org/a/sitemap.xml" in (out / "robots.txt").read_text()
    assert f"https://example.org/a/item/{feed[1]['items'][0]}/" in (out / "sitemap.xml").read_text()
    assert check_site(out, feed_dir=feed[0]) == []

    out, result = fresh(feed[0], tmp_path, index=True, preview=True)        # a preview is never indexed
    assert not result.indexable and "Preview." in (out / "index.html").read_text("utf-8")

    members = tmp_path_factory.mktemp("members") / "members"                # nor is a feed with members' records
    make_feed(members, items=3, seed=3, pool=1, feed="members", tiers=("members", "public"))
    out = tmp_path / "members-site"
    result = build(members, cfg("members", index=True), out, search=False)
    assert not result.indexable and any("index = true ignored" in w for w in result.warnings)
    assert "Disallow: /" in (out / "robots.txt").read_text()
    assert check_site(out, feed_dir=members) == []


def test_records_cannot_inject_markup(tmp_path):
    feed_dir = tmp_path / "public"
    made = make_feed(feed_dir, items=2, seed=5, pool=1, marker='<script>alert("x")</script><img src=//evil.example/p.png>')
    out = tmp_path / "site"
    build(feed_dir, cfg(), out, search=False)
    for f in out.rglob("*.html"):
        html = f.read_text("utf-8")
        assert "<script>alert" not in html and "<img src=//evil" not in html
    assert "&lt;script&gt;alert" in (out / "item" / made["items"][0] / "index.html").read_text("utf-8")
    assert check_site(out, feed_dir=feed_dir) == []


def test_build_replaces_the_whole_directory_and_refuses_others(feed, tmp_path):
    out, _ = fresh(feed[0], tmp_path)
    (out / "item" / "stale").mkdir()
    (out / "item" / "stale" / "index.html").write_text("left over from an earlier build")
    build(feed[0], cfg(), out, search=False)
    assert not (out / "item" / "stale").exists()

    other = tmp_path / "documents"
    other.mkdir()
    (other / "keep.txt").write_text("not a site")
    with pytest.raises(SiteError, match="refusing to replace"):
        build(feed[0], cfg(), other, search=False)
    assert (other / "keep.txt").is_file()
    with pytest.raises(SiteError, match="inside its feed"):
        build(feed[0], cfg(), feed[0] / "site", search=False)
    with pytest.raises(SiteError, match="is for feed 'members'"):
        build(feed[0], cfg("members"), tmp_path / "m", search=False)
    with pytest.raises(SiteError, match="not a feed directory"):
        build(tmp_path, cfg(), tmp_path / "x", search=False)


def test_a_malformed_feed_is_refused(tmp_path):
    feed_dir = tmp_path / "public"
    made = make_feed(feed_dir, items=1, seed=2, pool=1)
    item = feed_dir / "items" / f"{made['items'][0]}.json"
    doc = json.loads(item.read_text())
    bad = dict(doc, id="../../escape")
    (feed_dir / "items" / "evil.json").write_text(json.dumps(bad))
    with pytest.raises(SiteError, match="bad item id"):
        build(feed_dir, cfg(), tmp_path / "site", search=False)
    (feed_dir / "items" / "evil.json").unlink()
    page = feed_dir / "pages" / f"{doc['pages'][0]['id']}.json"
    p = json.loads(page.read_text())
    p["images"]["web"]["path"] = "../../../etc/passwd"
    page.write_text(json.dumps(p))
    with pytest.raises(SiteError, match="missing or misplaced"):
        build(feed_dir, cfg(), tmp_path / "site", search=False)
    meta = json.loads((feed_dir / "feed.json").read_text())
    (feed_dir / "feed.json").write_text(json.dumps(dict(meta, feed_version=2)))
    with pytest.raises(SiteError, match="feed version"):
        build(feed_dir, cfg(), tmp_path / "site", search=False)


def test_featured_logo_colours_and_404(feed, tmp_path):
    ids = feed[1]["items"]
    logo = tmp_path / "logo.png"
    from PIL import Image
    im = Image.new("RGB", (40, 40), "green")
    exif = Image.Exif()
    exif[0x010F] = "Apple"
    im.save(logo, "PNG", exif=exif)
    c = from_dict("public", {**SITE, "featured": [ids[0], "0" * 20], "logo": str(logo),
                             "base_url": "https://example.org/archive/",
                             "colors": {"accent": "#7a1f3d", "background": "#ffffff", "text": "#111111"}})
    out = tmp_path / "site"
    result = build(feed[0], c, out, search=False)
    home = (out / "index.html").read_text("utf-8")
    assert 'id="featured"' in home and f"item/{ids[0]}/" in home
    assert any("featured item 0000" in w for w in result.warnings)
    assert "--accent: #7a1f3d" in (out / "static" / "theme.css").read_text()
    assert 'src="static/logo.png' in home
    assert 'href="/archive/static/site.css' in (out / "404.html").read_text("utf-8")
    assert "https://example.org/archive/item/" in (out / "item" / ids[0] / "index.html").read_text("utf-8")
    assert check_site(out, feed_dir=feed[0]) == []                 # the logo's metadata was dropped


def test_an_empty_feed_still_builds(tmp_path):
    feed_dir = tmp_path / "public"
    make_feed(feed_dir, items=1, seed=1, pool=1)
    for f in list((feed_dir / "items").iterdir()):
        f.unlink()
    out = tmp_path / "site"
    result = build(feed_dir, cfg(), out, search=HAVE_PAGEFIND)
    assert result.items == 0 and not result.search and not (out / "search").exists()
    assert "Nothing has been published here yet." in (out / "index.html").read_text("utf-8")
    assert check_site(out) == []


def test_vendored_viewer_is_the_pinned_release():
    js = (resources.files("athena.site") / "static/vendor/openseadragon/openseadragon.min.js").read_bytes()
    assert hashlib.sha256(js).hexdigest() == OSD_SHA256
    readme = (resources.files("athena.site") / "static/vendor/openseadragon/README.md").read_text()
    assert OSD_SHA256 in readme


# -------------------------------------------------------------------------------------------------------- check

@pytest.mark.parametrize("old, new, expect", [
    ('<h1>', '<img src="https://tracker.example/p.gif" alt=""><h1>', "points outside the site"),
    ('<h1>', '<script src="//cdn.example/a.js"></script><h1>', "points outside the site"),
    ('<h1>', '<link rel="stylesheet" href="https://fonts.example/css"><h1>', "points outside the site"),
    ('<h1>', '<script>fetch("/x")</script><h1>', "an inline script"),
    ('<h1>', '<iframe src="about/"></iframe><h1>', "a <iframe> element"),
    ('<h1>', '<p onclick="x()">hi</p><h1>', "inline event handler"),
    ('<h1>', '<img src="static/site.css"><h1>', "without alt text"),
    ('<h1>', '<h1>Another</h1><h1>', "main headings"),
    ('<h1>', '<a href="item/nowhere/">gone</a><h1>', "broken link"),
    ('<h1>', '<a href="#nowhere">gone</a><h1>', "isn't on the page"),
    ('<h1>', '<a href="../outside.html">out</a><h1>', "broken link"),
    ("default-src ", "default-src https://cdn.example ", "Content-Security-Policy"),
    ('<meta name="robots" content="noindex, nofollow">', "", "no noindex"),
    (' lang="en"', "", "no language"),
])
def test_check_catches(feed, tmp_path, old, new, expect):
    out, _ = fresh(feed[0], tmp_path)
    assert check_site(out) == []
    replace(out / "index.html", old, new)
    problems = check_site(out)
    assert any(expect in p for p in problems), problems


def test_check_allows_ordinary_outside_links(feed, tmp_path):
    out, _ = fresh(feed[0], tmp_path, contact="https://example.org/contact")
    assert check_site(out) == []


def test_check_catches_css_robots_and_feed_differences(feed, tmp_path):
    out, _ = fresh(feed[0], tmp_path)
    with open(out / "static" / "theme.css", "a") as f:
        f.write('@import "https://fonts.example/css";\nbody { background: url(missing.png); }\n')
    problems = check_site(out)
    assert any("from outside the site" in p for p in problems) and any("broken url" in p for p in problems)

    out, _ = fresh(feed[0], tmp_path)
    (out / "robots.txt").write_text("User-agent: *\nAllow: /\n")
    assert any("robots.txt" in p for p in check_site(out))

    out, _ = fresh(feed[0], tmp_path)
    image = next((out / "images").rglob("thumb.jpg"))
    data = image.read_bytes()
    image.unlink()                                             # a hard link into the feed: replace, don't edit
    image.write_bytes(data + b"\x00")
    (out / "images" / "0000" / "x").mkdir(parents=True)
    (out / "images" / "0000" / "x" / "web.jpg").write_bytes(data)
    (out / "item" / "abcdef0123").mkdir()
    problems = check_site(out, feed_dir=feed[0])
    assert any("differs from the feed's image" in p for p in problems)
    assert any("an image that isn't in the feed" in p for p in problems)
    assert any("an item that isn't in the feed" in p for p in problems)

    assert check_site(tmp_path) == [f"{tmp_path} is not a site directory (no site.json)"]


def test_check_notices_a_rebuilt_feed(tmp_path):
    feed_dir = tmp_path / "public"
    make_feed(feed_dir, items=2, seed=4, pool=1)
    out = tmp_path / "site"
    build(feed_dir, cfg(), out, search=False)
    meta = json.loads((feed_dir / "feed.json").read_text())
    (feed_dir / "feed.json").write_text(json.dumps(dict(meta, built_at="2027-01-02T00:00:00+00:00")))
    assert any("rebuilt since" in p for p in check_site(out, feed_dir=feed_dir))


# ------------------------------------------------------------------------------------------------------- search

def fragments(site_dir: Path) -> str:
    """Everything Pagefind would show for a search, decompressed."""
    return "\n".join(gzip.decompress(f.read_bytes()).decode("utf-8", "replace")
                     for f in sorted((site_dir / "pagefind" / "fragment").iterdir()))


@needs_pagefind
def test_search_index_covers_this_build_only(feed, site):
    out, result = site
    made = feed[1]
    assert result.search and result.pagefind
    assert json.loads((out / "site.json").read_text())["pagefind"] == result.pagefind
    index = fragments(out)
    assert index.count('"url"') == len(made["items"])                    # item pages only: no home, no listings
    for item_id, word in made["words"].items():
        if (word in (out / "item" / item_id / "index.html").read_text("utf-8")):
            assert word in index
    assert '"Series":["Newsletters"]' in index and '"Decade":[' in index and '"anchors":[{"element":"h2","id":"page-1"' in index
    assert "Cite as" not in index and "Ask for this item" not in index   # page furniture isn't searchable
    html = (out / "search" / "index.html").read_text("utf-8")
    assert "pagefind/pagefind-ui.js" in html and "static/search.js" in html
    assert not (out / "pagefind" / "pagefind-modular-ui.js").exists()


@needs_pagefind
def test_a_leak_inside_the_compressed_index_is_caught(feed, tmp_path):
    out = tmp_path / "site"
    build(feed[0], cfg(), out)
    secret = "MEMBERS-ONLY-CANARY"
    assert check_site(out, feed_dir=feed[0], forbidden=[secret]) == []
    planted = out / "pagefind" / "fragment" / "en_planted.pf_fragment"
    planted.write_bytes(gzip.compress(b'pagefind_dcd{"url":"/item/x/","content":"minutes MEMBERS-ONLY-CANARY"}'))
    assert secret.encode() not in planted.read_bytes()                   # a plain grep would miss it
    problems = check_site(out, feed_dir=feed[0], forbidden=[secret])
    assert any("en_planted.pf_fragment (gzip)" in p and secret in p for p in problems), problems


# ---------------------------------------------------------------------------------------------- cli and preview

def test_cli_build_check_and_serve(feed, tmp_path, capsys):
    (tmp_path / "library.toml").write_text(
        '[site.public]\ntitle = "Riverside Garden Society Archive"\ncontact = "archive@example.org"\n')
    out = tmp_path / "site"
    args = ["site", "build", str(feed[0]), "--config", str(tmp_path / "library.toml"), "--out", str(out), "--no-search"]
    assert main(args) == 0
    assert "may not index it" in capsys.readouterr().out
    assert main(["site", "check", str(out), "--feed", str(feed[0])]) == 0
    forbid = tmp_path / "forbid.txt"
    forbid.write_text("Riverside Garden\n")
    assert main(["site", "check", str(out), "--forbid-file", str(forbid)]) == 1
    assert main(["site", "build", str(feed[0]), "--config", str(tmp_path / "library.toml"), "--site", "members",
                 "--out", str(out), "--no-search"]) == 1
    assert main(["site", "serve", str(tmp_path / "nowhere")]) == 1

    server = make_server(out, "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        with urllib.request.urlopen(base + "/") as r:
            assert r.status == 200 and b"Riverside Garden Society Archive" in r.read()
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(base + "/item/missing/")
        assert e.value.code == 404 and b"Page not found" in e.value.read()
    finally:
        server.shutdown()
        server.server_close()


# ------------------------------------------------------------------------------------- the canary library's feeds

@pytest.fixture
def canary_sites(pg_url, tmp_path):
    from athena.publish import build as build_feed
    from fixtures.canary import make_canary_library

    lib, ids = make_canary_library(pg_url, tmp_path / "lib")
    sites = {}
    for name in ("public", "members"):
        feed_dir = tmp_path / "feeds" / name
        build_feed(lib, name, feed_dir)
        out = tmp_path / "sites" / name
        result = build(feed_dir, cfg(name, index=True), out, search=HAVE_PAGEFIND)
        sites[name] = (out, feed_dir, result)
    return sites


def test_canary_sites_carry_what_their_feed_does_and_nothing_else(canary_sites):
    from fixtures import canary

    for name, forbidden, required in (("public", canary.FORBIDDEN_PUBLIC, canary.REQUIRED_PUBLIC),
                                      ("members", canary.FORBIDDEN_MEMBERS, canary.REQUIRED_MEMBERS)):
        out, feed_dir, result = canary_sites[name]
        assert check_site(out, feed_dir=feed_dir, forbidden=forbidden) == [], name
        for marker in required:
            assert scan_for_strings(out, [marker]), f"{marker} is missing from the {name} site"
        assert not scan_for_strings(out, ["kept in the library only"])       # notes aren't a published field
    public, members = canary_sites["public"][2], canary_sites["members"][2]
    assert public.indexable and not members.indexable                         # members: noindex whatever it asks
    assert "Disallow: /" in (canary_sites["members"][0] / "robots.txt").read_text()
    assert canary_sites["public"][0].resolve() not in canary_sites["members"][0].resolve().parents


def test_canary_redacted_pages(canary_sites):
    out = canary_sites["public"][0]
    pages = {f: f.read_text("utf-8") for f in (out / "item").rglob("index.html")}
    red = next(html for html in pages.values() if "CANARY-RED-TITLE" in html)
    assert "Call the secretary at [redacted] today" in red
    stale = next(html for html in pages.values() if "CANARY-STALE-TITLE" in html)
    assert "No text is available for this page." in stale and "<details" not in stale
    narrow = next(html for html in pages.values() if "CANARY-NARROW-TITLE" in html)
    assert narrow.count('<section class="page"') == 1


@needs_pagefind
def test_canary_search_finds_public_words_only(canary_sites):
    from fixtures import canary

    public = fragments(canary_sites["public"][0])
    assert "CANARY-PUB-TEXT" in public and "CANARY-NARROW-ONE" in public
    for marker in canary.FORBIDDEN_PUBLIC:
        assert marker not in public
    members = fragments(canary_sites["members"][0])
    assert "CANARY-MEM-TEXT" in members and "CANARY-CAP-TEXT" in members
    for marker in canary.FORBIDDEN_MEMBERS:
        assert marker not in members

    # A members' record planted into the public site's search index fails the check.
    out, feed_dir, _ = canary_sites["public"]
    leaked = next(f for f in sorted((canary_sites["members"][0] / "pagefind" / "fragment").iterdir())
                  if b"CANARY-MEM-TEXT" in gzip.decompress(f.read_bytes()))
    (out / "pagefind" / "fragment" / leaked.name).write_bytes(leaked.read_bytes())
    problems = check_site(out, feed_dir=feed_dir, forbidden=canary.FORBIDDEN_PUBLIC)
    assert any("(gzip)" in p and "CANARY-MEM" in p for p in problems), problems
