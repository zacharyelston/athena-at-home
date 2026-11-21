# SPDX-License-Identifier: GPL-3.0-or-later
"""ULIDs for bundle ids: 26 characters, sortable by creation time, and carrying no personal data."""
from __future__ import annotations

import os
import re
import time

ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # Crockford base32
PATTERN = re.compile(r"^[0-7][0-9A-HJKMNP-TV-Z]{25}$")


def new(now_ms: int | None = None) -> str:
    ms = int(time.time() * 1000) if now_ms is None else now_ms
    value = (ms & (2**48 - 1)) << 80 | int.from_bytes(os.urandom(10), "big")
    return "".join(ALPHABET[(value >> shift) & 31] for shift in range(125, -1, -5))


def is_ulid(text: str) -> bool:
    return isinstance(text, str) and bool(PATTERN.match(text))
