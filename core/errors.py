"""Stable exception types used by the deterministic core."""

from __future__ import annotations


class CliError(Exception):
    code = 2


class StalePlanError(CliError):
    code = 3
