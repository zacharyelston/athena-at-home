# GNUmon

A home capture system: point any camera at paper (or drop in a file, or snap it with a phone), and GNUmon reads
it, sorts it and files it on home machines (an M1 Mac mini for OCR, a Linux mini for storage and services,
a NAS at release), where it stays.

## The name

Around 240 BC Eratosthenes, chief librarian at Alexandria, measured the circumference of the Earth with a
**gnomon** (a stick and its shadow) and a bit of geometry. He also invented the **sieve** that sorts primes from
the rest. GNUmon measures pages with a camera and an AprilTag instead of a stick and the sun, sieves what it
reads, and keeps a library.

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

GNUmon is free software: you can redistribute it and/or modify it under the terms of the **GNU General Public
License** as published by the Free Software Foundation, either version 3 of the License, or (at your option) any
later version. See [COPYING](COPYING).

Source files carry the header `SPDX-License-Identifier: GPL-3.0-or-later`. Dependencies must be GPLv3-compatible
(OpenCV, NATS, vLLM and pyobjc are Apache-2.0 or MIT, which are); check model-weight licenses separately from code
licenses.

GNUmon is not part of the GNU Project; the name is a pun on *gnomon*.
