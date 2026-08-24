#!/usr/bin/env python3
"""
Offline tests for the Peloton import merge key.

`normalize_ts` decides whether an incoming CSV row is "the same workout" as an
existing Airtable row. Getting it wrong in the lenient direction merges two
workouts; getting it wrong in the strict direction creates a duplicate, which
is what actually happened (505 duplicate pairs, cleaned up Aug 2026).

    python3 test_merge_key.py     # standalone
    pytest test_merge_key.py      # discovered as test_* functions
"""

import Peloton_Airtable_Import as imp

n = imp.normalize_ts


def test_numeric_offset_is_stripped():
    assert n("2026-03-10 13:11 (-07)") == "2026-03-10 13:11"
    assert n("2026-03-10 13:11 (+05)") == "2026-03-10 13:11"


def test_alphabetic_dst_label_is_stripped():
    # The old normalizer only handled numeric offsets, so these kept a
    # mangled "(PD )" suffix in the key.
    assert n("2022-03-24 07:43 (PDT)") == "2022-03-24 07:43"
    assert n("2022-03-24 07:43 (EST)") == "2022-03-24 07:43"
    assert n("2022-03-24 07:43 (MST)") == "2022-03-24 07:43"


def test_dst_flip_produces_one_key():
    # THE regression. Peloton re-renders history with the DST label in force
    # at export time, so the same workout arrives as PDT in summer and PST in
    # winter. Two keys here means the importer creates the workout twice.
    assert n("2022-03-24 07:43 (PDT)") == n("2022-03-24 07:43 (PST)")
    assert n("2022-03-24 07:43 (EDT)") == n("2022-03-24 07:43 (EST)")
    assert n("2022-03-24 07:43 (CDT)") == n("2022-03-24 07:43 (CST)")


def test_iso_form_matches_csv_form():
    # Some historical rows were written in ISO form by another path.
    assert n("2026-03-10T13:11:00") == n("2026-03-10 13:11 (-07)")


def test_seconds_are_dropped():
    assert n("2026-03-10T13:11:47") == "2026-03-10 13:11"


def test_no_stray_parenthesis_survives():
    # The old blanket .replace("T", " ") left "(PD )" behind. Any paren
    # surviving into the key means the label leaked in.
    for label in ("PDT", "PST", "EDT", "EST", "CDT", "MST", "-07", "+05"):
        assert "(" not in n(f"2026-03-10 13:11 ({label})")


def test_empty_and_whitespace_are_passed_through():
    assert n("") == ""
    assert n("   ") == ""


def test_unrecognized_value_is_not_mangled():
    assert n("not a timestamp") == "not a timestamp"


def test_time_of_day_is_still_significant():
    # Guard the lenient direction: distinct workouts must keep distinct keys.
    assert n("2026-03-10 13:11 (PDT)") != n("2026-03-10 13:12 (PDT)")
    assert n("2026-03-10 13:11 (PDT)") != n("2026-03-11 13:11 (PDT)")


if __name__ == "__main__":
    import sys
    mod = sys.modules[__name__]
    fns = [getattr(mod, name) for name in dir(mod) if name.startswith("test_")]
    for fn in fns:
        fn()
    print(f"OK — {len(fns)} tests passed")
