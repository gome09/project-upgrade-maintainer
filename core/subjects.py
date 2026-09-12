"""Deterministic delivery/validation subject identity helpers.

A subject identity binds validation and delivery approval to the exact code or
artifact that was reviewed.  Explicit paths are hashed directly; Git worktrees
are hashed from HEAD plus tracked and non-ignored untracked file content while
excluding the governed .upgrade workspace and recognized saved review plans.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .constants import PLAN_SCHEMA_VERSION
from .errors import CliError
from .paths import relative_posix, require_within

_SHA256_RE = __import__("re").compile(r"[0-9a-f]{64}")


def _hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_digest(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return _hash_bytes(encoded)


def _read_regular_or_symlink(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        return {"kind": "symlink", "target": os.readlink(path)}
    if not path.is_file():
        raise CliError(f"subject contains a non-regular file: {path}")
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            size += len(chunk)
            digest.update(chunk)
    return {"kind": "file", "sha256": digest.hexdigest(), "size": size}


def _looks_like_saved_plan(path: Path) -> bool:
    """Exclude untracked saved Plan Schema files from a Git subject snapshot.

    Review plan output is an authorization artifact, not product source.  The
    check is deliberately narrow and does not trust or execute the plan.
    """
    if not path.is_file() or path.is_symlink() or path.stat().st_size > 4 * 1024 * 1024:
        return False
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    if not (
        isinstance(value, dict)
        and value.get("schema_version") == PLAN_SCHEMA_VERSION
        and isinstance(value.get("plan_id"), str)
        and bool(_SHA256_RE.fullmatch(value["plan_id"]))
        and isinstance(value.get("kind"), str)
        and isinstance(value.get("entries"), list)
    ):
        return False
    recorded = value["plan_id"]
    canonical = dict(value)
    canonical.pop("plan_id", None)
    return recorded == _canonical_digest(canonical)


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    git = shutil.which("git")
    if not git:
        raise CliError("git is unavailable")
    return subprocess.run([git, *args], cwd=root, capture_output=True, check=False)


def _git_root(root: Path) -> Path | None:
    try:
        completed = _git(root, "rev-parse", "--show-toplevel")
    except CliError:
        return None
    if completed.returncode != 0:
        return None
    try:
        return Path(completed.stdout.decode("utf-8", errors="strict").strip()).resolve()
    except (UnicodeDecodeError, OSError):
        return None


def _git_paths(root: Path, *args: str) -> list[str]:
    completed = _git(root, *args)
    if completed.returncode != 0:
        stderr = completed.stderr.decode("utf-8", errors="replace").strip()
        raise CliError(f"cannot compute Git subject identity: {stderr or 'git command failed'}")
    raw = completed.stdout.split(b"\0")
    out: list[str] = []
    for item in raw:
        if not item:
            continue
        try:
            value = item.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise CliError("Git subject path is not valid UTF-8") from exc
        out.append(value)
    return out


def git_subject(root: Path) -> dict[str, Any] | None:
    """Return a deterministic snapshot for the current Git worktree.

    The root must itself be the repository root. This avoids ambiguous subject
    boundaries when the CLI is pointed at a nested directory.
    """
    repo = _git_root(root)
    if repo is None or repo != root.resolve():
        return None
    head_result = _git(root, "rev-parse", "HEAD")
    head = head_result.stdout.decode("utf-8", errors="replace").strip() if head_result.returncode == 0 else "unborn"

    tracked = _git_paths(root, "ls-files", "-z", "--cached")
    untracked = _git_paths(root, "ls-files", "-z", "--others", "--exclude-standard")
    entries: list[dict[str, Any]] = []
    for relative in sorted(set(tracked + untracked), key=str.casefold):
        normalized = relative.replace("\\", "/")
        if normalized == ".upgrade" or normalized.startswith(".upgrade/") or normalized == ".git" or normalized.startswith(".git/"):
            continue
        path = require_within(root / relative, root, "Git subject path")
        if relative in untracked and _looks_like_saved_plan(path):
            continue
        if not path.exists() and not path.is_symlink():
            entries.append({"path": normalized, "kind": "missing"})
            continue
        descriptor = _read_regular_or_symlink(path)
        entries.append({"path": normalized, **descriptor})

    payload = {"head": head, "entries": entries}
    return {
        "type": "git-worktree",
        "ref": head,
        "digest": _canonical_digest(payload),
        "file_count": len(entries),
    }


def path_subject(root: Path, value: str) -> dict[str, Any]:
    raw = Path(value)
    path = raw if raw.is_absolute() else root / raw
    path = require_within(path, root, "subject path")
    if not path.exists() and not path.is_symlink():
        raise CliError(f"subject path does not exist: {relative_posix(path, root)}")
    relative = relative_posix(path, root)
    if path.is_symlink():
        raise CliError("subject path itself must not be a symlink")
    if path.is_file():
        descriptor = _read_regular_or_symlink(path)
        payload = {"root": relative, **descriptor}
        return {"type": "path", "ref": relative, "digest": _canonical_digest(payload), "file_count": 1}
    if not path.is_dir():
        raise CliError("subject path must be a regular file or directory")

    entries: list[dict[str, Any]] = []
    for current, dirs, files in os.walk(path, topdown=True, followlinks=False):
        current_path = Path(current)
        safe_dirs: list[str] = []
        for name in sorted(dirs, key=str.casefold):
            child = current_path / name
            rel_root = relative_posix(child, root)
            if rel_root == ".upgrade" or rel_root.startswith(".upgrade/") or rel_root == ".git" or rel_root.startswith(".git/"):
                continue
            if child.is_symlink():
                entries.append({"path": relative_posix(child, path), **_read_regular_or_symlink(child)})
            else:
                safe_dirs.append(name)
        dirs[:] = safe_dirs
        for name in sorted(files, key=str.casefold):
            child = require_within(current_path / name, root, "subject file")
            rel_root = relative_posix(child, root)
            if rel_root == ".upgrade" or rel_root.startswith(".upgrade/") or rel_root == ".git" or rel_root.startswith(".git/"):
                continue
            if _looks_like_saved_plan(child):
                continue
            entries.append({"path": relative_posix(child, path), **_read_regular_or_symlink(child)})
    payload = {"root": relative, "entries": entries}
    return {"type": "path", "ref": relative, "digest": _canonical_digest(payload), "file_count": len(entries)}


def external_subject(digest: str, ref: str) -> dict[str, Any]:
    value = (digest or "").strip().lower()
    if not _SHA256_RE.fullmatch(value):
        raise CliError("subject digest must be a sha256 hex digest")
    reference = (ref or "").strip()
    if not reference:
        raise CliError("external subject digest requires a non-empty subject reference")
    return {"type": "external-digest", "ref": reference, "digest": value, "file_count": None}


def resolve_subject(
    root: Path,
    *,
    subject_path: str | None = None,
    subject_digest: str | None = None,
    subject_ref: str | None = None,
    fallback_ref: str = "",
) -> dict[str, Any]:
    if subject_path and subject_digest:
        raise CliError("use either --subject-path or --subject-digest, not both")
    if subject_path:
        return path_subject(root, subject_path)
    if subject_digest:
        return external_subject(subject_digest, subject_ref or fallback_ref)
    if subject_ref:
        raise CliError("--subject-ref requires --subject-digest")
    git = git_subject(root)
    if git is not None:
        return git
    return {"type": "unbound", "ref": fallback_ref or "workspace", "digest": None, "file_count": None}


def refresh_subject(root: Path, subject: dict[str, Any]) -> dict[str, Any]:
    kind = subject.get("type")
    if kind == "path":
        return path_subject(root, str(subject.get("ref") or ""))
    if kind == "git-worktree":
        current = git_subject(root)
        if current is None:
            raise CliError("reviewed Git delivery subject is no longer available from the repository root")
        return current
    if kind == "external-digest":
        return external_subject(str(subject.get("digest") or ""), str(subject.get("ref") or ""))
    if kind == "unbound":
        return {"type": "unbound", "ref": str(subject.get("ref") or "workspace"), "digest": None, "file_count": None}
    raise CliError("delivery subject descriptor is invalid")
