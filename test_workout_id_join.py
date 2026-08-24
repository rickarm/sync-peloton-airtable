#!/usr/bin/env python3
"""
Offline tests for wiring workout IDs into the CSV import.

Exercises Peloton_Airtable_Import.apply_workout_ids with the lookup module
stubbed out — no Peloton API, no Airtable, no subprocess. The point is the
join itself: CSV rows carry a timezone-suffixed timestamp, the API carries a
bare one, and they have to land on the same merge key.

    python3 test_workout_id_join.py     # standalone
    pytest test_workout_id_join.py      # discovered as test_* functions
"""

import Peloton_Airtable_Import as imp
import workout_id_lookup as w

TS = imp.FIELD_IDS["Workout_timestamp"]
WID = imp.FIELD_IDS["Peloton_Workout_ID"]


class _Stub:
    """Swaps the lookup module's surface for the duration of a test."""

    def __init__(self, id_map=None, error=None, helper="/bin/helper"):
        self.id_map, self.error, self.helper = id_map or {}, error, helper
        self.since = None

    def __enter__(self):
        self._saved = (w.resolve_helper, w.fetch_workout_id_map)
        w.resolve_helper = lambda explicit=None: self.helper

        def _fetch(helper, since=None, normalize=None, **kw):
            self.since = since
            if self.error:
                raise self.error
            return dict(self.id_map)

        w.fetch_workout_id_map = _fetch
        return self

    def __exit__(self, *exc):
        w.resolve_helper, w.fetch_workout_id_map = self._saved
        return False


def row(ts):
    return {TS: ts}


def test_join_matches_across_timezone_suffix():
    # The CSV writes "2026-08-21 06:30 (-07)"; the API writes "2026-08-21 06:30".
    rows = [row("2026-08-21 06:30 (-07)")]
    with _Stub({"2026-08-21 06:30": "aaa"}):
        out = imp.apply_workout_ids(rows, helper=None, verbose=False)
    assert rows[0][WID] == "aaa"
    assert out == {"filled": 1, "missing": 0}


def test_join_handles_iso_style_timestamp():
    rows = [row("2026-08-21T06:30:00")]
    with _Stub({"2026-08-21 06:30": "aaa"}):
        imp.apply_workout_ids(rows, helper=None, verbose=False)
    assert rows[0][WID] == "aaa"


def test_unmatched_row_is_counted_and_left_untouched():
    # Freestyle and Apple Health workouts legitimately have no API match.
    rows = [row("2026-08-21 06:30 (-07)"), row("2019-01-01 05:00 (-08)")]
    with _Stub({"2026-08-21 06:30": "aaa"}):
        out = imp.apply_workout_ids(rows, helper=None, verbose=False)
    assert out == {"filled": 1, "missing": 1}
    assert WID not in rows[1]


def test_since_is_bounded_by_the_oldest_row_we_are_writing():
    rows = [row("2026-08-21 06:30 (-07)"), row("2026-08-01 07:00 (-07)")]
    with _Stub({}) as stub:
        imp.apply_workout_ids(rows, helper=None, verbose=False)
    assert stub.since == "2026-08-01"


def test_lookup_failure_does_not_touch_rows():
    # The import is the single write path for workouts; losing an ID is
    # recoverable, losing the workout row is not.
    rows = [row("2026-08-21 06:30 (-07)")]
    with _Stub(error=w.WorkoutIdLookupError("session expired")):
        out = imp.apply_workout_ids(rows, helper=None, verbose=False)
    assert out == {"filled": 0, "missing": 0}
    assert WID not in rows[0]


def test_missing_helper_is_a_no_op():
    rows = [row("2026-08-21 06:30 (-07)")]
    with _Stub(helper=None):
        out = imp.apply_workout_ids(rows, helper=None, verbose=False)
    assert out == {"filled": 0, "missing": 0}
    assert WID not in rows[0]


def test_empty_batch_short_circuits():
    with _Stub({"x": "y"}) as stub:
        assert imp.apply_workout_ids([], helper=None, verbose=False) == {"filled": 0, "missing": 0}
    assert stub.since is None


if __name__ == "__main__":
    import sys
    mod = sys.modules[__name__]
    fns = [getattr(mod, n) for n in dir(mod) if n.startswith("test_")]
    for fn in fns:
        fn()
    print(f"OK — {len(fns)} tests passed")
