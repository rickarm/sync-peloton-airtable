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
    ride = _ride(_id="recRide3", ClassTimeDate="2026-04-21T07:00:00.000Z")  # 96h > 48h
    res = m.score_match(_workout(), WDATE, TYPE_MAP, ride)
    assert res["score"] == -999, res


def test_window_is_48h():
    assert m.MAX_TIME_WINDOW_HOURS == 48
    # A ride 40h away is now inside the window (would have been rejected at 24h).
    ride = _ride(_id="recRide4", ClassTimestamp="2026-04-18 23:00 (-07)")
    res = m.score_match(_workout(), WDATE, TYPE_MAP, ride)
    assert res["score"] != -999, res


def test_ride_prefers_time_bearing_timestamp():
    # Ride.date should come from ClassTimestamp (with HH:MM), not the date-only
    # ClassTimeDate formula — and ignore the timezone suffix (wall-clock).
    ride = m.Ride({"id": "r", "fields": {
        "ClassTimestamp": "2026-04-17 21:00 (-04)",
        "ClassTimeDate": "2026-04-18",  # formula drifted a day; must NOT win
        "RideTitle": "x", "RideDuration_min": 45, "Instructor": [],
        "PowerZoneType": "", "ClassID": "c",
    }})
    assert ride.date == datetime(2026, 4, 17, 21, 0), ride.date


def test_same_day_classes_separated_by_air_time():
    # Two identical-title/instructor/duration classes on the SAME day: the one
    # matching the workout's air time must outscore the other.
    wf = _workout()
    took_at = datetime(2026, 4, 17, 21, 0)  # took the 21:00 class
    near = m.Ride({"id": "near", "fields": {
        "ClassTimestamp": "2026-04-17 21:00 (-04)", "RideTitle": wf["Title"],
        "RideDuration_min": 45, "Instructor": ["recMattW"],
        "PowerZoneType": "Power Zone Endurance", "ClassID": "c1"}})
    far = m.Ride({"id": "far", "fields": {
        "ClassTimestamp": "2026-04-17 06:00 (-04)", "RideTitle": wf["Title"],
        "RideDuration_min": 45, "Instructor": ["recMattW"],
        "PowerZoneType": "Power Zone Endurance", "ClassID": "c2"}})
    s_near = m.score_match(wf, took_at, TYPE_MAP, near)["score"]
    s_far = m.score_match(wf, took_at, TYPE_MAP, far)["score"]
    assert s_near == 120 and s_far == 105, (s_near, s_far)  # +15 vs +0 time bonus


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
    out_of_window = _ride(_id="recRide3", ClassTimeDate="2026-04-21T07:00:00.000Z")  # 96h
    best, second = m.find_best_candidates(
        _workout(), WDATE, TYPE_MAP, [best_ride, weak_ride, out_of_window]
    )
    assert best["ride_id"] == "recRide1" and best["score"] == 120
    assert second["ride_id"] == "recRide2"


def test_classify_unlinked():
    # Strong, no close second -> auto-match
    assert m.classify_unlinked(120, 40) == "auto-match"
    assert m.classify_unlinked(85, 40) == "auto-match"
    # High score (>=90) ignores a close second -> still auto-match (cap kept)
    assert m.classify_unlinked(120, 118) == "auto-match"
    # Just over threshold with a near-tie -> genuinely ambiguous
    assert m.classify_unlinked(82, 80) == "ambiguous"
    assert m.classify_unlinked(85, 81) == "ambiguous"
    # Below threshold: a close second is NOT ambiguous, just too-low
    # (this is the low-score noise that a wider window was surfacing)
    assert m.classify_unlinked(55, 52) == "too-low"
    assert m.classify_unlinked(40, 40) == "too-low"
    # Below threshold, no second -> too-low
    assert m.classify_unlinked(70, None) == "too-low"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"\nAll {len(tests)} tests passed.")
