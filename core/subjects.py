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
import stat
import subprocess
from pathlib import Path
from typing import Any

from .constants import PLAN_SCHEMA_VERSION
from .errors import CliError
from .paths import require_within

_SHA256_RE = __import__("re").compile(r"[0-9a-f]{64}")
_MOUNT_POINT_TAG = getattr(stat, "IO_REPARSE_TAG_MOUNT_POINT", 0xA0000003)


def _hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_digest(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return _hash_bytes(encoded)


def _path_order(name: str) -> tuple[str, str]:
    return name.casefold(), name


def _excluded(relative: str) -> bool:
    return relative in {".git", ".upgrade"} or relative.startswith((".git/", ".upgrade/"))


def _link_kind(path: Path) -> str | None:
    try:
        info = path.lstat()
    except (FileNotFoundError, NotADirectoryError):
        return None
    except OSError as exc:
        raise CliError(f"cannot inspect subject path: {path}: {exc}") from exc
    if stat.S_ISLNK(info.st_mode):
        return "symlink"
    if getattr(info, "st_reparse_tag", None) == _MOUNT_POINT_TAG:
        return "junction"
    return None


def _read_regular_file(path: Path) -> dict[str, Any]:
    digest = hashlib.sha256()
    size = 0
    try:
        mode = path.stat().st_mode
        if not stat.S_ISREG(mode):
            raise CliError(f"subject contains a non-regular file: {path}")
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                size += len(chunk)
                digest.update(chunk)
    except OSError as exc:
        raise CliError(f"cannot read subject file: {path}: {exc}") from exc
    return {"kind": "file", "sha256": digest.hexdigest(), "size": size,
            "executable": bool(mode & 0o111) if os.name != "nt" else None}


def _looks_like_saved_plan(path: Path) -> bool:
    """Recognize saved review plans for exclusion from directory and Git subjects.

    Review plan output is an authorization artifact, not product source.  The
    check is deliberately narrow and does not trust or execute the plan.
    """
    try:
        if not path.is_file() or path.is_symlink() or path.stat().st_size > 4 * 1024 * 1024:
            return False
        value = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise CliError(f"cannot read subject file: {path}: {exc}") from exc
    except (UnicodeDecodeError, json.JSONDecodeError):
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


def _directory_link_error(path: Path, reason: str) -> CliError:
    return CliError(
        f"subject directory link is not covered: {path}: {reason}. "
        "Select a common physical directory that covers the source and its real dependencies, "
        "select an unambiguous physical delivery artifact if the subtree is unsupported, "
        "or specify an immutable artifact digest and reference."
    )


def _read_link(
    path: Path, kind: str, root: Path, *,
    tracked: dict[str, tuple[str, str]] | None = None,
    directories: dict[Path, Path] | None = None,
) -> dict[str, Any]:
    try:
        target_text = os.readlink(path)
        target_text.encode("utf-8")
        descriptor: dict[str, Any] = {"kind": kind, "target": target_text}
        try:
            target = path.resolve(strict=True)
            mode = target.stat().st_mode
        except (FileNotFoundError, NotADirectoryError):
            # Strict resolution also detects loops on Python versions where
            # non-strict resolution would silently leave an unresolved link.
            target = path.resolve(strict=False)
            mode = None
        try:
            relative = target.relative_to(root)
        except ValueError as exc:
            raise CliError(f"subject link target resolves outside allowed boundary: {path}") from exc
        if mode is None:
            return descriptor
        control_target = bool(relative.parts) and Path(relative.parts[0]) in {Path(".git"), Path(".upgrade")}
        if stat.S_ISDIR(mode):
            if control_target:
                raise _directory_link_error(path, "its target is a repository control directory")
            if directories is None:
                raise _directory_link_error(path, "Git subjects cannot bind directory link targets")
            directories[path] = target
            return descriptor
        if not stat.S_ISREG(mode):
            raise CliError(f"subject link target is not a regular file or directory: {path}")
        if control_target:
            return descriptor
        # Git snapshots include tracked plan files. Directory snapshots exclude
        # recognized plans regardless of whether another snapshot tracks them.
        if (tracked is None or relative.as_posix() not in tracked) and _looks_like_saved_plan(target):
            return descriptor
        descriptor["target_file"] = _read_regular_file(target)
        return descriptor
    except (OSError, RuntimeError, UnicodeEncodeError) as exc:
        raise CliError(f"cannot fingerprint subject link: {path}: {exc}") from exc


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


def _git_worktree_links(root: Path, paths: list[str]) -> tuple[dict[str, str], set[str]]:
    """Keep each link at its own path even if Git lists paths under it."""
    known: dict[str, str | None] = {}
    links: dict[str, str] = {}
    shadowed: set[str] = set()
    for relative in paths:
        raw = Path(relative)
        if raw.anchor or ".." in raw.parts:
            raise CliError(f"Git subject path resolves outside allowed boundary: {relative}")
        path = root
        for part in raw.parts:
            path /= part
            name = path.relative_to(root).as_posix()
            if name not in known:
                known[name] = _link_kind(path)
            link_kind = known[name]
            if link_kind:
                links[name] = link_kind
                if name != relative:
                    shadowed.add(relative)
                break
    return links, shadowed


def git_subject(root: Path, *, _seen: frozenset[Path] = frozenset()) -> dict[str, Any] | None:
    """Return a deterministic snapshot for the current Git worktree.

    The root must itself be the repository root. This avoids ambiguous subject
    boundaries when the CLI is pointed at a nested directory.
    """
    repo = _git_root(root)
    if repo is None or repo != root.resolve():
        return None
    if repo in _seen:
        raise CliError("cyclic submodule subject boundary")
    seen = _seen | {repo}
    head_result = _git(root, "rev-parse", "HEAD")
    head = head_result.stdout.decode("utf-8", errors="replace").strip() if head_result.returncode == 0 else "unborn"

    tracked = _git_paths(root, "ls-files", "-z", "--cached")
    indexed: dict[str, tuple[str, str]] = {}
    for entry in _git_paths(root, "ls-files", "--stage", "-z"):
        metadata, relative = entry.split("\t", 1)
        mode, oid, stage = metadata.split()
        if stage != "0":
            raise CliError(f"unmerged Git subject path: {relative}")
        indexed[relative] = (mode, oid)
    untracked = set(_git_paths(root, "ls-files", "-z", "--others", "--exclude-standard"))
    paths = sorted(
        (relative for relative in set(tracked) | untracked if not _excluded(relative)),
        key=_path_order,
    )
    links, shadowed = _git_worktree_links(repo, paths)
    entries: list[dict[str, Any]] = []
    for relative in sorted(set(paths) | links.keys(), key=_path_order):
        path = repo / relative
        link_kind = links.get(relative)
        # Git reports slash-separated paths. Keep literal backslashes in link
        # identities and retain the historical spelling for ordinary entries.
        recorded_path = relative if link_kind or relative in shadowed else relative.replace("\\", "/")
        if _excluded(recorded_path):
            continue
        index_entry = indexed.get(relative)
        if index_entry and index_entry[0] == "160000":
            nested = None
            if link_kind is None and relative not in shadowed and path.is_dir() and (path / ".git").exists():
                nested = git_subject(path, _seen=seen)
                if nested is None:
                    raise CliError(f"cannot fingerprint initialized submodule: {relative}")
            descriptor = {"path": recorded_path, "kind": "submodule", "gitlink": index_entry[1],
                          "worktree": nested}
            if link_kind:
                descriptor["link"] = _read_link(path, link_kind, repo, tracked=indexed)
            entries.append(descriptor)
            continue
        if link_kind:
            descriptor = _read_link(path, link_kind, repo, tracked=indexed)
        elif relative in shadowed:
            if index_entry:
                entries.append({"path": recorded_path, "kind": "missing"})
            continue
        elif relative in untracked and _looks_like_saved_plan(path):
            continue
        elif not path.exists():
            entries.append({"path": recorded_path, "kind": "missing"})
            continue
        else:
            descriptor = _read_regular_file(path)
        entries.append({"path": recorded_path, "index_mode": index_entry[0] if index_entry else None, **descriptor})

    payload = {"head": head, "entries": entries}
    return {
        "type": "git-worktree",
        "ref": head,
        "digest": _canonical_digest(payload),
        "file_count": len(entries),
    }


def path_subject(root: Path, value: str) -> dict[str, Any]:
    root = root.resolve()
    raw = Path(value)
    path = raw if raw.is_absolute() else root / raw
    link_kind = _link_kind(path)
    if link_kind:
        raise CliError(f"subject path ends at a {link_kind}: {path}. Use the target's repository path.")
    try:
        path = require_within(path, root, "subject path")
    except (OSError, RuntimeError) as exc:
        raise CliError(f"cannot resolve subject path: {path}: {exc}") from exc
    relative = path.relative_to(root).as_posix()
    try:
        mode = path.stat().st_mode
    except (FileNotFoundError, NotADirectoryError) as exc:
        raise CliError(f"subject path does not exist: {relative}") from exc
    except OSError as exc:
        raise CliError(f"cannot inspect subject path: {path}: {exc}") from exc
    if stat.S_ISREG(mode):
        descriptor = _read_regular_file(path)
        payload = {"root": relative, **descriptor}
        return {"type": "path", "ref": relative, "digest": _canonical_digest(payload), "file_count": 1}
    if not stat.S_ISDIR(mode):
        raise CliError("subject path must be a regular file or directory")

    entries: list[dict[str, Any]] = []
    visited: dict[Path, str] = {}
    unbound_ancestors: set[Path] = set()
    directories: dict[Path, Path] = {}

    def scan_error(exc: OSError) -> None:
        raise CliError(f"cannot scan subject directory: {exc.filename or path}: {exc}") from exc

    for current, dirs, files in os.walk(path, topdown=True, onerror=scan_error, followlinks=False):
        current_path = Path(current)
        if _link_kind(current_path):
            raise CliError(f"subject directory changed to a link during scan: {current_path}")
        visited[current_path] = current_path.relative_to(path).as_posix()
        safe_dirs: list[str] = []
        for name in sorted(dirs, key=_path_order):
            child = current_path / name
            if _excluded(child.relative_to(root).as_posix()):
                unbound_ancestors.update(child.parents)
                continue
            link_kind = _link_kind(child)
            if link_kind:
                unbound_ancestors.update(child.parents)
                descriptor = _read_link(child, link_kind, root, directories=directories)
                entries.append({"path": child.relative_to(path).as_posix(), **descriptor})
            else:
                safe_dirs.append(name)
        dirs[:] = safe_dirs
        for name in sorted(files, key=_path_order):
            child = current_path / name
            if _excluded(child.relative_to(root).as_posix()):
                unbound_ancestors.update(child.parents)
                continue
            link_kind = _link_kind(child)
            if link_kind:
                unbound_ancestors.update(child.parents)
                descriptor = _read_link(child, link_kind, root, directories=directories)
            elif _looks_like_saved_plan(child):
                unbound_ancestors.update(child.parents)
                continue
            else:
                descriptor = _read_regular_file(child)
            entries.append({"path": child.relative_to(path).as_posix(), **descriptor})
    payload = {"root": relative, "entries": entries}
    if directories:
        targets = set(directories.values())
        for entry in entries:
            link = path / entry["path"]
            if link not in directories:
                continue
            target = directories[link]
            if target not in visited:
                raise _directory_link_error(link, "its target was not visited in the selected physical tree")
            if target in unbound_ancestors:
                raise _directory_link_error(link, "its target subtree contains a link or excluded content")
            entry["target_directory"] = visited[target]
        payload["linked_directories"] = sorted(
            (name for directory, name in visited.items()
             if directory in targets or not targets.isdisjoint(directory.parents)),
            key=_path_order,
        )
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
