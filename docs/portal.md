# Sharing and publishing: libraries, feeds and portals

Status: **design**, 2026-11-20.

Athena@Home is built from core modules (see [design.md](design.md#core-modules-composed-per-deployment)), and a
deployment can run **several libraries**, each placed where it's needed: one on a private network, one on a guest
network, one in a car, one behind a public website. Sharing between them is one mechanism: a library offers a
**filtered feed** of its contents, and another library, or a portal, receives it. Nothing is shared until someone
decides it should be, and what's shared, with whom, and from where is configuration, not code.

Examples of the same mechanism:

| Situation | Upstream library | Receives a feed | Contents shared |
|---|---|---|---|
| Household guests | the private home library | a guest library on the guest wifi | wifi details, house rules, what's nearby, appliance manuals |
| A car | the home library | a small offline library in the car | owner's manual, repair guide, its settings, registration and insurance cards it must carry |
| A club | the archivist's library | the club's public and members sites | history, programs, older newsletters (public); directories, minutes (members) |
| A library or company | the staff library | a public catalog portal | the catalog and public documents |

## Visibility tiers

Inside a library, everything has an audience:

| Tier | Who sees it |
|---|---|
| **private** | the people who run this library (default for everything) |
| **members** | a group the owner admits: club members, card holders, employees, guests |
| **public** | anyone the receiving side serves |

Three tiers to start; named groups (e.g. "board", "researchers", "guests", "mechanic") can come later. A feed is
defined by the tiers (and optionally groups, collections or tags) it carries, so "the guest feed" and "the car feed"
are just two feed definitions on the same library.

## Where visibility is set

- **Collection → series → item → page.** Each level inherits from its parent unless it overrides. Narrowing (making
  something more private than its parent) is always allowed; widening is allowed but logged and shown in review.
- **Field and region redaction.** A shared page can still hide a field (a phone number, an account number) or a
  region of the page image (a directory listing). Redaction is applied when a feed is built, to the page image and
  its text alike.
- **Policies**, so nobody decides page by page:
  - *age*: "items newer than 50 years are members-only"
  - *content*: "pages tagged `directory` are members-only"
  - *workflow*: "nothing is shared until it has been reviewed"
  - *rights*: "clippings published after 1930 are members-only" (US public domain cutoff as of 2026; it moves
    forward each January)
- **Effective visibility** combines the item's own setting, its parents and the policies, and is shown next to
  every item, along with which feeds it currently goes to.

## Feeds: the boundary

A receiving library or portal never reads the upstream library. The upstream library's **publisher** builds each
feed separately:

- page images at the size the receiver needs (web sizes and zoom tiles for a site, phone-readable images for a car),
  never the originals, with redactions burned in
- OCR text and the metadata fields the feed allows, also redacted
- a search index for what the feed carries

The publisher updates feeds as the library changes (`library.events`), and every share and unshare is logged with
who, what, when and why. Taking something down is an unshare plus an update on the receiving side (and a cache
purge for a public website).

A receiving library is an ordinary Athena@Home library, so it can search, serve its own portal, and keep working
offline (the car). It can also be **read-only**, accepting only its upstream feed.

## Where things are reachable

Each library and portal is placed on the network that suits it: the private LAN, a guest network, a device with
no network at all that syncs when it's home (the car), or the internet through a static host or a tunnel. This is
deployment configuration; no library assumes which network it's on.

## Ways to serve a feed to people

| Mode | What it is | Fits |
|---|---|---|
| **None** | the library has no portal | private libraries, the default |
| **Static site** | the feed rendered as a static website and copied to any static host (GitHub Pages, Netlify, an S3 bucket, an existing website). Search runs in the browser (Pagefind). A members side is a second build behind the host's own login (e.g. Cloudflare Access, Netlify password protection), or isn't published at all. | clubs, small historical societies, anyone who wants nothing of theirs running on the internet |
| **Live portal** | a read-only web app over a receiving library, with member logins and live search | guest networks, a car's screen or a phone, libraries and organizations that want accounts and frequent updates |

The originals, the private side and the OCR pipeline stay with the upstream library in every case.

## Cloud AI

The same tiers govern what an MCP client may see: public content can be offered to cloud AI tools such as Claude,
members content only to authenticated local clients, and private content never.

## Build order

1. Visibility fields, policies and effective visibility in the library schema, from the first version.
2. Libraries as named instances (each with its own data directory, config and network address), so a deployment
   can run several from the start.
3. Publisher and **static site** from a feed, with Pagefind search: needed first, for the Branford Garden Club.
4. **Library-to-library feeds** (guest library, car library), including offline sync.
5. Live portal with member logins.
