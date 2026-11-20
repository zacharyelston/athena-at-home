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
| **Sieve** | OCR and classification on the M1 Mac mini: Apple Vision for an instant pass, DeepSeek-OCR-2 (MLX) for layout-aware text, a local LLM to pull out type, date, institution, people and amounts. Linux stations rely on it for all OCR. |
| **HomeLibrarian** | The family-records library (Ubuntu mini now, NAS at release): write-once originals, full-text search, a review queue, retention reminders. |

FamilyFinances consumes the same pipeline as a separate service alongside HomeLibrarian.

**Family records never leave the house**: no cloud OCR APIs, no work infrastructure.

## Status

Design stage. See [docs/design.md](docs/design.md) for the architecture, what's reused from earlier work, and
the phased plan. Phase 0 (measuring Apple Vision vs DeepSeek-OCR-2 on home hardware) comes first.

## License

Athena@Home is free software: you can redistribute it and/or modify it under the terms of the **GNU General Public
License** as published by the Free Software Foundation, either version 3 of the License, or (at your option) any
later version. See [COPYING](COPYING).

Source files carry the header `SPDX-License-Identifier: GPL-3.0-or-later`. Dependencies must be GPLv3-compatible
(OpenCV, NATS, vLLM and pyobjc are Apache-2.0 or MIT, which are); check model-weight licenses separately from code
licenses.
