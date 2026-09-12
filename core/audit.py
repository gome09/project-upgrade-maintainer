"""Durable result and maintenance-trace primitives."""

from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .constants import SCHEMA_VERSION
from .paths import relative_posix, require_within, verified_upgrade
from .workspace import atomic_write, read_text_file


@dataclass
class Result:
    command: str
    root: Path
    status: str = "ok"
    changed: list[dict[str, Any]] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    result: dict[str, Any] = field(default_factory=dict)
    plan_id: str | None = None

    def change(self, path: str, action: str, **extra: Any) -> None:
        self.changed.append({"path": path, "action": action, **extra})

    def skip(self, path: str, reason: str) -> None:
        self.skipped.append({"path": path, "reason": reason})

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "command": self.command,
            "status": self.status,
            "root": str(self.root),
            "changed": self.changed,
            "skipped": self.skipped,
            "warnings": self.warnings,
            "errors": self.errors,
            "result": self.result,
            "plan_id": self.plan_id,
        }


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def write_report(
    root: Path,
    result: Result,
    dry_run: bool = False,
    *,
    clock: Callable[[], str] = now_iso,
    writer: Callable[..., bool] = atomic_write,
) -> list[str]:
    upgrade = verified_upgrade(root)
    changed = "\n".join(f"- `{item['path']}`: {item['action']}" for item in result.changed) or "- None."
    skipped = "\n".join(f"- `{item['path']}`: {item['reason']}" for item in result.skipped) or "- None."
    warnings = "\n".join(f"- {item}" for item in result.warnings) or "- None."
    errors = "\n".join(f"- {item}" for item in result.errors) or "- None."
    timestamp = clock()
    text = (
        f"# Upgrade Maintenance Report\n\n## Action Type\n\n{result.command}\n\n"
        f"## Status\n\n{result.status}\n\n## Timestamp\n\n{timestamp}\n\n"
        f"## Changed\n\n{changed}\n\n## Skipped\n\n{skipped}\n\n"
        f"## Warnings\n\n{warnings}\n\n## Errors\n\n{errors}\n"
    )
    safe_stamp = timestamp.replace("+00:00", "Z").replace("-", "").replace(":", "")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]
    trace_name = f"{safe_stamp}-{result.command}-{digest}.md"
    historical = require_within(upgrade / "traces" / trace_name, upgrade, "maintenance trace")
    collision = 0
    while historical.exists():
        collision += 1
        trace_name = f"{safe_stamp}-{result.command}-{digest}-{collision}.md"
        historical = require_within(upgrade / "traces" / trace_name, upgrade, "maintenance trace")
    latest = require_within(upgrade / "traces" / "last-maintenance.md", upgrade, "maintenance report")
    written: list[str] = []
    if writer(historical, text, dry_run=dry_run):
        written.append(relative_posix(historical, root))
    latest_source = read_text_file(latest) if latest.exists() else None
    if writer(latest, text, source=latest_source, dry_run=dry_run):
        written.append(relative_posix(latest, root))
    return written


def finalize_mutation(
    root: Path,
    result: Result,
    collected: list[dict[str, Any]] | None = None,
    *,
    clock: Callable[[], str] = now_iso,
    writer: Callable[..., bool] = atomic_write,
) -> None:
    """Finalize a mutation with a refreshed manifest and durable maintenance trace."""
    from .manifests import refresh_manifest

    had_changes = bool(result.changed)
    try:
        manifest_changed = refresh_manifest(root, result, collected, clock=clock, writer=writer)
        if manifest_changed and not any(item["path"] == ".upgrade/MANIFEST.md" for item in result.changed):
            result.change(".upgrade/MANIFEST.md", "updated")
    except Exception as exc:
        result.status = "partial"
        result.errors.append(f"finalization failed while refreshing MANIFEST.md: {exc}")
        try:
            for path in write_report(root, result, clock=clock, writer=writer):
                if not any(item["path"] == path for item in result.changed):
                    result.change(path, "created" if path != ".upgrade/traces/last-maintenance.md" else "updated")
        except Exception as trace_exc:
            result.errors.append(f"finalization also failed while writing maintenance trace: {trace_exc}")
        return

    if not had_changes and not manifest_changed:
        return

    try:
        trace_paths = write_report(root, result, clock=clock, writer=writer)
    except Exception as exc:
        result.status = "partial"
        result.errors.append(f"finalization failed while writing maintenance trace: {exc}")
        try:
            trace_paths = write_report(root, result, clock=clock, writer=writer)
        except Exception as retry_exc:
            result.errors.append(f"partial maintenance trace could not be written: {retry_exc}")
            return
    for path in trace_paths:
        if not any(item["path"] == path for item in result.changed):
            result.change(path, "created" if path != ".upgrade/traces/last-maintenance.md" else "updated")

