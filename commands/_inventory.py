"""Repository inventory, lifecycle classification, and Git inspection helpers."""
from __future__ import annotations
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any
from core.constants import *
from core.errors import CliError
from core.paths import is_within, literal_git_pathspecs, normalize_focuses, normalize_user_path, normalized, relative_posix
from core.workspace import read_text_file
from ._common import walk_files

def git_inventory_paths(root: Path, git: str, *args: str, pathspecs: list[str] | None = None) -> list[Path] | None:
    command = [git, "ls-files", *args, "-z"]
    if pathspecs:
        command.extend(["--", *pathspecs])
    try:
        completed = subprocess.run(command, cwd=root, text=False, capture_output=True, check=False)
    except OSError:
        return None
    if completed.returncode != 0:
        return None
    output: list[Path] = []
    for raw in completed.stdout.split(b"\0"):
        if not raw:
            continue
        relative = raw.decode("utf-8", errors="surrogateescape")
        path = root / relative
        parts = {part.casefold() for part in Path(relative).parts}
        if ".upgrade" in parts or parts & EXCLUDED_DIRS:
            continue
        if path.is_file() and not path.is_symlink() and is_within(path, root):
            output.append(path)
    return output


def ignored_collect_pathspecs() -> list[str]:
    # Collect-review only needs ignored files that could actually become
    # candidates: path-signal matches anywhere, plus shallow text documents
    # eligible for content-signal inspection. Exclude known heavy directories
    # at the Git pathspec layer so they are not enumerated merely to be dropped.
    positive = [f":(icase)*{term}*" for term in COLLECT_SIGNAL_TERMS]
    for extension in COLLECT_CONTENT_EXTENSIONS:
        positive.extend((f":(glob,icase)*{extension}", f":(glob,icase)*/*{extension}"))
    negative = [
        f":(exclude,glob)**/{name}/**"
        for name in sorted(EXCLUDED_DIRS, key=str.casefold)
    ]
    return positive + negative


def project_inventory(root: Path, focuses: list[str] | None = None) -> list[Path]:
    """Return files relevant to collect-review, including targeted ignored candidates."""
    # An explicit focus is already a bounded filesystem scope. Walking it
    # directly is faster than spawning Git for a tiny pathspec and, importantly,
    # still sees an explicitly requested file even when .gitignore hides it.
    if focuses:
        return walk_files(root, EXCLUDED_DIRS, focuses)
    git = shutil.which("git")
    if git and git_repo_root(root) is not None:
        regular = git_inventory_paths(root, git, "-co", "--exclude-standard")
        ignored = git_inventory_paths(
            root, git, "--others", "--ignored", "--exclude-standard",
            pathspecs=ignored_collect_pathspecs(),
        )
        if regular is not None and ignored is not None:
            output = {normalized(path): path for path in (*regular, *ignored)}
            return sorted(output.values(), key=lambda item: relative_posix(item, root).casefold())
    return walk_files(root, EXCLUDED_DIRS)


def git_repo_root(root: Path) -> Path | None:
    git = shutil.which("git")
    if not git:
        return None
    try:
        completed = subprocess.run(
            [git, "rev-parse", "--show-toplevel"], cwd=root, text=True, encoding="utf-8",
            errors="replace", capture_output=True, check=False,
        )
    except OSError:
        return None
    if completed.returncode != 0:
        return None
    value = completed.stdout.strip()
    return Path(value).resolve() if value else None


def git_snapshot(root: Path, focuses: list[str] | None = None) -> dict[str, Any]:
    git = shutil.which("git")
    repo = git_repo_root(root)
    if not git or repo is None:
        return {"available": False, "repo_root": str(repo) if repo else None, "tracked": set(), "ignored": set(), "untracked": set()}

    pathspecs = literal_git_pathspecs(focuses)

    def run(*args: str) -> set[str]:
        command = [git, *args, "-z"]
        if pathspecs:
            command.extend(["--", *pathspecs])
        try:
            completed = subprocess.run(command, cwd=root, text=False, capture_output=True, check=False)
        except OSError:
            return set()
        if completed.returncode != 0:
            return set()
        return {
            normalize_user_path(raw.decode("utf-8", errors="surrogateescape"))
            for raw in completed.stdout.split(b"\0") if raw
        }

    return {
        "available": True,
        "repo_root": str(repo),
        "tracked": run("ls-files"),
        "ignored": run("ls-files", "--others", "-i", "--exclude-standard"),
        "untracked": run("ls-files", "--others", "--exclude-standard"),
    }


def repository_files_for_audit(root: Path, focuses: list[str] | None = None) -> list[Path]:
    return walk_files(root, AUDIT_EXCLUDED_DIRS, focuses)


def sensitive_reason(path: Path) -> str | None:
    if SENSITIVE_NAME_RE.search(path.name):
        return "sensitive filename pattern"
    if path.suffix.casefold() in TEXT_EXTENSIONS:
        try:
            if path.stat().st_size <= 1024 * 1024:
                text = read_text_file(path).text
                if SENSITIVE_CONTENT_RE.search(text):
                    return "credential or secret-like content pattern"
        except (OSError, CliError, UnicodeError):
            pass
    return None


def lifecycle_classification(path: Path, relative: str) -> tuple[str, str]:
    sensitive = sensitive_reason(path)
    if sensitive:
        return "sensitive", sensitive
    parts = {part.casefold() for part in Path(relative).parts[:-1]}
    name = path.name.casefold()
    generated = bool(parts & GENERATED_PARTS) or path.suffix.casefold() in {".pyc", ".coverage"}
    temporary = bool(parts & TEMPORARY_PARTS) or path.suffix.casefold() == ".log" or bool(
        re.search(r"(?:^|[._-])(tmp|temp|scratch|debug)(?:[._-]|$)", name)
    )
    historical = bool(parts & HISTORICAL_PARTS) or bool(
        re.search(r"(?:^|[._-])(old|obsolete|superseded|backup)(?:[._-]|$)", name)
    )
    permanent = bool(parts & PERMANENT_PARTS) or name in PROTECTED_NAMES or path.suffix.casefold() in {
        ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".go", ".rs", ".c", ".cpp", ".h", ".md", ".rst"
    }
    transient_labels = [label for label, matched in (("generated", generated), ("temporary", temporary), ("historical", historical)) if matched]
    if permanent and transient_labels:
        return "unknown", "conflicting lifecycle signals: permanent project material and " + ", ".join(transient_labels)
    if generated:
        return "generated", "generated or reproducible output path"
    if temporary:
        return "temporary", "temporary, debug, or scratch artifact"
    if historical:
        return "historical", "historical or superseded artifact"
    if permanent:
        return "permanent", "source, test, or retained project documentation"
    return "unknown", "no safe lifecycle inference"


def expected_git_state(classification: str) -> str:
    return {
        "permanent": "tracked",
        "generated": "ignored",
        "temporary": "ignored",
        "sensitive": "ignored",
        "historical": "review",
        "unknown": "review",
    }[classification]


def actual_git_state(relative: str, snapshot: dict[str, Any]) -> str:
    if not snapshot["available"]:
        return "unavailable"
    if relative in snapshot["tracked"]:
        return "tracked"
    if relative in snapshot["ignored"]:
        return "ignored"
    if relative in snapshot["untracked"]:
        return "untracked"
    return "unknown"


def escape_gitignore_literal(value: str) -> str | None:
    if any(character in value for character in ("\x00", "\n", "\r")):
        return None
    escaped: list[str] = []
    for character in value:
        if character in {"\\", "*", "?", "[", "]", "#", "!", " "}:
            escaped.append("\\" + character)
        else:
            escaped.append(character)
    return "".join(escaped)


def ignore_suggestion(relative: str, classification: str) -> str | None:
    if classification not in {"generated", "temporary", "sensitive"}:
        return None
    parts = Path(relative).parts
    special = GENERATED_PARTS | TEMPORARY_PARTS
    for index, part in enumerate(parts[:-1]):
        if part.casefold() in special:
            return escape_gitignore_literal("/" + "/".join(parts[:index + 1]) + "/")
    return escape_gitignore_literal("/" + relative)


def audit_entries(root: Path, focuses: list[str] | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    focuses = normalize_focuses(root, focuses)
    snapshot = git_snapshot(root, focuses)
    entries: list[dict[str, Any]] = []
    for path in repository_files_for_audit(root, focuses):
        relative = relative_posix(path, root)
        classification, reason = lifecycle_classification(path, relative)
        expected = expected_git_state(classification)
        actual = actual_git_state(relative, snapshot)
        mismatch = (
            (expected == "tracked" and actual in {"ignored", "untracked"}) or
            (expected == "ignored" and actual in {"tracked", "untracked"})
        )
        stat = path.stat()
        entries.append({
            "path": relative,
            "classification": classification,
            "reason": reason,
            "expected_git_state": expected,
            "actual_git_state": actual,
            "git_mismatch": mismatch,
            "ignore_suggestion": ignore_suggestion(relative, classification),
            "sha256": None,
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
        })
    return entries, snapshot
