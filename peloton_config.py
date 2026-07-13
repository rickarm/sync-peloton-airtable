#!/usr/bin/env python3
"""Shared config loader for the Peloton → Airtable tools.

Reads the same shell-sourceable config the wrapper scripts use, so username,
base ID, and table IDs live in exactly one place:

  1. peloton-sync.conf   (repo root, checked-in defaults)
  2. ~/.peloton-sync.conf (optional per-user override)
  3. environment variables of the same name (highest precedence)

CLI flags on the individual scripts still override everything.

Only simple KEY="value" / KEY=value assignments are understood — no command
substitution, variable expansion, or multi-line values.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Dict

CONFIG_KEYS = (
    "PELOTON_USERNAME",
    "AIRTABLE_BASE_ID",
    "PELOTON_TABLE_ID",
    "PELOTON_RIDES_TABLE_ID",
    "PELOTON_TYPE_TABLE_ID",
    "PELOTON_INSTRUCTOR_TABLE_ID",
)

_ASSIGNMENT_RE = re.compile(
    r"""^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*("(?P<dq>[^"]*)"|'(?P<sq>[^']*)'|(?P<bare>[^#\s]*))"""
)


def _parse_conf(path: Path, into: Dict[str, str]) -> None:
    for line in path.read_text(encoding="utf-8").splitlines():
        m = _ASSIGNMENT_RE.match(line)
        if not m:
            continue
        key = m.group(1)
        value = m.group("dq")
        if value is None:
            value = m.group("sq")
        if value is None:
            value = m.group("bare") or ""
        into[key] = value


def load_config() -> Dict[str, str]:
    """Return the merged config. Missing files are skipped, never an error."""
    cfg: Dict[str, str] = {}
    repo_conf = Path(__file__).resolve().parent / "peloton-sync.conf"
    home_conf = Path.home() / ".peloton-sync.conf"
    for path in (repo_conf, home_conf):
        if path.is_file():
            _parse_conf(path, cfg)
    for key in CONFIG_KEYS:
        env_val = os.environ.get(key)
        if env_val:
            cfg[key] = env_val
    return cfg
