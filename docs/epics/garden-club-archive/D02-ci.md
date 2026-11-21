# D02 · CI: tests on Linux, macOS-only skips, SPDX and no-real-records guards

| | |
|---|---|
| **ID** | D02 |
| **Type** | code |
| **Owner role** | ops agent |
| **Size** | 1 day |
| **Depends on** | — |
| **Week** | W1 |
| **Branch** | `feat/ci` (from `main`) |

## Why

Every code deliverable needs tests that run somewhere other than the author's laptop, and two rules (SPDX headers,
no real records in git) are cheaper to enforce mechanically than by review.

## Inputs

- `pyproject.toml`, `tests/`, `sieve/` from `feat/sieve` (31 tests; MLX and Apple Vision paths, a throwaway
  `nats-server` fixture).
- `phase0/gen_testset.py` (uses macOS system fonts).
- `.gitignore` (data patterns).

## Outputs

- `.github/workflows/ci.yml`: on push and pull request, Ubuntu, Python 3.11 and 3.12.
- `scripts/check_spdx.py` and `scripts/check_no_records.py`, run in CI and usable as pre-commit hooks.
- pytest markers `macos`, `mlx`, `e2e` with automatic skips off Apple Silicon.
- Font fallback in `gen_testset.py` so fixtures render on Linux.
- A root-anchored `.gitignore`.

## Steps

1. **Workflow**: `uv` install of `.[client,dev]` (not `.[sieve]`; MLX and pyobjc are macOS-only); install
   `nats-server` (pinned release binary) so the NATS tests run; `pytest -m "not e2e"`.
2. **Skips**: register markers in `pyproject.toml`; in `tests/conftest.py`, skip `mlx` unless
   `sys.platform == "darwin"` and arm64 and `mlx_vlm` imports; skip `macos` (Apple Vision) off darwin. Tests must
   **skip, not error**, when the import is missing. Sieve's existing tests that use `FakeEngine` must still run on
   Linux.
3. **Fonts**: `gen_testset.py` picks macOS fonts when present, else DejaVu/Liberation (`fonts-dejavu-core`,
   `fonts-liberation` in CI). Ground truth doesn't depend on the font, so fixtures stay valid.
4. **SPDX check**: every `*.py`, `*.sh` and workflow file carries `SPDX-License-Identifier: GPL-3.0-or-later`; a
   file whose header says "Derived from" must name a source.
5. **No-real-records guard** (`check_no_records.py`) fails the build if the diff adds:
   - image, PDF, HEIC, TIFF, DNG or SQLite files outside `tests/fixtures/` and `phase0/` synthetic paths;
   - any file over 2 MB;
   - image files whose EXIF has GPS or a camera make/model (real captures; synthetic fixtures have none);
   - paths under data directories (`captures/`, `originals/`, `scans/`, library data dirs).
6. **`.gitignore`**: anchor the data patterns to the repo root (`/library/`, `/originals/`, …). As written,
   `library/` also ignores any code package named `library` (e.g. `athena/library/`, which D10 creates), which
   would silently drop it from commits.
7. Document how to run the same checks locally in `docs/development.md` (short).

## Acceptance criteria

- CI is green on `feat/ci` rebased onto `feat/sieve` (Sieve's tests included): everything that doesn't need MLX, Apple Vision or the model
  runs on Linux; the rest is reported as skipped with a reason.
- A test commit that adds a JPEG with GPS EXIF, or a 3 MB file, or a `.sqlite` file, fails CI with a clear
  message (verified on a throwaway branch, then deleted).
- A Python file without the SPDX header fails CI.
- `git check-ignore athena/library/x.py` returns nothing; `git check-ignore library/x.sqlite` matches.
- CI runs in under 5 minutes.

## Test plan

Unit tests for both check scripts with fixture files generated in `tmp_path` (a JPEG with GPS EXIF written by
Pillow, a large file, a header-less file). The workflow itself is verified by the green run and the deliberate red
runs above.

## Privacy and rights

- [ ] No secrets in the workflow; nothing uploads artifacts containing test images beyond the synthetic set.
- [ ] No step calls a cloud OCR/AI service.

## Touches

`.github/workflows/ci.yml`, `scripts/`, `tests/conftest.py`, `pyproject.toml` (markers), `phase0/gen_testset.py`
(fonts), `.gitignore`, `docs/development.md`.

## Done means

`feat/ci` is pushed, CI is green on it, the deliberate red runs are recorded in the report back, and every later
branch inherits the workflow (the author merges it first, so it reaches `main` before the other branches).
