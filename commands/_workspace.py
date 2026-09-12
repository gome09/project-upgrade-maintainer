"""Command-domain workspace routing helpers.

Deterministic configuration, registry, materialization, and mutation preflight
live in ``core``.  This module intentionally retains only domain-selection
semantics used by Bootstrap/Lifecycle commands.
"""

from __future__ import annotations

from core.constants import PROFILE_MODULES
from core.errors import CliError
from core.paths import normalize_user_path
from core.schemas import ordered_modules


def modules_for_profile(profile: str | None, custom_modules: list[str] | None = None) -> list[str]:
    normalized = (profile or "lean").casefold()
    if normalized == "default":
        # The supported `default` profile name maps to the governed profile.
        normalized = "governed"
    if normalized == "custom":
        return ordered_modules(set(custom_modules or []) | {"core"})
    if custom_modules:
        raise CliError("--module is valid only with --profile custom")
    if normalized not in PROFILE_MODULES:
        raise CliError(f"unknown initialization profile: {profile}")
    return list(PROFILE_MODULES[normalized])


def module_for_upgrade_destination(relative: str) -> str:
    normalized_relative = normalize_user_path(relative)
    if normalized_relative.startswith(".upgrade/docs/"):
        return "planning"
    if normalized_relative.startswith(".upgrade/stages/") or normalized_relative.startswith(".upgrade/checkpoints/"):
        return "phase"
    if normalized_relative.startswith(".upgrade/validation/"):
        return "validation"
    if normalized_relative.startswith(".upgrade/reports/"):
        return "reporting"
    if any(
        normalized_relative.startswith(prefix)
        for prefix in (
            ".upgrade/reviews/",
            ".upgrade/decisions/",
            ".upgrade/risks/",
            ".upgrade/exceptions/",
            ".upgrade/delivery/",
        )
    ):
        return "review"
    if any(
        normalized_relative.startswith(prefix)
        for prefix in (".upgrade/tmp/", ".upgrade/logs/", ".upgrade/archive/", ".upgrade/prompts/")
    ):
        return "lifecycle"
    return "core"


def normalized_profile_name(profile: str | None) -> str:
    if profile is None:
        return "lean"
    value = profile.casefold()
    return "governed" if value == "default" else value
