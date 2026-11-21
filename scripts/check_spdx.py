#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Every source file carries the GPL-3.0-or-later SPDX header, and every "Derived from" line names its source.

    scripts/check_spdx.py              check all tracked and new (not ignored) files
    scripts/check_spdx.py FILE...      check these files (for a pre-commit hook)

Checked: *.py, *.sh, and GitHub workflow files. Exit 1 with one line per problem.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

SPDX = "SPDX-License-Identifier: GPL-3.0-or-later"
HEADER_LINES = 5           # the SPDX line must be within the first lines (after a shebang)
PROVENANCE_LINES = 12      # where a "Derived from" line is looked for
DERIVED = re.compile(r"Derived from\b(.*)")


def is_checked(path: str) -> bool:
    p = Path(path)
    if p.suffix in (".py", ".sh"):
        return True
    return p.suffix in (".yml", ".yaml") and p.parts[:2] == (".github", "workflows")


def problems(path: Path) -> list[str]:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as e:
        return [f"can't read: {e}"]
    out = []
    if not any(SPDX in line for line in lines[:HEADER_LINES]):
        out.append(f"missing '{SPDX}' in the first {HEADER_LINES} lines")
    for line in lines[:PROVENANCE_LINES]:
        m = DERIVED.search(line)
        if m and not re.search(r"\w", m.group(1)):
            out.append(f"'Derived from' names no source: {line.strip()!r}")
    return out


def repo_files(root: Path) -> list[str]:
    res = subprocess.run(["git", "ls-files", "-co", "--exclude-standard"], cwd=root, capture_output=True,
                         text=True, check=True)
    return [f for f in res.stdout.splitlines() if f]


def check(root: Path, paths: list[str]) -> list[str]:
    report = []
    for rel in paths:
        if is_checked(rel) and (root / rel).is_file():
            report += [f"{rel}: {p}" for p in problems(root / rel)]
    return report


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    root = Path.cwd()
    report = check(root, argv or repo_files(root))
    for line in report:
        print(line)
    if report:
        print(f"\n{len(report)} problem(s). Add '# {SPDX}' at the top of each file "
              "(and say what a derived file came from).", file=sys.stderr)
    return 1 if report else 0


if __name__ == "__main__":
    sys.exit(main())
