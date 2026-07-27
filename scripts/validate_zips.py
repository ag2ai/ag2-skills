#!/usr/bin/env python3
"""Validate that each skills/<name>.zip matches its skills/<name>/ source dir.

For every skill directory under skills/ there must be a sibling <name>.zip whose
contents are byte-for-byte identical to the source tree, rooted at "<name>/".
Build artifacts that are never packaged (__pycache__/, *.pyc, .DS_Store) are
ignored on both sides so they don't cause false mismatches.

Exit code 0 = all zips match; 1 = at least one mismatch (details printed).

Usage:
    python scripts/validate_zips.py            # validate the whole repo
    python scripts/validate_zips.py ag2-mcp    # validate one or more named skills
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

# Path components / filenames that are intentionally excluded from the zips.
EXCLUDED_DIRS = {"__pycache__"}
EXCLUDED_NAMES = {".DS_Store"}
EXCLUDED_SUFFIXES = {".pyc"}

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = REPO_ROOT / "skills"


def is_excluded(rel: Path) -> bool:
    if any(part in EXCLUDED_DIRS for part in rel.parts):
        return True
    if rel.name in EXCLUDED_NAMES:
        return True
    if rel.suffix in EXCLUDED_SUFFIXES:
        return True
    return False


def source_files(skill_dir: Path) -> dict[str, bytes]:
    """Map of 'in-zip path' -> file bytes for the source directory."""
    out: dict[str, bytes] = {}
    for path in skill_dir.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(skill_dir)
        if is_excluded(rel):
            continue
        arc = f"{skill_dir.name}/{rel.as_posix()}"
        out[arc] = path.read_bytes()
    return out


def zip_files(zip_path: Path) -> dict[str, bytes]:
    """Map of 'in-zip path' -> file bytes for the archive (dir entries skipped)."""
    out: dict[str, bytes] = {}
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            rel = Path(info.filename)
            # Strip the leading "<name>/" root, then apply the same exclusions.
            sub = Path(*rel.parts[1:]) if len(rel.parts) > 1 else rel
            if is_excluded(sub):
                continue
            out[info.filename] = zf.read(info.filename)
    return out


def validate_skill(skill_dir: Path) -> list[str]:
    """Return a list of human-readable problems (empty == matches)."""
    name = skill_dir.name
    zip_path = skill_dir.parent / f"{name}.zip"
    if not zip_path.exists():
        return [f"missing zip: skills/{name}.zip does not exist"]

    src = source_files(skill_dir)
    zed = zip_files(zip_path)
    problems: list[str] = []

    for arc in sorted(set(src) - set(zed)):
        problems.append(f"in source but missing from zip: {arc}")
    for arc in sorted(set(zed) - set(src)):
        problems.append(f"in zip but not in source: {arc}")
    for arc in sorted(set(src) & set(zed)):
        if src[arc] != zed[arc]:
            problems.append(f"content differs: {arc}")

    return problems


def main(argv: list[str]) -> int:
    if not SKILLS_DIR.is_dir():
        print(f"error: {SKILLS_DIR} not found", file=sys.stderr)
        return 1

    if argv:
        skill_dirs = [SKILLS_DIR / n for n in argv]
        for d in skill_dirs:
            if not d.is_dir():
                print(f"error: skills/{d.name}/ not found", file=sys.stderr)
                return 1
    else:
        skill_dirs = sorted(d for d in SKILLS_DIR.iterdir() if d.is_dir())

    # Orphan zips: a <name>.zip with no matching source dir.
    dir_names = {d.name for d in SKILLS_DIR.iterdir() if d.is_dir()}
    orphans = [
        z.name
        for z in sorted(SKILLS_DIR.glob("*.zip"))
        if z.stem not in dir_names
    ]

    failures = 0
    for skill_dir in skill_dirs:
        problems = validate_skill(skill_dir)
        if problems:
            failures += 1
            print(f"FAIL  {skill_dir.name}")
            for p in problems:
                print(f"        - {p}")
        else:
            print(f"ok    {skill_dir.name}")

    if not argv and orphans:
        failures += len(orphans)
        for z in orphans:
            print(f"FAIL  {z} (zip has no matching source directory)")

    print()
    checked = len(skill_dirs)
    if failures:
        print(f"{failures} problem(s) across {checked} skill(s) — zips are OUT OF SYNC.")
        print("Rebuild the affected zip(s) from their source directory and commit.")
        return 1
    print(f"All {checked} skill zip(s) match their source directories.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
