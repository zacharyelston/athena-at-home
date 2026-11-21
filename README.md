# Athena@Home

A home capture system: point any camera at paper (or drop in a file, or snap it with a phone), and Athena@Home reads
it, sorts it and files it on home machines (an M1 Mac mini for OCR, a Linux mini for storage and services,
a NAS at release), where it stays.

## The name

Named by a librarian. **Athena** is the Greek goddess of wisdom and patron of learning, which suits a system whose
job is to keep a family's records and find them again. **Athena@Home** is that, for one household. (Not a
volunteer-computing project, despite the SETI@home-style suffix: nothing here leaves the house.)

Where "@" isn't allowed (repo, folder, package and image names) it's spelled **`athena-at-home`**.

Its parts borrow from **Eratosthenes**, chief librarian at Alexandria around 240 BC. He measured the Earth with a
stick and its shadow (Athena@Home measures pages with a camera and an AprilTag), and invented the **sieve** that sorts
primes from the rest (its **Sieve** sorts what it reads).

## Parts

| Part | Job |
|---|---|
| **Capture stations** | Cameras, iPhones and importers that turn paper and files into *capture bundles* (page images, quality stats, and quick OCR where Apple Vision exists). Runs on macOS, iOS and Linux. The desk camera station is inspired by [OCRX](https://github.com/zacharyelston/OCRX). |
| **Sieve** | OCR and classification on Apple Silicon (an M1 Mac mini in the reference household): Apple Vision for an instant pass, DeepSeek-OCR-2 (MLX) for layout-aware text, a local LLM to pull out type, date, institution, people and amounts. Linux stations rely on it for all OCR. |
| **Library** | A records library (HomeLibrarian in a household): write-once originals, full-text search, visibility, a review queue, retention reminders. Any number of named libraries, each wherever it's needed. |
| **Publisher and portal** | Feeds from one library to another or to a website, on the owner's terms: a guest library on the guest wifi, an offline library in a car, a club's public and members sites. Static site or live read-only portal. |

FamilyFinances consumes the same pipeline as a separate service alongside the household library.

The modules are composed per deployment: a household, a car, a club or a library each run as many libraries as
they need, wherever they need them. See [docs/design.md](docs/design.md#core-modules-composed-per-deployment).

**Private by default, published by choice**: OCR and search never use cloud APIs or anyone else's infrastructure,
and nothing leaves the owner's machines unless the owner publishes it through the portal.

## Status

Design stage. See [docs/design.md](docs/design.md) for the architecture, what's reused from earlier work, and
the phased plan. Phase 0 (measuring Apple Vision vs DeepSeek-OCR-2 on home hardware) is done:
[docs/phase0-results.md](docs/phase0-results.md). The backend plan, derived from myvect and the HaberDash OCR
workers, is in [docs/backend.md](docs/backend.md).

## License

Athena@Home is free software: you can redistribute it and/or modify it under the terms of the **GNU General Public
License** as published by the Free Software Foundation, either version 3 of the License, or (at your option) any
later version. See [COPYING](COPYING).

Source files carry the header `SPDX-License-Identifier: GPL-3.0-or-later`. Dependencies must be GPLv3-compatible
(OpenCV, NATS, vLLM and pyobjc are Apache-2.0 or MIT, which are); check model-weight licenses separately from code
licenses.
