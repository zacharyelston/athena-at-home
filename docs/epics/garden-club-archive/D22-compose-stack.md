# D22 · Compose stack and the `athena-postgres` image

| | |
|---|---|
| **ID** | D22 |
| **Type** | code + ops |
| **Owner role** | ops agent (the author installs Docker Engine on the Ubuntu mini) |
| **Size** | 3 days |
| **Depends on** | D02 (CI), D04 (NATS config, which this stack runs) |
| **Week** | W1–W2 |
| **Branch** | `feat/compose-stack` (from `feat/ci`) |

## Why

[ADR 0001](../../adr/0001-containers-and-postgres.md): every server-side module runs as a container with its data on
mounted volumes; Docker Compose is the first draft, a Kustomize-deployed Namespace per library in a home cluster is
the target; PostgreSQL is each library's database. This deliverable lays down that structure so D10, D13, D15, D16
and D17 build into it, and so the Kustomize base later is a translation.

## Inputs

- ADR 0001 (the mapping table from compose to Kustomize).
- D04's NATS config, nsc setup and certs.
- `pyproject.toml` (the `athena` and `sieve` packages).

## Outputs

- `images/postgres/Dockerfile` → **`athena-postgres`**: the official `postgres` image at a pinned major, plus
  `pgvector`, `pg_trgm` (contrib) and optionally Apache AGE, from the PGDG packages; an init script that creates the
  extensions; no data or passwords baked in. Choose the newest PostgreSQL major that every included extension
  packages, and record the versions in the image's labels.
- `images/athena/Dockerfile` → **`athena`**: Python 3.12 slim, `uv`-installed `athena` (library, publisher)
  without the MLX extras; non-root user; one image, several commands (`athena library serve`, `athena publish …`).
  Pagefind and libvips added when D15/D16 need them.
- `deploy/compose/`:
  - `site.yml` (project `athena-site`): `nats` with D04's config, JetStream volume, TLS certs and nsc resolver
    mounted read-only; a named network `athena-site` other projects join. (D04 owns the NATS service definition.)
  - `library.yml` (one project per library, e.g. `athena-garden`): `db` (`athena-postgres`, volume `pg`), `library`
    (placeholder command until D10 lands: a health endpoint only), `publisher` (profile `tools`, run on demand),
    `backup` (profile `tools`, filled in by D17). Volumes: `pg`, `files` (originals and derivatives), `intake`
    (bind mount, so capture stations can drop bundles), `site` (publisher output).
  - `example.env` (library name, ports, LAN bind address, volume paths, NATS URL and creds path); `secrets/`
    git-ignored, with an example layout.
  - `athena-compose`: a thin wrapper, `athena-compose garden up` → `docker compose -p athena-garden --env-file
    garden.env -f library.yml up -d`, so named instances are one command each.
  - Health checks on every service; `restart: unless-stopped`; the review UI port bound to the LAN address only.
- `deploy/compose/README.md`: install Docker Engine and the compose plugin on Ubuntu (not Docker Desktop), where
  volumes live (the mini's encrypted disk), start/stop/upgrade, and the compose → Kustomize mapping from the ADR.
- **CI**: build both images on every push (no registry push yet; see the open question on a registry), and a compose
  smoke job: bring up `site` and one library, wait for health, check the extensions exist in `db`
  (`SELECT extname FROM pg_extension`), tear down.
- A `postgres` service container in the CI test job and an `ATHENA_TEST_DATABASE_URL` convention, so D10's tests
  run in CI; tests that need a database skip with a reason when it isn't set.

## Steps

1. Postgres image: pick the major, install extensions, init script, labels; check with `psql`.
2. App image: multi-stage build with `uv`, non-root, health command.
3. Compose files, env example, secrets layout, wrapper script.
4. CI: image builds, compose smoke job, Postgres service for tests.
5. On the Ubuntu mini (with the author): Docker Engine installed, `athena-site` and `athena-garden` up, volumes on
   the encrypted disk, the review UI port reachable from a laptop on the LAN and not from outside.

## Acceptance criteria

- `athena-compose garden up` and `athena-compose test up` run two isolated libraries side by side: separate
  databases, volumes and ports, one shared NATS.
- `db` has `vector` and `pg_trgm` (and `age` if included); data survives `down` + `up`; nothing is lost on
  `docker compose pull && up`.
- No image contains a password, key, hostname, club or household name (checked by grepping the image filesystem in
  CI) or runs as root.
- CI builds both images and the compose smoke job passes.
- On the mini: the stack restarts after a reboot; the UI port is bound to the LAN address only (checked with `ss`).

## Test plan

The CI smoke job; a `tests/test_compose_config.py` that parses the compose files and checks the rules (health
checks present, no `privileged`, no host network, LAN-only port binding, every volume named); the manual run on the
mini.

## Privacy and rights

- [ ] Volumes on encrypted disks only; the README says how to check.
- [ ] Secrets as files outside git; images carry none.
- [ ] No registry push of images until the author decides where (open question); nothing leaves the machines.
- [ ] Nothing from the author's work infrastructure (no AKS manifests, no work registry, no work NATS).

## Touches

`images/`, `deploy/compose/`, `.github/workflows/ci.yml`, `tests/test_compose_config.py`, `.gitignore`
(`deploy/compose/secrets/`), `docs/development.md`.

## Done means

Two named libraries and a shared NATS run from compose on the Ubuntu mini, built from images CI produces, with
PostgreSQL carrying the extensions the library needs, and the layout documented so the Kustomize base can follow.
