# ADR 0001: Containers first; PostgreSQL as each library's database

Status: **accepted**, 2026-11-21 (the author); **amended** the same day: native first on Apple Silicon, containers as
the destination (see [Amendment 1](#amendment-1-native-first-on-apple-silicon-containers-as-the-destination)). Supersedes the "SQLite with FTS5 to start" and "`nats-server` as a
systemd service" parts of [design.md](../design.md), [backend.md](../backend.md) and the
[garden club epic](../epics/garden-club-archive/README.md).

## Context

Athena@Home's modules are composed per deployment ([design.md](../design.md#core-modules-composed-per-deployment)).
Deployments range from one 16 GB mini in a house or a club to a business or library that can afford a cluster to
manage its archive. The same modules have to run on both, and move from one to the other without a redesign.

The library's data also has more shapes than rows and columns:

| Shape | Example |
|---|---|
| records | collections, series, items, pages, OCR text with the engine that produced it |
| documents | fields that differ by deployment (a club item, a household statement, a car manual) |
| full text | exact words and numbers ("account ending 4821") |
| meaning | "letters about the drought" (vectors) |
| graph | people, organizations, places and events across records; who is in which photo |
| provenance | "OCR said 1962, the archivist said 1961": every assertion with its source, never overwritten |
| intervals | EDTF dates are ranges (`1962-21`, `196X`, `1960/1965`) |
| places | later: plots, the town green, a map |
| files | originals (write-once, content-addressed) and derivatives |
| events | `library.events`, feeds to other libraries and portals |

## Decision

1. **Every server-side module is a container image** configured only by environment variables and mounted files,
   with its data on **mounted persistent volumes**. The first draft is **Docker Compose**. The target is a
   **Kustomize** base with one overlay per library, each library a **Namespace** in a home cluster (k3s on a mini, or
   a larger cluster). The compose layout is written so the Kustomize base is a translation, not a redesign.
2. **PostgreSQL is each library's database**, one server per library instance, covering the shapes above with
   built-ins and extensions instead of separate servers:

   | Shape | In PostgreSQL |
   |---|---|
   | records | tables |
   | documents | `jsonb` |
   | full text | `tsvector` with GIN indexes; `pg_trgm` for fuzzy matches |
   | meaning | `pgvector` |
   | graph | entity and relation tables with recursive queries; **Apache AGE** (Cypher) if and when queries need it |
   | provenance | an append-only `assertion` table (below) |
   | intervals | `daterange` plus earliest/latest columns from the EDTF parser |
   | places | PostGIS, later |

   SQLite is no longer a target for the library. (Pagefind on the static site is a browser-side index, not a
   database, and stays.)
3. **The truth is PostgreSQL**, with provenance built in: an append-only `assertion` table records who said what
   about which record, when, and from what source (a capture station, an OCR engine, the archivist), and
   `UPDATE`/`DELETE` on it are refused by trigger. Current-state tables are updated in the same transaction. A
   **transactional outbox** publishes `library.events` to NATS JetStream, so events are never lost or invented.
   Current state can always be rebuilt from the assertions.
4. **Files live on a mounted filesystem volume**: originals content-addressed and write-once, derivatives
   regenerable. On a single node that's a ReadWriteOnce volume; on a multi-node cluster, ReadWriteMany or an
   S3-compatible object store behind the same small storage interface.
5. **NATS runs as a container in the site stack**, one per site or cluster (`athena-system` namespace in
   Kubernetes), with an nsc account per library and JetStream on a volume. Libraries and Sieve address it by
   configuration.
6. **Sieve has two forms, by hardware.**
   - **Apple Silicon: native**, under launchd, outside any container. Linux containers on macOS (Docker Desktop, or
     Apple's own `container` tool, which runs each container in a lightweight VM) get no Metal GPU, so MLX can't run
     in them. Sieve on the M1 connects to the site's NATS over TLS.
   - **NVIDIA: a container image** backed by vLLM and CUDA (the HaberDash path), for deployments with NVIDIA GPUs:
     a GPU node in a cluster, or a GPU box running compose. Same `ocr.page` / `ocr.jobs` contracts, same queue
     group, so native and containerized workers can serve one site together. Built after the garden club launch.
7. **Capture stations stay native** (laptops, phones). In the first draft, bundles reach a library through a
   bind-mounted intake directory; in a cluster, through an upload endpoint (later).

## How the pieces map

| Concept | Docker Compose (first draft) | Kustomize (target) |
|---|---|---|
| site services | project `athena-site`: `nats` | base `site` → namespace `athena-system` |
| a library named `garden` | project `athena-garden` (`docker compose -p athena-garden`) with its own env file | overlay `garden` → namespace `athena-garden` |
| database | service `db` (image `athena-postgres`), named volume | StatefulSet + PVC, or a CloudNativePG `Cluster` |
| library API, review UI, intake, OCR ingest, outbox | service `library` (image `athena`) | Deployment + Service + an internal-only Ingress |
| publisher | `docker compose run --rm publisher …` | Job / CronJob |
| files (originals, derivatives) | named volume (or bind mount on the mini's encrypted disk) | PVC |
| config | env file + mounted `library.toml` | ConfigMap |
| secrets (DB password, NATS creds) | compose `secrets:` files | Secret (sealed or SOPS-encrypted later) |
| backups | `backup` service: `pg_dump` + restic | CloudNativePG backups or a CronJob, + restic for the files PVC |

Rules for every image: one process per container; no host paths, hostnames, club or household names baked in; a
health endpoint; runs as a non-root user; built in CI and pinned by digest in deployments.

## Consequences

Good:

- One set of modules from a mini to a cluster; a business can give its library more nodes without code changes.
- Multiple writers, real full-text and vector search, graph queries and provenance without extra servers.
- One backup method per kind of data (database dumps or base backups; restic for volumes).
- Named instances map cleanly: a compose project or a namespace, each with its own volumes and config.

Costs and risks:

- **Heavier on a mini**: a Postgres per library and a container runtime. Fine on 16 GB for a few libraries.
- **Extension version skew**: Apache AGE tends to support new PostgreSQL majors late. Pin a PostgreSQL major that
  every extension in the image supports (check when the image is built), and keep AGE optional, since the graph
  starts as plain tables.
- **Tests need PostgreSQL**: a service container in CI and the compose `db` locally; tests that need it skip with a
  reason when no database is configured, like the MLX tests do.
- **Offline libraries** (a car) are harder with a database server. Revisit with library-to-library feeds: a feed is
  files, so a small receiving library could still be static files or a small database.
- **More moving parts to operate** for whoever helps an archivist. The handover guide covers the commands; the
  archivist only uses the web UI.

## Alternatives considered

- **SQLite per library** (the previous plan): simplest to operate, but single-writer, with weaker full-text, vector
  and graph support, and no path to a business cluster.
- **A store per shape** (Neo4j, OpenSearch, Qdrant, …): each is another server to secure, back up and upgrade.
- **Multi-model databases** (ArangoDB, SurrealDB): now under the Business Source License, which isn't free software
  and doesn't fit a GPL-3.0-or-later project.
- **JetStream as the event-sourced source of truth**: writing to the log and to query tables would be two writes that
  can disagree; an append-only table plus an outbox in PostgreSQL gives the same provenance with transactions.
- **Kubernetes from the start**: too much for the garden club's January launch. Compose comes first, laid out so the
  Kustomize base follows.

## Amendment 1: native first on Apple Silicon, containers as the destination

2026-11-21, the author. A separate exploration is building an endpoint through which containerized apps on a Mac
could reach the GPU (Metal). Until it reports back:

- **Build and run Athena@Home natively on macOS with Metal now.** Sieve runs under launchd with MLX (as decision 6
  already says); the library, publisher and NATS run as native processes too: PostgreSQL and `nats-server` from
  Homebrew on a Mac, or distribution packages on Linux, each under launchd or systemd.
- **Containers remain the final destination**, as multi-arch images (`linux/arm64` and `linux/amd64`), Compose
  first and Kustomize-deployed Namespaces after. If the GPU endpoint works, Sieve on a Mac may become a container
  too; on NVIDIA it already will be.
- **So the code stays container-ready**: configuration only from environment variables and one config file; every
  data path (files, intake, database URL, NATS URL and creds) is a parameter; no hostnames, users or install paths
  assumed; one process per service; health endpoints; no reliance on macOS-only APIs outside Sieve's Metal and
  Apple Vision code. Native service definitions (launchd plists, systemd units) are thin wrappers that a container
  entrypoint replaces later.
- **PostgreSQL stays the database** (decisions 2 and 3 unchanged). Natively, any supported major from 14 up works
  for the library core; the image will pin one major when containers resume. Tests start a throwaway PostgreSQL
  with `initdb` (as they already start a throwaway `nats-server`), so they need no running server.
- **D22 (the compose stack) waits** until containers resume; D04's NATS and D10's library run natively meanwhile.
