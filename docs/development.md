# Development

## Set up

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e ".[client,library,dev]"  # any platform
uv pip install --python .venv/bin/python -e ".[sieve,client,dev]"    # Apple Silicon: adds MLX and Apple Vision
brew install nats-server                                             # or a release binary on Linux
```

## Tests

```bash
.venv/bin/python -m pytest                       # everything that can run here; the rest skips with a reason
.venv/bin/python -m pytest -rs                   # ... and list the skips
SIEVE_E2E=1 .venv/bin/python -m pytest -m e2e -s # real model over NATS (Apple Silicon; see docs/sieve.md)
```

Markers (registered in `pyproject.toml`, applied in `tests/conftest.py`):

| marker | runs when |
|---|---|
| `mlx` | macOS on Apple Silicon with `mlx-vlm` installed |
| `macos` | macOS with Apple Vision (`pyobjc-framework-Vision`) |
| `e2e` | `SIEVE_E2E=1`, the model downloaded and the Phase 0 pages rendered |

Library tests need PostgreSQL: they start a throwaway server with `initdb` (Homebrew's `postgresql@14` or later on a
Mac, the `postgresql` package on Linux), or use `ATHENA_TEST_DATABASE_URL` if it's set, and skip otherwise.

Tests that need neither use fakes (`FakeEngine`, canned tier-0 lines) and run everywhere, including CI.

The synthetic test set (`phase0/gen_testset.py`) uses macOS system fonts, or Liberation/DejaVu on Linux
(`apt install fonts-liberation fonts-dejavu-core`). Set `TESTSET_OUT` to render somewhere other than
`phase0/testset/`.

## Guards

CI runs two checks; run them before committing (or as pre-commit hooks, which pass the staged file names):

```bash
.venv/bin/python scripts/check_spdx.py         # every .py, .sh and workflow file starts with the GPL SPDX header
.venv/bin/python scripts/check_no_records.py   # no real records, captures, databases or secrets
```

`check_no_records.py` fails on images, PDFs and databases outside `tests/fixtures/` and `docs/guides/img/`; any image
with GPS or a camera make/model in its EXIF (anywhere); any file over 2 MB; files under `captures/`, `originals/`,
`scans/` or `intake/`; and credentials, keys and `.env` files. Real records stay on the library's encrypted disk;
tests use synthetic fixtures, generated at test time where possible.

Code derived from the author's earlier projects says so under the SPDX line:

```python
# SPDX-License-Identifier: GPL-3.0-or-later
# Derived from the author's myvect (store.py, chunking.py) in HaberDash.
```

## CI

`.github/workflows/ci.yml`: the guards, then the tests on Ubuntu with Python 3.11 and 3.12 (`.[client,dev]`, a
pinned and checksum-verified `nats-server`). MLX, Apple Vision and end-to-end tests skip there.
