# D17 · Backups (3-2-1), restore test, BagIt export

| | |
|---|---|
| **ID** | D17 |
| **Type** | ops |
| **Owner role** | ops agent (the author holds the backup passphrase and the off-site copy) |
| **Size** | 2 days (setup in W4, restore test in W7) |
| **Depends on** | D10 (library data directory layout) |
| **Week** | W4 (setup), W7 (restore test) |
| **Branch** | `feat/backups` (from `feat/library-core`) |

## Why

The digital archive becomes the club's record of its history. Backups that have never been restored aren't
backups. And the club should hold its own copy in an open format that doesn't need Athena@Home to read.

## Inputs

- D10's data directory (`originals/`, `library.sqlite`, `library.toml`, `derivatives/`, `logs/`).
- D04's NATS operator and account keys, the CA, the creds files.
- Session raw folders on the second disk (D12) until they're superseded by backups.
- Q11 (off-site location).

## Outputs

- `deploy/backup/`: `restic` (BSD-2) configuration, a systemd timer on the Ubuntu mini, an `athena library
  snapshot` command that makes a consistent SQLite copy (SQLite backup API, not a file copy) before each run.
- **3-2-1**:
  1. the library on the Ubuntu mini's encrypted disk (primary);
  2. a nightly restic repository on a local encrypted USB disk;
  3. an off-site restic repository (Q11): a second encrypted disk rotated to another home (the archivist's), or a
     cloud bucket that only ever receives restic's client-side-encrypted data. Either way the passphrase never
     leaves the author's (and, at handover, the club's) password manager.
- What's backed up: originals, the SQLite snapshot, `library.toml`, audit logs, NATS keys and creds (separately,
  as an encrypted archive), deploy configs. Not backed up: derivatives, feeds and site builds (rebuilt from the
  library), JetStream data (transient).
- `athena library export bagit <dir>`: a **BagIt** bag (RFC 8493) of originals plus metadata as CSV and JSON per
  item, with a README explaining the layout, checksummed. The club gets one on a disk at handover (Q13).
- `docs/backups.md`: the schedule, where each copy lives, how to restore, how to check.

## Steps

1. Snapshot command and restic setup; nightly timer; `restic check --read-data-subset` weekly.
2. Off-site per Q11; document rotation if it's a disk.
3. Monitoring: the timer writes a status file; the review UI shows "last backup" on its home page (small change in
   D13's template, or a CLI `athena library status`).
4. BagIt export with validation (`bagit` Python library, Apache-2.0, or a small writer plus a validator).
5. **Restore test (W7)**: on a clean machine or VM (a Mac laptop or a fresh Ubuntu container), restore the latest
   snapshot, run `athena library verify` (every original's sha256 matches its name, the SQLite integrity check
   passes, row counts match the source), rebuild the public site from the restored library and compare with the
   live build (same item and page lists; images may differ by encoder only).

## Acceptance criteria

- Nightly backups run to the local disk and the off-site target; `restic check` passes.
- **Restore test passes**: every original verified by sha256; integrity check passes; counts match; site rebuilt
  from the restore with the same item and page lists as the source build. Results recorded in `docs/backups.md`
  (counts and timings only).
- The BagIt export validates and opens without Athena@Home (CSV in a spreadsheet, images in any viewer).
- A test (on synthetic data) restores a deliberately corrupted file from backup and `verify` catches the
  corruption first.

## Test plan

pytest for `snapshot`, `verify` and `export bagit` on fixture libraries; a scripted restore of a fixture library
from a local restic repo in CI-able form (restic binary pinned); the real restore test in W7 as the experiment.

## Privacy and rights

- [ ] Every copy encrypted (LUKS/FileVault disks; restic's own encryption); the passphrase stored in a password
      manager, never in the repo or a config file on the server in plain text (`RESTIC_PASSWORD_COMMAND`).
- [ ] If off-site is a cloud bucket: only restic ciphertext goes there; no processing of records in the cloud.
- [ ] The BagIt export for the club contains private material; it's handed over on an encrypted disk with the
      club's agreement on who holds it.

## Touches

`deploy/backup/`, `athena/library/` (`snapshot`, `verify`, `export bagit`, `status`), `tests/test_backup*.py`,
`docs/backups.md`.

## Done means

Three copies, two media, one off-site, all encrypted; a restore onto a clean machine has been done and verified;
and the club can have its archive as a BagIt bag that needs no special software.
