"""Shared immutable governance-record helpers."""
from __future__ import annotations
import hashlib, json
from pathlib import Path
from typing import Any
from core.errors import CliError
from core.paths import require_within, relative_posix
from core.workspace import read_text_file, sha256_file


def governance_record_id(prefix: str, semantic: Any) -> str:
    encoded=json.dumps(semantic,ensure_ascii=False,sort_keys=True,separators=(",",":")).encode()
    return f"{prefix}-{hashlib.sha256(encoded).hexdigest()[:12].upper()}"


def read_json_object(path: Path, boundary: Path, label: str) -> dict[str,Any]:
    require_within(path,boundary,label)
    if path.is_symlink() or not path.is_file():
        raise CliError(f"{label} is not a regular file: {path}")
    try: value=json.loads(read_text_file(path).text)
    except json.JSONDecodeError as exc: raise CliError(f"{label} is invalid JSON: {exc}") from exc
    if not isinstance(value,dict): raise CliError(f"{label} must contain a JSON object")
    return value


def read_jsonl_objects(path: Path, boundary: Path, label: str) -> list[dict[str,Any]]:
    require_within(path,boundary,label)
    if path.is_symlink() or not path.is_file(): raise CliError(f"{label} is not a regular file: {path}")
    out=[]
    for number,line in enumerate(read_text_file(path).text.splitlines(),1):
        if not line.strip(): continue
        try: value=json.loads(line)
        except json.JSONDecodeError as exc: raise CliError(f"{label} has invalid JSON on line {number}: {exc}") from exc
        if not isinstance(value,dict): raise CliError(f"{label} line {number} must be a JSON object")
        out.append(value)
    return out


def record_snapshot(path: Path) -> dict[str,Any]:
    st=path.stat()
    return {"sha256":sha256_file(path),"size":st.st_size,"mtime_ns":st.st_mtime_ns}
