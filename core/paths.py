"""Repository-boundary and path-normalization primitives."""

from __future__ import annotations

import os
from pathlib import Path

from .errors import CliError


def normalized(path: Path) -> str:
    return os.path.normcase(str(path.resolve(strict=False)))


def is_within(path: Path, base: Path) -> bool:
    try:
        return os.path.commonpath([normalized(path), normalized(base)]) == normalized(base)
    except ValueError:
        return False


def require_within(path: Path, base: Path, label: str = "path") -> Path:
    resolved = path.resolve(strict=False)
    if not is_within(resolved, base):
        raise CliError(f"{label} resolves outside allowed boundary: {path}")
    return resolved


def relative_posix(path: Path, root: Path) -> str:
    return path.resolve(strict=False).relative_to(root.resolve()).as_posix()


def normalize_user_path(value: str) -> str:
    normalized_value = Path(value).as_posix()
    while normalized_value.startswith("./"):
        normalized_value = normalized_value[2:]
    return normalized_value.rstrip("/")


def normalize_focuses(root: Path, values: list[str] | None) -> list[str]:
    """Normalize, deduplicate, and boundary-check focus paths.

    A focus of ``.`` means the repository root, so it intentionally collapses
    to an empty list (the internal representation for an unscoped scan).
    """
    focuses: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        raw = Path(value)
        target = raw if raw.is_absolute() else root / raw
        require_within(target, root, "focus")
        try:
            lexical = os.path.relpath(os.path.abspath(target), root)
        except ValueError as exc:
            raise CliError(f"focus resolves outside allowed boundary: {value}") from exc
        focus = normalize_user_path(Path(lexical).as_posix())
        if focus in {"", "."}:
            return []
        key = os.path.normcase(focus)
        if key not in seen:
            focuses.append(focus)
            seen.add(key)
    return focuses


def literal_git_pathspecs(focuses: list[str] | None) -> list[str]:
    return [f":(literal){focus}" for focus in focuses or []]


def root_from(value: str) -> Path:
    root = Path(value).resolve()
    if not root.is_dir():
        raise CliError(f"repository root is not a directory: {root}")
    return root


def verified_upgrade(root: Path, *, must_exist: bool = True) -> Path:
    raw_upgrade = root / ".upgrade"
    if raw_upgrade.is_symlink():
        raise CliError(".upgrade must be a real local directory, not a symlink")
    upgrade = require_within(raw_upgrade, root, ".upgrade")
    if upgrade.exists() and not upgrade.is_dir():
        raise CliError(".upgrade exists but is not a directory")
    if must_exist and not upgrade.is_dir():
        raise CliError(".upgrade does not exist; run initialize first")
    return upgrade
