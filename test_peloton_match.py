#!/usr/bin/env python3
"""
Offline tests for Peloton_Match scoring logic.

These need no Airtable token and no network — they exercise the pure scoring
functions that decide how a workout is matched to a class. Runnable two ways:

    python3 test_peloton_match.py     # standalone (asserts, prints OK)
    pytest test_peloton_match.py      # discovered as test_* functions

Importing Peloton_Match here does NOT require `requests` (it's imported lazily
inside the network paths), so this runs anywhere Python 3 is available.
"""

from datetime import datetime

import Peloton_Match as m


def test_parse_date_safe():
    # ClassTimestampString style, with a timezone suffix to strip
    assert m.parse_date_safe("2026-04-17 07:00 (-07)") == datetime(2026, 4, 17, 7, 0)
    assert m.parse_date_safe("2022-03-24 07:43 (PDT)") == datetime(2022, 3, 24, 7, 43)
    # Formula date fields come back date-only via REST
    assert m.parse_date_safe("2025-07-12") == datetime(2025, 7, 12, 0, 0)
    # ISO with Z
    assert m.parse_date_safe("2026-04-17T07:00:00.000Z") == datetime(2026, 4, 17, 7, 0)
    # Junk / empty
    assert m.parse_date_safe(None) is None
    assert m.parse_date_safe("") is None


def test_text_helpers():
    assert m.token_similarity("45 min Power Zone Ride", "45 min Power Zone Ride") == 1.0
    assert m.token_similarity("", "anything") == 0.0
    assert m.contains_power_zone_hint("45 min Power Zone Endurance Ride") == "endurance"
    assert m.contains_power_zone_hint("Power Zone Max Ride") == "max"
    assert m.contains_power_zone_hint("30 min Power Zone Ride") == "power zone"
    assert m.contains_power_zone_hint("Climb Ride") == ""


def _workout():
    return {
        "Title": "45 min Power Zone Endurance Ride",
        "Length": 45,
        "InstructorName": ["recMattW"],
        "FitnessDiscipline": "cycling",
        "Type": ["rectypePZE"],
    }


TYPE_MAP = {"rectypePZE": "Power Zone Endurance"}
WDATE = datetime(2026, 4, 17, 7, 0)


def _ride(**overrides):
    fields = {
        "ClassTimeDate": "2026-04-17T07:00:00.000Z",
        "RideTitle": "45 min Power Zone Endurance Ride",
        "RideDuration_min": 45,
        "Instructor": ["recMattW"],
        "PowerZoneType": "Power Zone Endurance",
        "ClassID": "abc123",
    }
    fields.update(overrides)
    return m.Ride({"id": overrides.get("_id", "recRide1"), "fields": fields})


def test_perfect_match_score():
    # instructor 40 + duration 25 + title 30 + time<=1h 15 + pz exact 10 = 120
    res = m.score_match(_workout(), WDATE, TYPE_MAP, _ride())
    assert res["score"] == 120, res


def test_weak_match_below_threshold():
    ride = _ride(
        _id="recRide2",
        ClassTimeDate="2026-04-17T09:30:00.000Z",  # 2.5h away -> +10
        RideTitle="30 min Climb Ride",
        RideDuration_min=30,
        Instructor=["recOther"],
        PowerZoneType="",
    )
    res = m.score_match(_workout(), WDATE, TYPE_MAP, ride)
    assert res["score"] < m.AUTO_MATCH_THRESHOLD, res


def test_outside_time_window_rejected():
    ride = _ride(_id="recRide3", ClassTimeDate="2026-04-19T07:00:00.000Z")  # >24h
    res = m.score_match(_workout(), WDATE, TYPE_MAP, ride)
    assert res["score"] == -999, res


def test_instructor_matched_by_record_id():
    # Same instructor record id in both tables -> +40; different id -> no bonus.
    same = m.score_match(_workout(), WDATE, TYPE_MAP, _ride())["score"]
    diff = m.score_match(_workout(), WDATE, TYPE_MAP, _ride(Instructor=["recOther"]))["score"]
    assert same - diff == 40, (same, diff)


def test_candidate_selection_orders_best_then_second():
    best_ride = _ride()
    weak_ride = _ride(
        _id="recRide2", ClassTimeDate="2026-04-17T09:30:00.000Z",
        RideTitle="30 min Climb Ride", RideDuration_min=30,
        Instructor=["recOther"], PowerZoneType="",
    )
    out_of_window = _ride(_id="recRide3", ClassTimeDate="2026-04-19T07:00:00.000Z")
    best, second = m.find_best_candidates(
        _workout(), WDATE, TYPE_MAP, [best_ride, weak_ride, out_of_window]
    )
    assert best["ride_id"] == "recRide1" and best["score"] == 120
    assert second["ride_id"] == "recRide2"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"\nAll {len(tests)} tests passed.")
