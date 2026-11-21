# SPDX-License-Identifier: GPL-3.0-or-later
"""Capture bundles: the one contract every capture source produces and every library consumes.

One bundle is one item (a newsletter issue, a yearbook, a clipping, a photo) with its pages, how they were captured,
its archival description, and a checksum for every file. Spec: docs/capture-bundle.md.
"""
from .edtf import EdtfDate, EdtfError
from .edtf import parse as parse_date
from .manifest import BUNDLE_VERSION, MANIFEST, Bundle, Page, read_bundle, schema
from .separator import SEPARATOR_PAYLOAD, SeparatorDetector, is_separator_payload
from .validate import Report, validate
from .write import BundleError, PageInput, write_bundle

__all__ = [
    "BUNDLE_VERSION", "MANIFEST", "Bundle", "BundleError", "EdtfDate", "EdtfError", "Page", "PageInput", "Report",
    "SEPARATOR_PAYLOAD", "SeparatorDetector", "is_separator_payload", "parse_date", "read_bundle", "schema",
    "validate", "write_bundle",
]
