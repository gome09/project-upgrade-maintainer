"""Shared command-layer parsing and safe filesystem traversal helpers."""
from __future__ import annotations
import os
from pathlib import Path
from core.errors import CliError
from core.paths import is_within, normalize_user_path, normalized, relative_posix, require_within
from core.workspace import read_text_file

def parse_assignments(values: list[str] | None, allowed: set[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values or []:
        if "=" not in value:
            raise CliError(f"decision must use PATH=ACTION: {value}")
        path, action = value.rsplit("=", 1)
        path = normalize_user_path(path)
        if not path or action not in allowed:
            raise CliError(f"invalid decision: {value}")
        result[path] = action
    return result


def walk_files(base: Path, excluded_dirs: set[str], focuses: list[str] | None = None) -> list[Path]:
    """Walk safe regular files, optionally starting only from exact focus paths."""
    excluded_folded = {name.casefold() for name in excluded_dirs}
    starts: list[Path] = []
    if focuses:
        for focus in focuses:
            raw = base / focus
            require_within(raw, base, "focus")
            cursor = base
            blocked = False
            for part in Path(focus).parts:
                cursor = cursor / part
                if part.casefold() in excluded_folded or cursor.is_symlink():
                    blocked = True
                    break
            if blocked or not raw.exists():
                continue
            starts.append(raw)
    else:
        starts.append(base)

    output: dict[str, Path] = {}
    for start in starts:
        if start.is_symlink() or not is_within(start, base):
            continue
        if start.is_file():
            output[normalized(start)] = start
            continue
        if not start.is_dir():
            continue
        for current, dirnames, filenames in os.walk(start, followlinks=False):
            current_path = Path(current)
            kept: list[str] = []
            for name in dirnames:
                child = current_path / name
                if name.casefold() in excluded_folded or child.is_symlink() or not is_within(child, base):
                    continue
                kept.append(name)
            dirnames[:] = kept
            for name in filenames:
                path = current_path / name
                if path.is_symlink() or not is_within(path, base):
                    continue
                output[normalized(path)] = path
    return sorted(output.values(), key=lambda item: relative_posix(item, base).casefold())


def scan_files(base: Path) -> list[Path]:
    """Walk every safe regular file inside the managed workspace scope."""
    return walk_files(base, set())


def summarize_actions(entries: list[dict[str, Any]]) -> dict[str, int]:
    summary: dict[str, int] = {}
    for entry in entries:
        action = str(entry.get("action", "unknown"))
        summary[action] = summary.get(action, 0) + 1
    return dict(sorted(summary.items()))


def parse_replacements(values: list[str] | None, root: Path) -> dict[str, str]:
    replacements: dict[str, str] = {}
    for value in values or []:
        if "=" not in value:
            raise CliError(f"replacement must use PATH=FILE: {value}")
        target, source_value = value.rsplit("=", 1)
        target = normalize_user_path(target)
        source = Path(source_value)
        if not source.is_absolute():
            source = root / source
        source = require_within(source, root, "replacement source")
        if not source.is_file():
            raise CliError(f"replacement source is not a file: {source}")
        replacements[target] = read_text_file(source).text
    return replacements
