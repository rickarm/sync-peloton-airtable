"""Tests for deterministic workout-to-class linking.

The point of these is that `ride.id` beats the similarity score, including a
confident-looking one — a 105 has already attached a workout to the wrong
class in the live base. And that every way the deterministic path can fail to
be certain falls back to scoring rather than guessing.
"""

import json

import Peloton_Match as M
import workout_id_lookup


def ride(rec_id, class_id, title="45 min Power Zone Endurance Ride"):
    return M.Ride({"id": rec_id, "fields": {"ClassID": class_id, "RideTitle": title}})


# --- class index -----------------------------------------------------------

def test_build_class_index_groups_rows_by_class_id():
    index = M.build_class_index([ride("recA", "c1"), ride("recB", "c2")])
    assert index == {"c1": ["recA"], "c2": ["recB"]}


def test_build_class_index_keeps_every_row_claiming_a_class():
    # The live table has 8 such classes, minted by the scraper's offset bug.
    index = M.build_class_index([ride("recA", "c1"), ride("recB", "c1")])
    assert index["c1"] == ["recA", "recB"]


def test_build_class_index_skips_rows_with_no_class_id():
    assert M.build_class_index([ride("recA", "")]) == {}


# --- deterministic resolution ----------------------------------------------

INDEX = {"c1": ["recA"], "dup": ["recX", "recY"]}
CLASS_MAP = {"w1": "c1", "wfree": None, "wdup": "dup", "wmissing": "c-not-in-table"}


def test_resolves_a_unique_class_to_its_row():
    assert M.deterministic_ride_id("w1", CLASS_MAP, INDEX) == ("recA", "class id")


def test_declines_when_the_workout_has_no_id():
    assert M.deterministic_ride_id(None, CLASS_MAP, INDEX)[0] is None
    assert M.deterministic_ride_id("", CLASS_MAP, INDEX)[0] is None


def test_declines_when_the_workout_is_absent_from_the_export():
    rid, why = M.deterministic_ride_id("unknown", CLASS_MAP, INDEX)
    assert rid is None and why == "workout id not in export"


def test_declines_for_a_workout_that_took_no_class():
    # Freestyle and Apple Health. Must never be forced onto a class.
    rid, why = M.deterministic_ride_id("wfree", CLASS_MAP, INDEX)
    assert rid is None and why == "workout took no class"


def test_declines_when_the_class_is_not_in_the_table():
    rid, why = M.deterministic_ride_id("wmissing", CLASS_MAP, INDEX)
    assert rid is None and why == "class not in Peloton-Rides"


def test_declines_rather_than_picking_between_duplicate_rows():
    # Arbitrarily taking the first would attach the workout to whichever
    # duplicate happened to sort first.
    rid, why = M.deterministic_ride_id("wdup", CLASS_MAP, INDEX)
    assert rid is None and why == "class id claimed by 2 rows"


def test_declines_when_no_class_map_was_loaded():
    # The lookup is best-effort; an empty map must mean "score only".
    assert M.deterministic_ride_id("w1", {}, INDEX)[0] is None


# --- the class map from the helper -----------------------------------------

def helper_payload(records):
    return json.dumps({"workouts": records})


def test_parse_class_map_reads_the_ride_join():
    payload = helper_payload([{"workout_id": "w1", "class_id": "c1"}])
    assert workout_id_lookup.parse_class_map(payload) == {"w1": "c1"}


def test_parse_class_map_keeps_a_classless_workout_as_none():
    payload = helper_payload([{"workout_id": "w1", "class_id": None}])
    assert workout_id_lookup.parse_class_map(payload) == {"w1": None}


def test_parse_class_map_skips_records_with_no_workout_id():
    payload = helper_payload([{"class_id": "c1"}, {"workout_id": "w1", "class_id": "c2"}])
    assert workout_id_lookup.parse_class_map(payload) == {"w1": "c2"}


def test_parse_class_map_keeps_the_first_entry_for_a_repeated_id():
    payload = helper_payload([
        {"workout_id": "w1", "class_id": "newest"},
        {"workout_id": "w1", "class_id": "older"},
    ])
    assert workout_id_lookup.parse_class_map(payload) == {"w1": "newest"}


def test_parse_class_map_rejects_non_json():
    try:
        workout_id_lookup.parse_class_map("not json")
    except workout_id_lookup.WorkoutIdLookupError:
        return
    raise AssertionError("expected WorkoutIdLookupError")


def test_parse_class_map_rejects_json_without_a_workouts_list():
    try:
        workout_id_lookup.parse_class_map(json.dumps({"nope": []}))
    except workout_id_lookup.WorkoutIdLookupError:
        return
    raise AssertionError("expected WorkoutIdLookupError")


def test_fetch_class_map_runs_the_helper_once():
    calls = []

    class Result:
        returncode = 0
        stdout = helper_payload([{"workout_id": "w1", "class_id": "c1"}])
        stderr = ""

    def runner(cmd, **kw):
        calls.append(cmd)
        return Result()

    assert workout_id_lookup.fetch_class_map("/bin/true", runner=runner) == {"w1": "c1"}
    assert len(calls) == 1


def test_fetch_class_map_raises_when_the_helper_fails():
    class Result:
        returncode = 1
        stdout = ""
        stderr = "session expired\n"

    try:
        workout_id_lookup.fetch_class_map("/bin/true", runner=lambda cmd, **kw: Result())
    except workout_id_lookup.WorkoutIdLookupError as exc:
        assert "session expired" in str(exc)
        return
    raise AssertionError("expected WorkoutIdLookupError")


# --- degradation -----------------------------------------------------------

def test_load_class_map_is_empty_when_disabled():
    assert M.load_class_map(False, "/does/not/matter") == {}


def test_load_class_map_is_empty_when_the_helper_is_missing():
    assert M.load_class_map(True, "/no/such/helper.sh") == {}
