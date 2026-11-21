# D03 · Sieve on the M1 and ready for its PR

| | |
|---|---|
| **ID** | D03 |
| **Type** | code + ops |
| **Owner role** | ops agent (with the author at the M1 for the first login and FileVault check) |
| **Size** | 2 days |
| **Depends on** | D02 (CI runs on the PR); Q3 (merge order) |
| **Week** | W1 |
| **Branch** | `feat/sieve` (rebased onto `main`) |

## Why

Sieve (`ocr.page`) works on the M3 laptop with every key field exact on the synthetic set, but has never run on
the M1 Mac mini that will serve the club's OCR, and `feat/sieve` was cut from `main` before PR #3's design changes.
The pilot (D06) and all OCR after it run here.

## Inputs

- `feat/sieve`: `sieve/` (engine, tiles, tier0, guard, service, worker, client, CLI), `tests/` (31 + e2e),
  `deploy/macos/org.athena-at-home.sieve.plist`, `docs/sieve.md`.
- `main` after PR #3 merges (`docs/backend-plan`: design.md, portal.md, backend.md).
- The M1 Mac mini, 16 GB, FileVault on, logged-in user session (Apple Vision and Metal need one).

## Outputs

- `feat/sieve` rebased onto `main` with CI green, conflicts in `docs/design.md` resolved in favour of PR #3's
  text plus Sieve's status.
- Sieve running on the M1 as a LaunchAgent, reachable on the LAN (until D04, a local `nats-server` on the M1).
- An "M1 results" section in `docs/sieve.md`: per-page seconds by tile budget, set total, peak memory, load time;
  and a bf16 attempt (pass or fail, with the reason).
- A PR-ready report to the author (summary, test output, numbers). The author opens the PR.

## Steps

1. Rebase `feat/sieve` onto `main` (after PR #3, Q3); resolve `docs/design.md`; keep `docs/sieve.md`. Rebase on
   `feat/ci` too if it hasn't merged yet, and make the Sieve tests pass in CI with the D02 skip markers.
2. On the M1: Xcode command-line tools, `uv`, Python 3.12, `brew install nats-server`; clone; install
   `.[sieve,dev]`; first run downloads the 8-bit model (4 GB) to the Hugging Face cache.
3. Run the 31 tests, then render the Phase 0 set and run the end-to-end test (`SIEVE_E2E=1`). Record timings.
4. Try bf16 once (`SIEVE_MODEL=mlx-community/DeepSeek-OCR-2-bf16`) on the e2e set with nothing else running.
   Record peak memory and whether it completes. Keep 8-bit as the default unless bf16 fixes a real gap later.
5. Install the LaunchAgent (edit paths), confirm it restarts after a crash (`kill -9`) and after reboot + login.
   Set the M1 to log in automatically to a dedicated, standard (non-admin) user with FileVault on, and to never
   sleep while on power; document this in `docs/sieve.md`.
6. Check the client's `max_payload` guard against the defaults; note that D04 raises it to 8 MB.
7. Confirm in the logs that no page text is logged (only ids, sizes, timings).
8. Write the report.

## Acceptance criteria

- CI green on the rebased branch.
- On the M1: 31 tests pass; the end-to-end test gets every key field exact on all six documents with tiles on auto.
- M1 timings and peak memory recorded in `docs/sieve.md`; the set completes in under 3× the M3's 31.6 s (if not,
  record it and flag it: overnight OCR still works, but D12's estimates change).
- `sieve ping` from a Mac laptop on the LAN gets a pong through the M1's NATS.
- The LaunchAgent survives a kill and a reboot.
- bf16 outcome recorded either way.

## Test plan

The existing suite plus e2e on the M1; manual checks for the LaunchAgent and LAN ping; log grep for any page text.

## Privacy and rights

- [ ] FileVault on (page images pass through the M1 while OCRing).
- [ ] Only synthetic pages used in this deliverable.
- [ ] No connection to any outside NATS (no leafnode, no work hub); Hugging Face is contacted only to download the
      model, then `HF_HUB_OFFLINE=1` is set in the LaunchAgent.

## Touches

`sieve/` (only if the M1 run needs fixes), `tests/`, `docs/sieve.md`, `docs/design.md` (rebase),
`deploy/macos/org.athena-at-home.sieve.plist`.

## Done means

The author has a rebased, green `feat/sieve` with M1 numbers in `docs/sieve.md`, and Sieve is serving on the M1.
The author opens the PR.
