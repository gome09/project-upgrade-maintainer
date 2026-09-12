"""Shared Delivery Policy loading and path helpers."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from core.constants import DELIVERY_POLICY_NAME
from core.errors import CliError
from core.paths import require_within
from core.schemas import validate_delivery_policy
from ._governance_records import read_json_object


def delivery_policy_path(upgrade: Path) -> Path:
    return require_within(upgrade / DELIVERY_POLICY_NAME, upgrade, "delivery policy")


def load_delivery_policy(upgrade: Path, *, required: bool = False) -> dict[str, Any] | None:
    path = delivery_policy_path(upgrade)
    if path.is_symlink():
        raise CliError("delivery policy must not be a symlink")
    if not path.exists():
        if required:
            raise CliError("delivery policy is not configured")
        return None
    return validate_delivery_policy(read_json_object(path, upgrade, "delivery policy"))
