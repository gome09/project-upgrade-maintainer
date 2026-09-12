#!/usr/bin/env python3
"""Deterministic maintenance CLI for the project-upgrade-maintainer skill."""
from __future__ import annotations

import sys
from pathlib import Path

_SKILL_PACKAGE_ROOT = Path(__file__).resolve().parents[1]
if str(_SKILL_PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(_SKILL_PACKAGE_ROOT))

from cli.main import main


if __name__ == "__main__":
    raise SystemExit(main())
