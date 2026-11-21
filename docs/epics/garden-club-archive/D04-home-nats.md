# D04 · Home NATS: the site's NATS container

| | |
|---|---|
| **ID** | D04 |
| **Type** | ops |
| **Owner role** | ops agent (the author holds the operator signing key) |
| **Size** | 2 days |
| **Depends on** | — |
| **Week** | W1–W2 |
| **Branch** | `feat/home-nats` (from `feat/ci`) |

> **Native first** ([ADR 0001, amendment 1](../../adr/0001-containers-and-postgres.md#amendment-1-native-first-on-apple-silicon-containers-as-the-destination)): until containers resume, NATS runs as a native
> `nats-server` (Homebrew on a Mac, the release binary on Linux) under launchd or systemd, with the same config,
> accounts, TLS and JetStream. The compose service follows with D22.

## Why

Sieve, the library and capture stations talk over NATS. The deployment needs its own server, with its own operator
and accounts, TLS and JetStream (for `ocr.jobs`), and no link to any outside hub. Per
[ADR 0001](../../adr/0001-containers-and-postgres.md) it runs as a **container in the site stack** (compose project
`athena-site` now; the `athena-system` namespace in a home cluster later), shared by every library at the site.

## Inputs

- The Ubuntu Intel mini (16 GB), on the home LAN, library disk encrypted (LUKS), with Docker Engine (D22).
- The nsc recipe pattern from HaberDash `docs/nats-fabric-for-other-projects.md` (pattern only; nothing from the
  work hub: no operator, account, key, hostname or leafnode from it).
- Sieve's settings: `SIEVE_NATS_URL`, `SIEVE_NATS_CREDS`, `SIEVE_NATS_TLS_HOSTNAME`.

## Outputs

- `deploy/nats/`: a templated `nats-server.conf`, `setup-operator.sh` (nsc), `make-certs.sh` (a small private CA
  with `openssl` or `step`), `smoke.sh`, and a README.
- The `nats` service in `deploy/compose/site.yml` (official `nats` image at a pinned version): config, certs and
  account JWTs mounted read-only, JetStream on a named volume, port 4222 published on the LAN address only,
  monitoring not published, a health check, `restart: unless-stopped`.
- Deployment-specific values come from one env file (operator name, hostnames, account list, data dir); the
  repo holds an example, never real keys or certs.
- On the Ubuntu mini: the `athena-site` stack running, JetStream on, TLS on, `max_payload: 8MB`.
- Users and `.creds` files: `sieve` (OCR account), `library` (LIBRARY account), `station` (CAPTURE account).

## Steps

1. **Accounts by configuration.** The design names `CAPTURE`, `OCR`, `LIBRARY`, `FINANCE` for the household;
   this deployment needs `CAPTURE`, `OCR`, `LIBRARY`. The script takes the list from the env file. The operator's
   name is configuration too (e.g. `athena-<deployment>`).
2. **Exports and imports** so accounts can only do what they need: `OCR` exports the `ocr.page` service and the
   `ocr.jobs` stream subjects; `CAPTURE` and `LIBRARY` import them; `LIBRARY` exports `library.events` (stream).
   JetStream enabled for `OCR` and `LIBRARY` with explicit limits (memory, disk).
   **Decide and document one thing**: D09's stream (`OCR_JOBS`) and Object Store buckets (`pages-in`, `text-out`)
   live in `OCR`, and the library must put pages and read results. Cross-account JetStream needs the stream's and
   buckets' JetStream API subjects exported and imported, which is fiddly. If it is, give the library a user
   *inside* `OCR` whose permissions allow only those API subjects and `ocr.jobs.>`, and say why in the README.
3. **Server config**: `max_payload: 8MB` (pages are 100–700 KB as base64; D07 sends JPEG q90 OCR derivatives),
   JetStream `store_dir` on the encrypted disk, TLS with the private CA (server cert for the mini's LAN name and
   IP), listen on the LAN interface only, monitoring port bound to localhost, `no_auth_user` unset, no leafnodes,
   no gateways, no websockets.
4. **Container**: the `nats` service in `site.yml`, non-root, read-only root filesystem, the JetStream volume the
   only writable path, the `athena-site` network for libraries' containers to join. Clients on the LAN (Sieve on
   the M1, capture stations) reach it on the published port; containers reach it by service name. One account per
   library (`LIBRARY_<NAME>`) so a second library on the site gets its own, by configuration.
5. **Keys**: the operator and account signing keys stay with the author (offline copy in D17's backups, encrypted);
   the server holds only what it needs (resolver preload or `MEMORY` resolver with the account JWTs).
6. **Firewall**: `ufw` allows 4222 from the LAN subnet only (Docker's published ports bypass `ufw` unless bound to
   the LAN address, so bind it explicitly and check from outside the subnet).
7. Verify from the M1: Sieve connects with `sieve.creds` and TLS; `sieve ping` from a laptop with
   `station.creds` succeeds; a `station` user can't subscribe to `library.events`' internals or publish as
   `ocr.page` replies.

## Acceptance criteria

- `nats-server -t -c nats-server.conf` passes (config test, also run in CI from the example env).
- Sieve on the M1 serves `ocr.page` through this server with TLS and creds; a laptop's `sieve ping` and
  `sieve ocr page.jpg` (a synthetic page) succeed.
- A 6 MB message is accepted; a 9 MB message is refused with a clear client-side error.
- JetStream is available to `OCR` and `LIBRARY` (`nats account info` shows it), not to `CAPTURE`.
- Permission tests: `station` can request `ocr.page` but can't subscribe to `ocr.>` as a worker; unauthenticated
  connections are refused; plaintext connections are refused.
- The container restarts on failure and after a reboot; JetStream data survives `down` + `up`.
- Nothing in the repo is a real key, cert or creds file (D02's guard plus a `*.creds`/`*.nk`/`*.pem` ignore rule).

## Test plan

A shell script `deploy/nats/smoke.sh` that runs the checks above with the `nats` CLI against the live server;
`nats-server -t` on the example config in CI; manual reboot test.

## Privacy and rights

- [ ] No leafnode, gateway or account from the work hub; no Ancera hostnames.
- [ ] Monitoring endpoint not exposed beyond localhost.
- [ ] No message content in server logs (default; don't enable tracing in production).

## Touches

`deploy/nats/` (new), `deploy/compose/site.yml` (the `nats` service; D22 owns the rest of `deploy/compose/`),
`.gitignore` (key and cert patterns), `docs/sieve.md` (pointer to the home server settings).

## Done means

The site's NATS runs as a container on the Ubuntu mini with TLS, JetStream and per-purpose accounts; Sieve on the
M1 (native, outside the containers) serves through it; the setup is reproducible from `deploy/nats/`,
`deploy/compose/site.yml` and an env file.
