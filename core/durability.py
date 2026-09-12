"""Filesystem durability primitives shared by transactions, locks, and workspace writes.

The contract is intentionally narrow: make file contents durable, make directory-entry
changes durable on platforms/filesystems that support directory fsync, and keep callers
from re-implementing subtly different crash-safety sequences.
"""
from __future__ import annotations

import errno
import os
import shutil
import tempfile
from pathlib import Path

_UNSUPPORTED_DIR_FSYNC = {
    errno.EINVAL,
    errno.EBADF,
    getattr(errno, "ENOTSUP", errno.EINVAL),
    getattr(errno, "EOPNOTSUPP", errno.EINVAL),
}


def fsync_directory(path: Path) -> bool:
    """fsync a directory when supported; return False only for unsupported platforms/filesystems."""
    if os.name == "nt":
        return False
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        if exc.errno in _UNSUPPORTED_DIR_FSYNC:
            return False
        raise
    try:
        try:
            os.fsync(fd)
            return True
        except OSError as exc:
            if exc.errno in _UNSUPPORTED_DIR_FSYNC:
                return False
            raise
    finally:
        os.close(fd)


def durable_mkdir(path: Path) -> None:
    """Create a directory tree and durably publish each newly created directory entry."""
    path = Path(path)
    if path.exists():
        return
    missing: list[Path] = []
    cursor = path
    while not cursor.exists():
        missing.append(cursor)
        parent = cursor.parent
        if parent == cursor:
            break
        cursor = parent
    for directory in reversed(missing):
        try:
            directory.mkdir()
        except FileExistsError:
            continue
        fsync_directory(directory)
        fsync_directory(directory.parent)


def _cleanup_temp(path: str | Path) -> None:
    raw = Path(path)
    try:
        raw.unlink()
    except FileNotFoundError:
        return
    fsync_directory(raw.parent)


def atomic_write_bytes(path: Path, data: bytes, *, mode: int | None = None) -> None:
    """Atomically replace *path* after fsyncing contents and then its parent directory."""
    path = Path(path)
    durable_mkdir(path.parent)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            if mode is not None:
                os.fchmod(stream.fileno(), mode)
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
        fsync_directory(path.parent)
    finally:
        if os.path.exists(temp_name):
            _cleanup_temp(temp_name)


def durable_copy_file(source: Path, target: Path) -> None:
    """Copy a regular file through a durable temporary file and atomically publish it."""
    source, target = Path(source), Path(target)
    durable_mkdir(target.parent)
    fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    os.close(fd)
    try:
        shutil.copy2(source, temp_name)
        with open(temp_name, "r+b") as stream:
            os.fsync(stream.fileno())
        os.replace(temp_name, target)
        fsync_directory(target.parent)
    finally:
        if os.path.exists(temp_name):
            _cleanup_temp(temp_name)


def durable_unlink(path: Path) -> None:
    """Unlink a file and durably publish the directory-entry removal."""
    path = Path(path)
    path.unlink()
    fsync_directory(path.parent)


def durable_move_file(source: Path, target: Path) -> None:
    """Durably move a file; fall back to durable copy+unlink across filesystems."""
    source, target = Path(source), Path(target)
    durable_mkdir(target.parent)
    source_parent = source.parent
    try:
        os.replace(source, target)
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise
        durable_copy_file(source, target)
        durable_unlink(source)
        return
    fsync_directory(target.parent)
    if source_parent != target.parent:
        fsync_directory(source_parent)


def durable_rmtree(path: Path) -> None:
    """Remove a private directory tree and durably publish removal of its root entry."""
    path = Path(path)
    if not path.exists():
        return
    parent = path.parent
    shutil.rmtree(path)
    fsync_directory(parent)
