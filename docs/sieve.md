# Sieve: the OCR service

Sieve runs on the **M1 Mac mini** and reads page images for every capture station in the house. It serves the NATS
subject **`ocr.page`** (request/reply) with DeepSeek-OCR-2 8-bit through MLX, in process, plus Apple Vision as a
fast tier 0. Derived from the HaberDash `gpu.ocr` worker and myvect's `OcrClient`; see [backend.md](backend.md).

Status: `ocr.page` implemented and tested. The durable `ocr.jobs` queue comes next.

## Run it

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e ".[sieve,dev]"
.venv/bin/sieve serve                          # connects to nats://127.0.0.1:4222, serves ocr.page
.venv/bin/sieve ping                           # from any machine with the client installed
.venv/bin/sieve ocr page.jpg                   # OCR a page through the service
.venv/bin/sieve local page.jpg --tiles auto    # OCR in process, no NATS
```

The first run downloads the model (4 GB) to the Hugging Face cache; loading takes a few seconds after that, and
the worker holds about 5.5 GB while serving. Loading never runs the model repo's own Python code.

Settings come from flags or environment: `SIEVE_NATS_URL`, `SIEVE_NATS_CREDS` (an nsc `.creds` file),
`SIEVE_NATS_TLS_HOSTNAME`, `SIEVE_SUBJECT` (`ocr.page`), `SIEVE_QUEUE` (`sieve`), `SIEVE_MODEL`
(`mlx-community/DeepSeek-OCR-2-8bit`).

To start it at login on the M1, copy [`deploy/macos/org.athena-at-home.sieve.plist`](../deploy/macos/org.athena-at-home.sieve.plist)
to `~/Library/LaunchAgents/`, edit the paths, and `launchctl load` it. (A LaunchAgent rather than a system daemon,
because Apple Vision and Metal need a logged-in session.)

### NATS server setting

A page image sent as base64 is usually 100–700 KB, and NATS refuses messages over its `max_payload`, which
defaults to 1 MB. Set it on the home server:

```
max_payload: 8MB
```

The client checks the limit before sending and says so if a page is too big. Stations should send JPEG (quality
~90), not PNG.

## The `ocr.page` contract

Request (JSON):

| field | default | |
|---|---|---|
| `image_b64` | required | base64 JPEG/PNG, or a `data:` URL |
| `mode` | `markdown` | `markdown` or `free` (plain text) |
| `prompt` | | overrides the mode's instruction |
| `tiles` | `auto` | `auto` or 0–6 detail tiles |
| `tier0` | `true` | include Apple Vision's text in the reply |
| `max_tokens` | 3000 | generation cap (loop guard) |
| `id` | | echoed back |

`{"ping": true}` returns `{"pong": true, "model", "worker"}`.

Reply: `text` (markdown), `model`, `worker`, `tiles`, `tile_reason`, `seconds`, `gen_tokens`, `image_tokens`,
`looped`, `hit_cap`, `tier0` (`{"text", "chars"}` or null), `id`; or `{"error": "…"}`. A worker answers errors
instead of crashing, and the client doesn't retry an error the worker reported, only timeouts and connection
failures.

Python:

```python
from sieve.client import SieveClient
reply = SieveClient("nats://m1-mini.local:4222", creds="~/.nats/station.creds").ocr(open("page.jpg", "rb").read())
print(reply["text"])
```

## Tile policy (`tiles: auto`)

From Phase 0: the 1024 px overview alone (0 tiles) halves OCR time and stays exact on sparse pages, while dense
tables need all 6 tiles and small print can be misread without them (a W-2 label came out "Employer's" instead of
"Employee's" at 0 tiles; correct at 2 and 6). So Sieve reads Apple Vision's lines first (~0.2 s) and uses 0 tiles
only for pages that are **clearly sparse**: under 900 characters, at most 6 number-heavy rows, a median line height
of ~11 pt or more, and at most one small-text line. Everything else, and any page without tier-0 signals, gets 6.
Thresholds are in `sieve/tiles.py` and were calibrated on the synthetic Phase 0 set only.

## Tests

```bash
.venv/bin/python -m pytest                     # policy, contract, NATS worker + client (fake model); see development.md
SIEVE_E2E=1 .venv/bin/python -m pytest -m e2e -s   # real model over NATS on the Phase 0 pages
```

The NATS tests start a throwaway `nats-server` (`brew install nats-server`). The end-to-end test needs the
Phase 0 pages rendered (`phase0/.venv/bin/python phase0/gen_testset.py`).

End-to-end result on the 16 GB M3 laptop, camera captures at 150 DPI, tiles on auto: **every key field exact on
all six documents**; bank statement 6 tiles 12.4 s, utility bill 0 tiles 3.1 s, insurance letter 0 tiles 2.6 s,
receipt 6 tiles 5.2 s, intake form 0 tiles 2.4 s, W-2 6 tiles 5.8 s. **31.6 s for the set, against ~67 s at a
fixed 6 tiles.**

## Next

- `ocr.jobs`: durable JetStream queue for whole capture bundles, with retry and a dead-letter subject (instead of
  the HaberDash worker's ack-on-error).
- Measure on the M1 itself, and the same tests with real captures from the desk station.
