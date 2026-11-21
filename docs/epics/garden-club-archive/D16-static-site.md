# D16 · Static site: Pagefind per tier, optional members build, leak tests

| | |
|---|---|
| **ID** | D16 |
| **Type** | code |
| **Owner role** | ops agent |
| **Size** | 3 days |
| **Depends on** | D15 (feed directories) |
| **Week** | W4; **preview by Fri Dec 18** |
| **Branch** | `feat/static-site` (from `feat/publisher`) |

## Why

The club has no website. A static site, built from a feed and copied to any static host, means nothing of the
club's runs on the internet, there's nothing to patch, and it keeps working without the author's machines
([portal.md](../../portal.md#ways-to-serve-a-feed-to-people)).

## Inputs

- D15 feed directories (`public`, and `members` if the club wants one).
- Site config in `library.toml` (`[site.public]`, `[site.members]`): site title, short about text, contact and
  takedown address (Q12), colours, logo file, rights statement defaults, base URL, `robots` (index or not).
  **No club name or text in code or templates.**

## Outputs

- `athena/site/` and `athena site build <feed-dir> --config <site> --out <dir>`: plain HTML, CSS and a little JS.
  - **Home**: title, about text, browse by series and by decade, a few featured items chosen in config.
  - **Series pages** and **decade pages** with thumbnails.
  - **Item page**: zoomable viewer (OpenSeadragon, vendored) over the feed's zoom images (pyramid or tiles), page strip, metadata, the
    OCR text (labelled "uncorrected machine OCR" unless checked), rights statement, a suggested citation, and
    "request removal" linking to the takedown contact.
  - **Search**: Pagefind index built over this build's pages only, so the public index contains only public text;
    filters by series, decade and type.
  - **About this archive**: how it was made, what's included, how to request removal or correction.
  - No cookies, no analytics, no third-party requests (fonts, scripts and viewer are all vendored).
- **Members build**: the same command over the members feed into a separate output directory, deployed separately
  behind the host's own login (D19). Never a "hidden" folder inside the public site.
- `athena site check <dir>`: the D15 leak checker plus site checks (broken links, external requests, a decompressed
  scan of Pagefind's index fragments, which are gzip-compressed and would hide a leak from a plain grep).
- **Preview**: `athena site serve <dir>` on the LAN for the archivist and board; or a password-protected preview
  deployment once a host is chosen.

## Steps

1. Templates (Jinja2) and CSS; responsive, readable at phone width; WCAG 2.1 AA basics (contrast, alt text from
   titles, headings, keyboard navigation in the viewer).
2. Pagefind (pinned version; MIT) run as a build step; record its version in `feed.json`'s build info.
3. Members build path and config; `robots.txt` and `noindex` meta for the members build regardless of config.
4. `site check` with the decompressed Pagefind scan and an external-request check.
5. Preview build from the real library for the archivist and board (whatever is captured and reviewed by then,
   with a "preview" banner), served on the LAN or behind host auth.

## Acceptance criteria

- From the canary fixture library: public and members sites build; `site check` passes on both; it fails when a
  members canary is planted into the public build's Pagefind index (proving the decompressed scan works).
- Search on the public build finds a public fixture word and doesn't find a members-only word.
- No external network request on any page (checked by parsing every `src`/`href` and the CSS).
- Pages pass an automated accessibility check (axe or pa11y, if available offline) with no serious violations.
- A 2,500-page synthetic site builds in under 10 minutes; record its total size and **file count** for the host
  choice in D19 (pyramid mode should stay well under 20,000 files and a few GB).
- **Preview shown to the archivist by Fri Dec 18.**

## Test plan

pytest for template rendering and `site check`; the canary fixture builds; link checker; size measurement; manual
check in Safari on an iPhone and a desktop browser.

## Privacy and rights

- [ ] Separate builds and separate output directories per tier; the members build is never inside the public one.
- [ ] Rights statement and "request removal" link on every item page.
- [ ] No trackers, no cookies, no third-party assets.
- [ ] The preview is not public: LAN only or behind host auth, with `noindex`.

## Touches

`athena/site/` (templates, static assets, build, check), `athena/__main__.py`, `tests/test_site*.py`,
`docs/static-site.md`.

## Done means

A static public site (and optionally a members site) built from the club's feeds, searchable in the browser,
passing the leak and site checks, and a preview of it in front of the archivist by Dec 18.
