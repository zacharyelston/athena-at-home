# SPDX-License-Identifier: GPL-3.0-or-later
"""Separator sheets: a printed page with a QR code that marks "a new item starts here" in a run of captures.

The capture tool (D07) prints them and finds them; this module only fixes the contract both sides share, so a
separator printed by one version of the tool is recognized by another.
"""
from __future__ import annotations

from pathlib import Path
from typing import Protocol

# The QR code's text. A version suffix lets the sheet's meaning change later without misreading old sheets.
SEPARATOR_PAYLOAD = "ATHENA-ITEM-BREAK/1"


def is_separator_payload(text: str | None) -> bool:
    return bool(text) and text.strip() == SEPARATOR_PAYLOAD


class SeparatorDetector(Protocol):
    """Decides whether a captured image is a separator sheet (implemented by the capture tool)."""

    def is_separator(self, image: Path) -> bool: ...
