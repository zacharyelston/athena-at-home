# The static site

Status: **D16**, 2026-11-21. Code: [`athena/site/`](../athena/site). Design:
[portal.md](portal.md#ways-to-serve-a-feed-to-people). What a site may show is decided earlier, by
[visibility](visibility.md) and the [publisher](publisher.md).

A site is plain HTML, CSS and a little JavaScript, built from **one feed directory** and copied to any static host.
Nothing of the owner's runs on the internet, and the site keeps working without the machines that made it. The
builder never reads the library: it reads a feed that `athena publish check` has already verified.

```bash
athena publish build public --out /srv/feeds/public
athena publish check public /srv/feeds/public
athena site build /srv/feeds/public --out /srv/sites/public      # replaced as a whole on every build
athena site check /srv/sites/public --feed /srv/feeds/public     # exit 1 on any problem
athena site serve /srv/sites/public                              # preview at http://127.0.0.1:8080/
```

A members' site is the same commands over the members feed, into a **separate directory**, deployed separately
behind the host's own login. It is never a folder inside the public site.

Install with the `site` extra (Jinja2 and a pinned Pagefind); `site check` also needs the `library` extra.

## Settings

One `[site.<name>]` section per site in `library.toml`. `athena site build` uses the section named after the feed
unless `--site NAME` says otherwise. Nothing a visitor reads about the owner is in the code or the templates.

```toml
[site.public]
feed = "public"                    # default: the section name
title = "Riverside Garden Society Archive"
tagline = "Newsletters, minutes and photographs since 1931"
about = """
One or more paragraphs for the home page.

A blank line starts a new paragraph.
"""
about_more = """Paragraphs for the top of the "About this archive" page."""
contact = "archive@example.org"    # an email address or a URL, for removals and corrections
base_url = "https://archive.example.org/"   # optional: citations, the sitemap, the 404 page's links
index = false                      # may search engines index it? See below
preview = false                    # show a "Preview" banner; a preview is never indexed
lang = "en"
logo = "logo.svg"                  # optional, relative to library.toml; .svg, .png, .jpg or .webp
featured = ["3f9a0c…"]             # optional: item ids, the last part of an item's address on the site

[site.public.colors]               # optional
accent = "#2c5f2d"
background = "#fbfaf7"
text = "#1f2421"
```

- **Colours** must be `#rgb` or `#rrggbb`. Text and accent colours need a contrast of at least 4.5:1 against the
  background (WCAG AA); the config is refused otherwise.
- **Indexing is off unless switched on**, and `index = true` is only honoured for a site whose feed carries the
  public tier alone and that isn't a preview. Every other site gets `noindex` on every page and a `robots.txt`
  that disallows everything, whatever its settings say.
- A raster **logo** is re-encoded from its pixels, so it can't carry an author's name or a location. An SVG is
  copied as it is.
- The rights statement on each item comes from the feed (`[publish] rights_default`, a series' `rights`, or the
  item's own).

## What gets built

```
index.html             title, about text, a search box, browse by series and by decade, featured items
series/<slug>/         one page per series, thumbnails, oldest first
decade/<1960s>/        one page per decade, and "undated"
item/<id>/             every page image with its text; description, rights, citation, "ask for a removal"
search/                Pagefind's search box
about/                 what is here, how it was made, removals and corrections, privacy
images/<id>/           the feed's images, unchanged (hard links when the file system allows)
pagefind/              the search index and its code
static/                CSS, the viewer, two small scripts
404.html  robots.txt  sitemap.xml (only when indexing is allowed and base_url is set)  site.json
```

**Item pages** show the whole item top to bottom: each page's image, then its text in a fold-out labelled either
"read by software and not corrected" or "checked by a person". A page whose text the publisher withheld, or that
has none, says "No text is available for this page." Pressing an image opens a full-window zoom viewer
(OpenSeadragon 6.1.1, vendored) over the feed's three image sizes, with buttons for zoom, fit, previous and next;
Escape closes it. Without JavaScript the same link opens the largest image, where the browser's own zoom works.

**Search** is [Pagefind](https://pagefind.app) 1.6.0, pinned. It runs as the last build step over this build's
item pages only, so a site's index holds exactly the text on its own pages. Results link to the page of the item
where the words are, and can be narrowed by series, decade and type. Searching happens in the visitor's browser.

**Links are relative**, so a site works at any address, including a sub-path, without setting `base_url`.

## What a page may load

Itself. Fonts are the system's, and the viewer and the search code are served from the site. Every page carries a
Content-Security-Policy (`default-src 'self'`, plus what Pagefind's WebAssembly needs), so the browser refuses a
request to any other host even if a script or a record tried to make one. There are no cookies, no analytics, no
inline scripts, and `referrer` is `no-referrer` so following the contact link doesn't reveal which page it was on.
Everything from a record is escaped by the templates.

## `athena site check`

Run it on every build before copying a site anywhere.

| Check | What fails |
|---|---|
| Confined to its host | A page without the expected Content-Security-Policy; a script, stylesheet, image, frame or form pointing at another host; an inline script or event handler; a stylesheet that imports or loads from elsewhere. Ordinary links (`<a href>`) may go anywhere. |
| Links | A local link, image, script or stylesheet that doesn't exist; a `#part` that isn't on its page. |
| Indexing | A site that mustn't be indexed with a page lacking `noindex`, or a `robots.txt` that allows crawling. |
| Accessibility basics | No page language; not exactly one main heading; an image without alt text; a form field without a label. |
| Image metadata | EXIF, XMP, IPTC or a colour profile on any image (the publisher's check). |
| Forbidden strings | With `--forbid-file`: any listed string in a path or a file, including inside Pagefind's index. Its files are gzip-compressed, so a plain `grep` would miss a leak; the scanner decompresses them. |
| Against the feed | With `--feed`: an image that isn't byte-for-byte the feed's; an item page for an item the feed doesn't have; a feed rebuilt since the site was. |

The redaction and visibility guarantees are the feed's (`athena publish check`). `--feed` ties the site to that
checked feed: same images, same items, nothing more.

## Previews

`athena site serve DIR` serves a built site on this computer only. `--bind 0.0.0.0` lets other devices on the same
network open it, for showing a preview to the people who decide what's shared; use it on a trusted network, with
`preview = true` in the site's settings. It is a preview tool, not a web server to leave running.

## Size

Measured on a 16 GB M3 with a made-up feed of 800 items and 2,787 pages (`tests/fixtures/synthetic_feed.py`, page
images at 3,600 × 4,660 with paper grain):

| | |
|---|---|
| `athena site build` | 3.7 s |
| `athena site check --feed` | 21 s |
| Files | 10,025 (8,361 images, 817 HTML pages, 839 for search) |
| Size | 3.6 GB, almost all images (about 1.3 MB a page); search index 4.3 MB |

Real photographs of paper may compress differently, so measure again with the real feed before choosing a host.
One page per series holds every item's thumbnail, loaded as it scrolls into view; the largest here listed about
400 items in 121 KB of HTML.

## Tests

`tests/test_site.py` builds sites from a made-up feed (no database needed) and from the canary library's public
and members feeds. It checks that each site carries what its feed does and nothing else, that a members' record
planted in the public site's compressed search index fails the check, that markup inside a record is escaped, and
that each kind of problem in the table above is caught. Tests that need Pagefind skip when it isn't installed.

## Not done yet

- **An automated accessibility audit** (axe or pa11y). The check above covers what static HTML can show; the
  search box and the viewer are drawn by scripts; they were tried in a desktop browser with a mouse and a
  keyboard (open, buttons, Escape), not audited, and not with a screen reader.
- **Phones and tablets by hand.** The layout was checked at phone width in a desktop browser. Pinch-zoom in the
  viewer on a real iPhone hasn't been tried.
- **Deep Zoom tiles.** The viewer uses the feed's three sizes, so the closest zoom is the 3,200-pixel image.
- **Paging long lists.** A series with thousands of items would want pages; hundreds are fine.
