#!/usr/bin/env python3
"""Build a minimal, deterministic .skill release archive."""
from __future__ import annotations

import argparse
import hashlib
import zipfile
from pathlib import Path

RUNTIME_FILES = ("SKILL.md", "LICENSE")
RUNTIME_DIRS = (
    "agents",
    "assets",
    "cli",
    "commands",
    "core",
    "references",
    "scripts",
    "subskills",
)
EXCLUDED_PARTS = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
EXCLUDED_SUFFIXES = {".pyc", ".pyo"}
# ZIP timestamps cannot predate 1980. Fixed metadata makes identical source trees
# produce byte-for-byte identical archives across runs.
ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


def iter_release_files(root: Path):
    for name in RUNTIME_FILES:
        path = root / name
        if not path.is_file():
            raise SystemExit(f"missing required release file: {name}")
        yield path
    for dirname in RUNTIME_DIRS:
        base = root / dirname
        if not base.is_dir():
            raise SystemExit(f"missing required runtime directory: {dirname}")
        for path in sorted(base.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(root)
            if any(part in EXCLUDED_PARTS for part in rel.parts):
                continue
            if path.suffix in EXCLUDED_SUFFIXES:
                continue
            yield path


def build(root: Path, output: Path) -> tuple[int, str]:
    root = root.resolve()
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    files = sorted(set(iter_release_files(root)), key=lambda p: p.relative_to(root).as_posix())

    forbidden = [p for p in files if "tests" in p.relative_to(root).parts or ".github" in p.relative_to(root).parts]
    if forbidden:
        raise SystemExit("release file set unexpectedly contains repository-only content")

    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in files:
            rel = path.relative_to(root).as_posix()
            info = zipfile.ZipInfo(rel, ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            zf.writestr(info, path.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)

    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    return len(files), digest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, default=Path("dist/project-upgrade-maintainer.skill"))
    args = parser.parse_args()

    count, digest = build(args.root, args.output)
    print(f"built {args.output} with {count} files")
    print(f"sha256 {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
