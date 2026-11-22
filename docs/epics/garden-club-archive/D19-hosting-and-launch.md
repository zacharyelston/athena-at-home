# D19 · Hosting, domain and launch

| | |
|---|---|
| **ID** | D19 |
| **Type** | ops |
| **Owner role** | ops agent (comparison, deploy tooling, audit); the club creates and owns the accounts; author deploys |
| **Size** | 2 days (options in W4; setup W7; launch W8) |
| **Depends on** | D16 (site builds), D18 (the club's choices); Q5, Q6, Q7, Q12 |
| **Week** | options with the preview (Dec 18); setup W7; **launch Wed Jan 13**; slack to Sun Jan 17 |
| **Branch** | `feat/site-deploy` (from `feat/static-site`) |

## Why

The club has no website. It needs a host it owns and can afford (ideally free), a domain if it wants one, a way to
deploy updates and take things down, and a launch that ships exactly what the club chose.

## Inputs

- D16 public and members builds, their size and **file count**.
  Measured in D16 on a made-up feed of 800 items and 2,787 pages: **10,025 files and 3.6 GB** (about 3.6 files
  and 1.3 MB a page; real photographs may compress differently, so measure the real build). The site needs nothing
  from a host but static files: links are relative, its security policy is a `<meta>` tag rather than a header,
  `404.html` is at the top level, and `athena publish diff` lists the paths to purge after a takedown.
- The club's answers: host (Q5), domain (Q6), members side (Q7), takedown contact (Q12).

## Outputs

1. **Host comparison** (`docs/hosting.md`, generic for any deployment), given to the club with the preview, no
   option pre-selected. Columns: cost, custom domain and HTTPS, members login option, file-count and size limits,
   account ownership and handover, cache purge. Candidates: Cloudflare Pages (members side via Cloudflare Access),
   Netlify (password protection on paid plans), GitHub Pages (public only), the club's or registrar's own static
   hosting. **Check current terms when writing it**; limits and free tiers change. Note that some hosts cap files
   per deployment (Cloudflare Pages does), which is why D15 defaults to a low-file-count zoom pyramid.
2. **Accounts owned by the club**: the club (or the archivist on its behalf) creates the host and registrar
   accounts with a club email and adds the author as a collaborator. Agents never create accounts or handle
   passwords; deploy tokens live in the author's password manager and are passed through the environment.
3. **Domain** (if wanted): registered in the club's name, auto-renew on, registrant privacy on, DNS pointing at the
   host, HTTPS enforced. Renewal date and cost written into the handover (D20).
4. `athena site deploy <build> --target <name>`: a thin wrapper over the host's own CLI (configured per target in
   `library.toml`), which refuses to deploy a build that fails `site check` or has a preview banner, and after
   deploying runs `publish diff` against the last deployed build and purges removed paths from the host's cache.
5. **Members side** (if wanted): deployed as a separate site/project behind the host's login, with the member list
   managed by whoever the club names; `noindex` everywhere.
6. **Takedown procedure** in `docs/hosting.md`: unshare in the review UI → rebuild → deploy (purges) → confirm the
   page and its images return 404 → reply to the requester. The club sets the response time it promises.
7. **Pre-launch audit** checklist, run on the exact builds that ship.

## Pre-launch audit (W8, before deploy)

- [ ] `athena site check` passes on the public build and the members build.
- [ ] Builds made from the library after the club's choices (D18) were entered; the build info shows the date.
- [ ] The archivist has walked the public build (preview URL, behind auth) and signs off.
- [ ] Members site refuses a private browser window without login.
- [ ] No preview banner; `robots` as the club chose for the public site; `noindex` on members.
- [ ] Takedown contact address receives mail.
- [ ] Restore test passed (D17).
- [ ] Rights statements present on every item page.

## Steps

1. W4: host comparison doc; preview deployed behind auth if the club picks a host by then, else LAN only.
2. W7: accounts created by the club; domain registered; deploy targets configured; a dry-run deploy of the
   preview behind auth.
3. W8 Mon–Tue: pre-launch audit; fixes.
4. **Wed Jan 13**: deploy public (and members); DNS and HTTPS verified; the club announces when it chooses.
5. W8 rest: watch for problems; first takedown drill on a test page (publish, then take down, timed).

## Acceptance criteria

- The site is live at the club's chosen address with HTTPS; the members side (if chosen) requires login.
- `site deploy` refuses a failing or preview build (tested with a deliberately failing build).
- The takedown drill removes a page and its images from the live site (including cache) and is timed.
- All accounts are owned by the club; the author is a collaborator; credentials are in password managers, not in
  files.

## Test plan

Unit tests for `site deploy`'s refusal rules and its diff/purge step (with a fake target); the dry-run deploy; the
launch itself and the takedown drill as the experiment.

## Privacy and rights

- [ ] The club owns the accounts and domain; no personal account of the author's is the owner.
- [ ] Registrant privacy on, so no member's home address is published in WHOIS.
- [ ] No analytics or third-party scripts added by the host (turn off any host "analytics" injection).
- [ ] Only the publisher's builds are uploaded; never the library, originals or feeds' logs.

## Touches

`athena/site/deploy.py`, `docs/hosting.md`, `tests/test_deploy*.py`, `library.toml` example (deploy targets).

## Done means

The club's site is live on infrastructure the club owns, showing exactly what it chose, with a tested way to take
things down, by Wed Jan 13 (and no later than Sun Jan 17).
