#!/usr/bin/env python3
"""
Offline tests for the Peloton workout ID lookup.

No Airtable token, no Peloton API, no subprocess — the helper invocation is
injected so the join logic and the failure modes can be exercised directly.
Runnable two ways:

    python3 test_workout_id_lookup.py     # standalone (asserts, prints OK)
    pytest test_workout_id_lookup.py      # discovered as test_* functions
"""

import contextlib
import json
import os
import subprocess
import tempfile

import workout_id_lookup as w


def normalize(ts):
    """Stand-in for Peloton_Airtable_Import.normalize_ts."""
    import re
    s = re.sub(r"\s*\([+-]?\w+\)\s*$", "", ts.strip())
    s = s.replace("T", " ")
    return re.sub(r"(\d{2}:\d{2}):\d{2}.*", r"\1", s).strip()


def fake_run(stdout="", returncode=0, stderr="", record=None):
    def _run(cmd, **kwargs):
        if record is not None:
            record.append(cmd)
        return subprocess.CompletedProcess(cmd, returncode, stdout, stderr)
    return _run


@contextlib.contextmanager
def _executable_helper(mode=0o755):
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "peloton-workout-ids.sh")
        with open(p, "w") as f:
            f.write("#!/bin/sh\n")
        os.chmod(p, mode)
        yield p


PAYLOAD = json.dumps({
    "workouts": [
        {"workout_id": "aaa", "workout_timestamp": "2026-08-21 06:30"},
        {"workout_id": "bbb", "workout_timestamp": "2026-08-18 06:45"},
    ],
    "run_metadata": {"total_workouts": 2},
})


def test_parse_maps_merge_key_to_id():
    out = w.parse_helper_output(PAYLOAD)
    assert out == {"2026-08-21 06:30": "aaa", "2026-08-18 06:45": "bbb"}


def test_parse_applies_normalizer_to_csv_timestamp_form():
    # The CSV side carries a timezone suffix; both sides must land on the
    # same key or the join silently produces zero matches.
    payload = json.dumps({"workouts": [
        {"workout_id": "aaa", "workout_timestamp": "2026-08-21 06:30"}]})
    out = w.parse_helper_output(payload, normalize=normalize)
    assert out[normalize("2026-08-21 06:30 (-07)")] == "aaa"


def test_parse_skips_incomplete_records():
    payload = json.dumps({"workouts": [
        {"workout_id": "aaa"},
        {"workout_timestamp": "2026-08-21 06:30"},
        "not-a-dict",
        {"workout_id": "ccc", "workout_timestamp": "2026-08-01 07:00"},
    ]})
    assert w.parse_helper_output(payload) == {"2026-08-01 07:00": "ccc"}


def test_parse_first_wins_on_duplicate_minute():
    # Helper output is newest-first, so the earlier entry is the newer workout.
    payload = json.dumps({"workouts": [
        {"workout_id": "new", "workout_timestamp": "2026-08-21 06:30"},
        {"workout_id": "old", "workout_timestamp": "2026-08-21 06:30"},
    ]})
    assert w.parse_helper_output(payload)["2026-08-21 06:30"] == "new"


def test_parse_rejects_non_json():
    try:
        w.parse_helper_output("Traceback (most recent call last):")
    except w.WorkoutIdLookupError as exc:
        assert "did not emit JSON" in str(exc)
    else:
        raise AssertionError("expected WorkoutIdLookupError")


def test_parse_rejects_json_without_workouts():
    try:
        w.parse_helper_output(json.dumps({"run_metadata": {}}))
    except w.WorkoutIdLookupError as exc:
        assert "no 'workouts' list" in str(exc)
    else:
        raise AssertionError("expected WorkoutIdLookupError")


def test_earliest_date_picks_oldest():
    keys = ["2026-08-21 06:30", "2026-08-01 07:00", "2026-08-18 06:45"]
    assert w.earliest_date(keys) == "2026-08-01"


def test_earliest_date_tolerates_empty_and_short():
    assert w.earliest_date([]) is None
    assert w.earliest_date(["", "bad"]) is None


def test_build_command_pads_since_by_a_day():
    # CSV timestamps are workout-local, --since is UTC; a same-day --since can
    # cut off a late-evening workout that is already "tomorrow" in UTC.
    cmd = w.build_command("/bin/helper", since="2026-08-01")
    assert cmd == ["/bin/helper", "--all", "--format", "json", "--since", "2026-07-31"]


def test_build_command_without_since_scans_all():
    assert w.build_command("/bin/helper") == ["/bin/helper", "--all", "--format", "json"]


def test_build_command_survives_unparseable_since():
    cmd = w.build_command("/bin/helper", since="not-a-date")
    assert cmd[-1] == "not-a-date"


def test_fetch_returns_map_on_success():
    calls = []
    out = w.fetch_workout_id_map(
        "/bin/helper", since="2026-08-01", normalize=normalize,
        runner=fake_run(stdout=PAYLOAD, record=calls),
    )
    assert out["2026-08-21 06:30"] == "aaa"
    assert calls[0][:4] == ["/bin/helper", "--all", "--format", "json"]


def test_fetch_raises_on_nonzero_exit_with_stderr_tail():
    try:
        w.fetch_workout_id_map(
            "/bin/helper",
            runner=fake_run(returncode=1, stderr="line one\nsession expired\n"),
        )
    except w.WorkoutIdLookupError as exc:
        assert "session expired" in str(exc)
    else:
        raise AssertionError("expected WorkoutIdLookupError")


def test_fetch_raises_on_timeout():
    def _run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 300)
    try:
        w.fetch_workout_id_map("/bin/helper", runner=_run)
    except w.WorkoutIdLookupError as exc:
        assert "timed out" in str(exc)
    else:
        raise AssertionError("expected WorkoutIdLookupError")


def test_fetch_raises_when_helper_is_not_executable():
    def _run(cmd, **kwargs):
        raise OSError("Permission denied")
    try:
        w.fetch_workout_id_map("/bin/helper", runner=_run)
    except w.WorkoutIdLookupError as exc:
        assert "could not run helper" in str(exc)
    else:
        raise AssertionError("expected WorkoutIdLookupError")


def test_resolve_helper_explicit_bad_path_does_not_fall_back():
    # An explicit path is authoritative. Falling back to the default here would
    # silently run a different binary than the operator asked for.
    assert w.resolve_helper("/nonexistent/peloton-workout-ids.sh") is None


def test_resolve_helper_finds_explicit_executable():
    with _executable_helper() as p:
        assert w.resolve_helper(p) == p


def test_resolve_helper_ignores_non_executable_file():
    with _executable_helper(mode=0o644) as p:
        assert w.resolve_helper(p) is None


def test_resolve_helper_uses_env_var_when_no_explicit_path():
    import os
    with _executable_helper() as p:
        old_env = os.environ.get("PELOTON_WORKOUT_IDS_CMD")
        os.environ["PELOTON_WORKOUT_IDS_CMD"] = p
        try:
            assert w.resolve_helper() == p
        finally:
            if old_env is None:
                del os.environ["PELOTON_WORKOUT_IDS_CMD"]
            else:
                os.environ["PELOTON_WORKOUT_IDS_CMD"] = old_env


def test_resolve_helper_returns_none_when_nothing_installed():
    import os
    old_env = os.environ.pop("PELOTON_WORKOUT_IDS_CMD", None)
    old_default = w.DEFAULT_HELPER
    w.DEFAULT_HELPER = "/nonexistent/peloton-workout-ids.sh"
    try:
        assert w.resolve_helper() is None
    finally:
        w.DEFAULT_HELPER = old_default
        if old_env is not None:
            os.environ["PELOTON_WORKOUT_IDS_CMD"] = old_env


if __name__ == "__main__":
    import sys
    mod = sys.modules[__name__]
    fns = [getattr(mod, n) for n in dir(mod) if n.startswith("test_")]
    for fn in fns:
        fn()
    print(f"OK — {len(fns)} tests passed")
