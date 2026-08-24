#!/usr/bin/env python3
"""Look up Peloton workout IDs for the rows the CSV import is about to write.

The Peloton CSV export carries no workout ID column, so the only way to fill
Airtable's `Peloton_Workout_ID` is to ask the Peloton API. That API client
lives in the sibling `peloton-workout-extract` repo, whose wrapper self-locates
via `uv run --project "$SCRIPT_DIR"` and so can neither be imported from here
nor relocated. We shell out to it and join on the shared merge key: the
workout's start time rendered in its own timezone as `YYYY-MM-DD HH:MM`, which
is exactly what the CSV's `Workout Timestamp` column holds.

Best-effort by design. The CSV import is the single supported write path for
workouts (see CLAUDE.md), so a missing, stale, or broken helper must degrade to
"no workout IDs this run" and let the import proceed. A workout row without an
ID is recoverable later; a workout row that never got created is not.
"""

from __future__ import annotations

import json
import os
import subprocess
from typing import Callable, Dict, Iterable, List, Optional

# Where the sibling repo's wrapper normally lives. Override in peloton-sync.conf
# with PELOTON_WORKOUT_IDS_CMD when the checkout is somewhere else.
DEFAULT_HELPER = "~/Dev/peloton-workout-extract/peloton-workout-ids.sh"

# A full-account run pages ~29 times against the Peloton API (~60s). Give it
# room without hanging a daily sync forever.
DEFAULT_TIMEOUT_SECONDS = 300


class WorkoutIdLookupError(RuntimeError):
    """Raised when the helper cannot be run or its output cannot be parsed."""


def _usable(candidate: str) -> Optional[str]:
    path = os.path.expanduser(candidate)
    return path if os.path.isfile(path) and os.access(path, os.X_OK) else None


def resolve_helper(explicit: Optional[str] = None) -> Optional[str]:
    """Return an executable helper path, or None when it isn't installed.

    An explicit path is authoritative: if it was given and is not usable we
    return None rather than quietly substituting the default, because silently
    running a different binary than the one asked for is worse than not running
    one at all. Otherwise: PELOTON_WORKOUT_IDS_CMD, then the sibling-repo
    default. Returns None rather than raising so a machine without the extract
    repo simply skips ID population.
    """
    if explicit:
        return _usable(explicit)
    for candidate in (os.getenv("PELOTON_WORKOUT_IDS_CMD"), DEFAULT_HELPER):
        if candidate:
            found = _usable(candidate)
            if found:
                return found
    return None


def earliest_date(normalized_timestamps: Iterable[str]) -> Optional[str]:
    """Oldest `YYYY-MM-DD` among the merge keys we need IDs for.

    Used to bound the API scan with `--since`. The CSV timestamps are local to
    each workout while `--since` is compared in UTC, so callers pad this by a
    day rather than trusting it to the hour.
    """
    dates = sorted(ts[:10] for ts in normalized_timestamps if ts and len(ts) >= 10)
    return dates[0] if dates else None


def _pad_since(date_str: str) -> str:
    """Shift a YYYY-MM-DD back one day to absorb local-vs-UTC skew."""
    from datetime import date, timedelta

    try:
        y, m, d = (int(part) for part in date_str.split("-"))
        return (date(y, m, d) - timedelta(days=1)).isoformat()
    except (ValueError, TypeError):
        return date_str


def build_command(helper: str, since: Optional[str] = None) -> List[str]:
    """Command line for the helper.

    Always `--all` so paging isn't capped at the helper's default limit of 100;
    `--since` is what actually bounds the scan, and the helper stops only after
    a fully out-of-range page (workout creation order is not start order).
    """
    cmd = [helper, "--all", "--format", "json"]
    if since:
        cmd += ["--since", _pad_since(since)]
    return cmd


def parse_helper_output(
    payload: str,
    normalize: Callable[[str], str] = lambda s: s,
) -> Dict[str, str]:
    """Map normalized merge key to workout ID from the helper's JSON output."""
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise WorkoutIdLookupError(f"helper did not emit JSON: {exc}") from exc

    workouts = data.get("workouts")
    if not isinstance(workouts, list):
        raise WorkoutIdLookupError("helper JSON has no 'workouts' list")

    mapping: Dict[str, str] = {}
    for record in workouts:
        if not isinstance(record, dict):
            continue
        workout_id = record.get("workout_id")
        timestamp = record.get("workout_timestamp")
        if not workout_id or not timestamp:
            continue
        # Newest-first output, so an earlier entry wins a duplicated minute.
        mapping.setdefault(normalize(timestamp), workout_id)
    return mapping


def fetch_workout_id_map(
    helper: str,
    since: Optional[str] = None,
    normalize: Callable[[str], str] = lambda s: s,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    runner: Optional[Callable[..., subprocess.CompletedProcess]] = None,
) -> Dict[str, str]:
    """Run the helper and return {normalized merge key: workout_id}."""
    return _run_helper(
        helper, since, timeout, runner,
        lambda payload: parse_helper_output(payload, normalize=normalize),
    )


def _run_helper(
    helper: str,
    since: Optional[str],
    timeout: int,
    runner: Optional[Callable[..., subprocess.CompletedProcess]],
    parse: Callable[[str], dict],
) -> dict:
    """Invoke the helper once and hand its stdout to `parse`."""
    cmd = build_command(helper, since=since)
    run = runner or subprocess.run
    try:
        proc = run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise WorkoutIdLookupError(f"helper timed out after {timeout}s") from exc
    except OSError as exc:
        raise WorkoutIdLookupError(f"could not run helper: {exc}") from exc

    if proc.returncode != 0:
        detail = (proc.stderr or "").strip().splitlines()
        tail = detail[-1] if detail else f"exit {proc.returncode}"
        raise WorkoutIdLookupError(f"helper failed: {tail}")

    return parse(proc.stdout)


def parse_class_map(payload: str) -> Dict[str, Optional[str]]:
    """Map workout ID to the ID of the class it was taken from.

    The same helper output that carries workout IDs already carries the class
    join, because the export asks the API for `joins=ride`. So the deterministic
    workout-to-class link costs no extra request beyond the one the ID lookup
    already makes.

    A workout with no class (freestyle, Apple Health — Peloton stamps those with
    an all-zero ride id, which the helper already maps to null) is kept with a
    value of None rather than dropped, so callers can tell "took no class" apart
    from "not in the export".
    """
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise WorkoutIdLookupError(f"helper did not emit JSON: {exc}") from exc

    workouts = data.get("workouts")
    if not isinstance(workouts, list):
        raise WorkoutIdLookupError("helper JSON has no 'workouts' list")

    mapping: Dict[str, Optional[str]] = {}
    for record in workouts:
        if not isinstance(record, dict):
            continue
        workout_id = record.get("workout_id")
        if not workout_id:
            continue
        mapping.setdefault(workout_id, record.get("class_id"))
    return mapping


def fetch_class_map(
    helper: str,
    since: Optional[str] = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    runner: Optional[Callable[..., subprocess.CompletedProcess]] = None,
) -> Dict[str, Optional[str]]:
    """Run the helper and return {workout_id: class_id or None}."""
    return _run_helper(helper, since, timeout, runner, parse_class_map)
