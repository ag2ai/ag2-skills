#!/usr/bin/env python3
"""Validate that every skill declares the AG2 version pinned in COMPATIBILITY.json.

Each skills/<name>/SKILL.md frontmatter must contain:

    metadata:
      ag2_version: "<COMPATIBILITY.json ag2_version>"

The field travels with the skill (copied directory or uploaded zip), while
COMPATIBILITY.json stays in the repo, so the two must be bumped together.

With --tag <tag>, also check that a release tag is exactly "ag2-<ag2_version>".

Exit code 0 = consistent; 1 = mismatch (details printed).

Usage:
    python scripts/validate_versions.py
    python scripts/validate_versions.py --tag ag2-1.3.0
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = REPO_ROOT / "skills"

FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
VERSION_RE = re.compile(r'^metadata:\n(?:  .*\n)*?  ag2_version: "([^"]+)"$', re.MULTILINE)


def skill_version(skill_md: Path) -> str | None:
    match = FRONTMATTER_RE.match(skill_md.read_text(encoding="utf-8"))
    if not match:
        return None
    version = VERSION_RE.search(match.group(1) + "\n")
    return version.group(1) if version else None


def main(argv: list[str]) -> int:
    expected = json.loads((REPO_ROOT / "COMPATIBILITY.json").read_text(encoding="utf-8"))["ag2_version"]
    failures: list[str] = []

    if argv[:1] == ["--tag"]:
        if len(argv) != 2:
            print("usage: validate_versions.py [--tag <tag>]", file=sys.stderr)
            return 2
        if argv[1] != f"ag2-{expected}":
            failures.append(f"tag {argv[1]!r} != 'ag2-{expected}' (COMPATIBILITY.json ag2_version)")
    elif argv:
        print("usage: validate_versions.py [--tag <tag>]", file=sys.stderr)
        return 2

    for skill_dir in sorted(p for p in SKILLS_DIR.iterdir() if p.is_dir()):
        found = skill_version(skill_dir / "SKILL.md")
        if found != expected:
            failures.append(f"{skill_dir.name}: metadata.ag2_version={found!r}, expected {expected!r}")

    for line in failures:
        print(f"FAIL  {line}")
    if failures:
        return 1
    print(f"All skills declare ag2_version {expected}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
