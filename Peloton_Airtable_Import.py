#!/usr/bin/env python3
"""
Import/update Peloton workouts into Airtable from a Peloton CSV export.

What it does
------------
- Reads a Peloton workout export CSV
- Normalizes common Peloton column names
- By default, imports **incrementally**: only creates rows whose
  Workout_timestamp is not already in Airtable (existing rows are left alone)
- With --full, upserts every CSV row (updates existing records too) — use for
  backfills or after changing how fields are parsed
- Writes in Airtable batches of 10 records
- Supports dry-run mode (reports would-create/would-update counts when a token
  is available)
- Emits a small summary at the end

How to run
----------
export AIRTABLE_TOKEN="pat_xxx"
python peloton_airtable_import.py \
  --base-id appBmQA2p3z2Fdofa \
  --table-id tblBuzhfztfwgE59f \
  --csv "/path/to/peloton.csv"

Dry run:
python peloton_airtable_import.py \
  --base-id appBmQA2p3z2Fdofa \
  --table-id tblBuzhfztfwgE59f \
  --csv "/path/to/peloton.csv" \
  --dry-run

Notes
-----
- This script only imports/updates the Peloton table.
- It does NOT yet do Peloton <-> Peloton-Rides matching.
- AvgResistance is converted from whole-number percent to decimal when needed:
    45 -> 0.45
  If the value is already decimal-like (0.45), it is left alone.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import sys
import time
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

import requests


# ----------------------------
# Airtable-specific config
# ----------------------------
# Base/table IDs come from peloton-sync.conf (overridable via
# ~/.peloton-sync.conf, env vars, or CLI flags).

from peloton_config import load_config

import workout_id_lookup

_CFG = load_config()
BASE_ID = _CFG.get("AIRTABLE_BASE_ID", "")
TABLE_ID = _CFG.get("PELOTON_TABLE_ID", "")
INSTRUCTOR_TABLE_ID = _CFG.get("PELOTON_INSTRUCTOR_TABLE_ID", "")
WORKOUT_IDS_CMD = _CFG.get("PELOTON_WORKOUT_IDS_CMD", "")

# Your Peloton table field IDs from the current base schema.
INSTRUCTOR_NAME_FIELD_ID = "fldfA0KxrFEfYpVQM"

# Known name differences between Peloton CSV and Airtable instructor table.
INSTRUCTOR_NAME_ALIASES: Dict[str, str] = {
    "Leanne Hainsby-Alldis": "Leanne Hainsby",
}

FIELD_IDS = {
    "Workout_timestamp": "fldLajy5EBHnICqj2",
    "Live_OnDemand": "fldkfVaggPIWfRGxl",
    "Length": "fldwLnP1zsR8bicBz",
    "FitnessDiscipline": "fldk5KsbJEXNf5xTz",
    "Title": "flds78C9ixooCSadq",
    "ClassTimestampString": "fldLMvjajp1d6zNAR",
    "TotalOutput": "fldPhZwkz2jMxYX5D",
    "AvgWatts": "fldvoElFViaqvdqj0",
    "AvgResistance": "fldmoDJG9Tf2vw9MH",
    "AvgCadence": "fldzBt57FRGcbx0FX",
    "AvgSpeed": "fldwpdQOw9rW2VSBt",
    "Distance": "fldGy1BcELLqfihzC",
    "CaloriesBurned": "fldf59N7qMNRWTYvz",
    "AvgHeartrate": "fldKBT7Li6cw4DKau",
    "InstructorName": "fldyBTp2009qXtVsr",
    "Peloton_Workout_ID": "fldiRWeHOnx99NWnH",
}

MERGE_FIELD_ID = FIELD_IDS["Workout_timestamp"]


def normalize_ts(ts: str) -> str:
    """Normalize timestamp to 'YYYY-MM-DD HH:MM' for merge-key comparison.

    Handles the Peloton CSV forms ("2026-03-10 13:11 (-07)",
    "2022-03-24 07:43 (PDT)") and the ISO form ("2026-03-10T13:11:00") so old
    and new records match.

    Two things this deliberately gets right, both of which it previously got
    wrong:

    1. The trailing timezone label is dropped whatever its shape, not just
       numeric offsets. Peloton re-renders historical exports using the DST
       label in force at export time, so one workout arrives as "(PDT)" in
       summer and "(PST)" in winter. Keeping the label in the merge key made
       those two spellings distinct, so the importer created the same workout
       twice — the origin of the duplicate rows cleaned up in Aug 2026.

    2. Only the ISO date/time separator is rewritten. The old blanket
       `.replace("T", " ")` also ate the T inside alphabetic labels, turning
       "(PDT)" into "(PD )" and leaving that debris in the key for 62% of rows.

    Tradeoff worth naming: two genuinely different workouts recorded at the
    same wall-clock minute on the same date in different timezones now share a
    key. That is the same local-minute key the peloton-workout-extract repo
    joins on, and it is far less likely than the DST flip it prevents.
    """
    if not ts:
        return ts
    s = ts.strip()
    s = re.sub(r'\s*\([^)]*\)\s*$', '', s)            # strip " (-07)" / " (PDT)"
    s = re.sub(r'^(\d{4}-\d{2}-\d{2})[T ]', r'\1 ', s)  # ISO separator → space
    s = re.sub(r'(\d{2}:\d{2}):\d{2}.*', r'\1', s)     # strip seconds
    return s.strip()


# Flexible CSV aliases so the script survives minor export differences.
CSV_ALIASES = {
    "Workout_timestamp": [
        "Workout Timestamp",
        "Workout Timestamp (Local)",
        "Workout_Timestamp",
        "workout_timestamp",
    ],
    "Live_OnDemand": [
        "Live/On-Demand",
        "Live / On-Demand",
        "Live_OnDemand",
        "live_on_demand",
    ],
    "Length": [
        "Length (minutes)",
        "Length",
        "Duration (minutes)",
        "duration_minutes",
    ],
    "FitnessDiscipline": [
        "Fitness Discipline",
        "Discipline",
        "FitnessDiscipline",
        "fitness_discipline",
    ],
    "Title": [
        "Title",
        "Workout Title",
        "title",
    ],
    "ClassTimestampString": [
        "Class Timestamp",
        "Class Timestamp String",
        "ClassTimestampString",
        "class_timestamp",
    ],
    "TotalOutput": [
        "Total Output",
        "Output",
        "total_output",
    ],
    "AvgWatts": [
        "Avg. Watts",
        "Average Watts",
        "Avg Watts",
        "avg_watts",
    ],
    "AvgResistance": [
        "Avg. Resistance",
        "Average Resistance",
        "Avg Resistance",
        "avg_resistance",
    ],
    "AvgCadence": [
        "Avg. Cadence (RPM)",
        "Average Cadence (RPM)",
        "Avg Cadence",
        "avg_cadence",
    ],
    "AvgSpeed": [
        "Avg. Speed (mph)",
        "Average Speed (mph)",
        "Avg Speed",
        "avg_speed",
    ],
    "Distance": [
        "Distance (mi)",
        "Distance",
        "distance",
    ],
    "CaloriesBurned": [
        "Calories Burned",
        "Calories",
        "calories_burned",
    ],
    "AvgHeartrate": [
        "Avg. Heartrate",
        "Average Heartrate",
        "Avg Heartrate",
        "Average Heart Rate",
        "avg_heartrate",
    ],
    "InstructorName": [
        "Instructor Name",
        "Instructor",
        "instructor_name",
    ],
}


@dataclass
class ImportStats:
    rows_read: int = 0
    rows_skipped_missing_key: int = 0
    rows_prepared: int = 0
    batches_sent: int = 0
    api_errors: int = 0


def eprint(*args: Any) -> None:
    print(*args, file=sys.stderr)


def chunked(items: List[Dict[str, Any]], size: int) -> Iterable[List[Dict[str, Any]]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def clean_string(value: Any) -> Optional[str]:
    if value is None:
        return None
    s = str(value).strip()
    return s if s else None


def parse_number(value: Any) -> Optional[float]:
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None

    s = s.replace(",", "")
    s = s.replace("%", "")

    try:
        return float(s)
    except ValueError:
        return None


def maybe_int(value: Optional[float]) -> Optional[int]:
    if value is None:
        return None
    if math.isfinite(value):
        if abs(value - round(value)) < 1e-9:
            return int(round(value))
    return None


def normalize_avg_resistance(value: Any) -> Optional[float]:
    """
    Airtable field expects decimal style, e.g. 0.45 rather than 45.
    If CSV already contains decimal (< 1.0 typically), leave it alone.
    """
    n = parse_number(value)
    if n is None:
        return None
    if n > 1.0:
        return round(n / 100.0, 4)
    return round(n, 4)


def first_present(row: Dict[str, Any], aliases: List[str]) -> Any:
    for key in aliases:
        if key in row and str(row[key]).strip() != "":
            return row[key]
    return None


def build_airtable_fields(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    workout_ts = clean_string(first_present(row, CSV_ALIASES["Workout_timestamp"]))
    if not workout_ts:
        return None

    fields: Dict[str, Any] = {
        FIELD_IDS["Workout_timestamp"]: workout_ts,
    }

    # strings
    for logical_name in [
        "Live_OnDemand",
        "FitnessDiscipline",
        "Title",
        "ClassTimestampString",
    ]:
        v = clean_string(first_present(row, CSV_ALIASES[logical_name]))
        if v is not None:
            fields[FIELD_IDS[logical_name]] = v

    # numeric, with some integer coercion where appropriate
    length = parse_number(first_present(row, CSV_ALIASES["Length"]))
    if length is not None:
        fields[FIELD_IDS["Length"]] = maybe_int(length) if maybe_int(length) is not None else round(length, 2)

    total_output = parse_number(first_present(row, CSV_ALIASES["TotalOutput"]))
    if total_output is not None:
        fields[FIELD_IDS["TotalOutput"]] = maybe_int(total_output) if maybe_int(total_output) is not None else round(total_output, 2)

    avg_watts = parse_number(first_present(row, CSV_ALIASES["AvgWatts"]))
    if avg_watts is not None:
        fields[FIELD_IDS["AvgWatts"]] = maybe_int(avg_watts) if maybe_int(avg_watts) is not None else round(avg_watts, 2)

    avg_resistance = normalize_avg_resistance(first_present(row, CSV_ALIASES["AvgResistance"]))
    if avg_resistance is not None:
        fields[FIELD_IDS["AvgResistance"]] = avg_resistance

    avg_cadence = parse_number(first_present(row, CSV_ALIASES["AvgCadence"]))
    if avg_cadence is not None:
        fields[FIELD_IDS["AvgCadence"]] = maybe_int(avg_cadence) if maybe_int(avg_cadence) is not None else round(avg_cadence, 2)

    avg_speed = parse_number(first_present(row, CSV_ALIASES["AvgSpeed"]))
    if avg_speed is not None:
        fields[FIELD_IDS["AvgSpeed"]] = round(avg_speed, 2)

    distance = parse_number(first_present(row, CSV_ALIASES["Distance"]))
    if distance is not None:
        fields[FIELD_IDS["Distance"]] = round(distance, 2)

    calories = parse_number(first_present(row, CSV_ALIASES["CaloriesBurned"]))
    if calories is not None:
        fields[FIELD_IDS["CaloriesBurned"]] = maybe_int(calories) if maybe_int(calories) is not None else round(calories, 2)

    avg_hr = parse_number(first_present(row, CSV_ALIASES["AvgHeartrate"]))
    if avg_hr is not None:
        fields[FIELD_IDS["AvgHeartrate"]] = maybe_int(avg_hr) if maybe_int(avg_hr) is not None else round(avg_hr, 2)

    # Store raw instructor name as a temp key — resolved to a record ID later.
    instructor = clean_string(first_present(row, CSV_ALIASES["InstructorName"]))
    if instructor:
        fields["_instructor_name"] = instructor

    return fields


def load_csv_rows(csv_path: str) -> List[Dict[str, Any]]:
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        return list(reader)


def airtable_request(
    session: requests.Session,
    method: str,
    url: str,
    *,
    json_payload: Optional[Dict[str, Any]] = None,
    params: Optional[Dict[str, Any]] = None,
    max_retries: int = 5,
) -> requests.Response:
    backoff = 1.0
    for attempt in range(max_retries):
        resp = session.request(method, url, json=json_payload, params=params, timeout=60)
        if resp.status_code < 400:
            return resp

        # Retry on rate limits / transient failures.
        if resp.status_code in (429, 500, 502, 503, 504):
            retry_after = resp.headers.get("Retry-After")
            sleep_for = float(retry_after) if retry_after else backoff
            eprint(f"Transient Airtable error {resp.status_code}; retrying in {sleep_for:.1f}s")
            time.sleep(sleep_for)
            backoff = min(backoff * 2, 16)
            continue

        return resp

    return resp


def fetch_instructor_map(
    session: requests.Session,
    base_id: str,
    instructor_table_id: str,
) -> Dict[str, str]:
    """Returns {instructor_name: record_id} from the Peloton_Instructor table."""
    url = f"https://api.airtable.com/v0/{base_id}/{instructor_table_id}"
    instructor_map: Dict[str, str] = {}
    offset = None
    while True:
        p: Dict[str, Any] = {"fields[]": INSTRUCTOR_NAME_FIELD_ID, "pageSize": 100, "returnFieldsByFieldId": "true"}
        if offset:
            p["offset"] = offset
        resp = airtable_request(session, "GET", url, params=p)
        if resp.status_code >= 400:
            eprint(f"Error fetching instructors: {resp.status_code} {resp.text[:500]}")
            break
        data = resp.json()
        for rec in data.get("records", []):
            name = rec.get("fields", {}).get(INSTRUCTOR_NAME_FIELD_ID)
            if name:
                instructor_map[name] = rec["id"]
        offset = data.get("offset")
        if not offset:
            break
    return instructor_map


def fetch_existing_records(
    session: requests.Session,
    base_id: str,
    table_id: str,
) -> Dict[str, str]:
    """
    Fetches all existing records and returns a {timestamp_value: record_id} map.
    If duplicates exist in Airtable, keeps the first record_id encountered (oldest).
    """
    url = f"https://api.airtable.com/v0/{base_id}/{table_id}"
    existing: Dict[str, str] = {}
    offset = None

    while True:
        p: Dict[str, Any] = {"fields[]": MERGE_FIELD_ID, "pageSize": 100, "returnFieldsByFieldId": "true"}
        if offset:
            p["offset"] = offset
        resp = airtable_request(session, "GET", url, params=p)
        if resp.status_code >= 400:
            eprint(f"Error fetching existing records: {resp.status_code} {resp.text[:500]}")
            break
        data = resp.json()
        for rec in data.get("records", []):
            ts = rec.get("fields", {}).get(MERGE_FIELD_ID)
            if ts:
                norm = normalize_ts(ts)
                if norm not in existing:
                    existing[norm] = rec["id"]
        offset = data.get("offset")
        if not offset:
            break

    return existing


def apply_workout_ids(
    to_write: List[Dict[str, Any]],
    helper: Optional[str],
    verbose: bool = True,
) -> Dict[str, int]:
    """Fill FIELD_IDS["Peloton_Workout_ID"] on rows we are about to write.

    The CSV has no workout ID column, so this asks the Peloton API (via the
    sibling extract repo) and joins on the normalized merge key. Best-effort:
    any failure is reported and leaves the rows untouched, because dropping a
    workout is far worse than dropping its ID.

    Returns {"filled": n, "missing": n} — "missing" is rows the API had no
    workout for, which is expected for anything Peloton itself never recorded.
    """
    outcome = {"filled": 0, "missing": 0}
    if not to_write:
        return outcome

    keys = [normalize_ts(f.get(MERGE_FIELD_ID, "")) for f in to_write]
    resolved = workout_id_lookup.resolve_helper(helper)
    if not resolved:
        if verbose:
            eprint("Skipping workout IDs: helper not found "
                   f"(looked for {workout_id_lookup.DEFAULT_HELPER}). "
                   "Set PELOTON_WORKOUT_IDS_CMD or pass --workout-ids-cmd.")
        return outcome

    since = workout_id_lookup.earliest_date(keys)
    if verbose:
        eprint(f"Fetching workout IDs via {resolved} (since {since or 'account start'})...")
    try:
        id_map = workout_id_lookup.fetch_workout_id_map(
            resolved, since=since, normalize=normalize_ts
        )
    except workout_id_lookup.WorkoutIdLookupError as exc:
        if verbose:
            eprint(f"Warning: workout ID lookup failed ({exc}); "
                   "continuing without Peloton_Workout_ID.")
        return outcome

    for fields, key in zip(to_write, keys):
        workout_id = id_map.get(key)
        if workout_id:
            fields[FIELD_IDS["Peloton_Workout_ID"]] = workout_id
            outcome["filled"] += 1
        else:
            outcome["missing"] += 1

    if verbose:
        eprint(f"Workout IDs: {outcome['filled']} filled, {outcome['missing']} with no API match.")
    return outcome


def upsert_records(
    token: str,
    base_id: str,
    table_id: str,
    instructor_table_id: str,
    records: List[Dict[str, Any]],
    dry_run: bool = False,
    full: bool = False,
    workout_ids: bool = True,
    workout_ids_cmd: Optional[str] = None,
) -> Dict[str, int]:
    """
    Default (incremental): fetches the existing merge-key map, then only
    CREATES rows whose Workout_timestamp is not already in Airtable. Rows that
    already exist are skipped, so a daily run against the full-history CSV
    writes only the new workouts.

    full=True: legacy upsert — also PATCHes every existing row from the CSV.
    Fetches existing records by ID first, avoiding Airtable's upsert API
    limitation when the table contains duplicate merge-key values.

    Returns counts: {created, updated, skipped_existing, batches_sent, api_errors}
    """
    result = {"created": 0, "updated": 0, "skipped_existing": 0,
              "batches_sent": 0, "api_errors": 0,
              "workout_ids_filled": 0, "workout_ids_missing": 0}
    if not records:
        return result

    if dry_run and not token:
        # Offline dry-run: no API access, just show the first parsed payload.
        first = {k: v for k, v in records[0].items() if not k.startswith("_")}
        print(json.dumps({"dry_run_first_record": first}, indent=2))
        return result

    url = f"https://api.airtable.com/v0/{base_id}/{table_id}"
    session = requests.Session()
    session.headers.update(
        {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
    )

    eprint("Fetching existing records to build merge map...")
    existing = fetch_existing_records(session, base_id, table_id)
    eprint(f"Found {len(existing)} existing records in Airtable.")

    to_update: List[Dict[str, Any]] = []  # {"id": rec_id, "fields": {...}}
    to_create: List[Dict[str, Any]] = []  # {"fields": {...}}

    for fields in records:
        ts = fields.get(MERGE_FIELD_ID)
        norm = normalize_ts(ts) if ts else None
        if norm and norm in existing:
            if full:
                to_update.append({"id": existing[norm], "fields": fields})
            else:
                result["skipped_existing"] += 1
        else:
            to_create.append({"fields": fields})

    # Resolve instructor names → linked record IDs, but only for rows we are
    # actually going to write (skipped rows don't need the lookup table).
    to_write = [r["fields"] for r in to_update] + [r["fields"] for r in to_create]
    unmatched_instructors: set = set()
    if any("_instructor_name" in f for f in to_write):
        eprint("Fetching instructor lookup table...")
        instructor_map = fetch_instructor_map(session, base_id, instructor_table_id)
        eprint(f"Loaded {len(instructor_map)} instructors.")
    else:
        instructor_map = {}
    for fields in to_write:
        raw_name = fields.pop("_instructor_name", None)
        if raw_name:
            normalized = INSTRUCTOR_NAME_ALIASES.get(raw_name, raw_name)
            rec_id = instructor_map.get(normalized)
            if rec_id:
                fields[FIELD_IDS["InstructorName"]] = [rec_id]
            else:
                unmatched_instructors.add(raw_name)

    if workout_ids:
        id_outcome = apply_workout_ids(to_write, workout_ids_cmd)
        result["workout_ids_filled"] = id_outcome["filled"]
        result["workout_ids_missing"] = id_outcome["missing"]

    if dry_run:
        preview: Dict[str, Any] = {
            "dry_run": True,
            "mode": "full" if full else "incremental",
            "would_create": len(to_create),
            "would_update": len(to_update),
            "would_skip_existing": result["skipped_existing"],
            "workout_ids_filled": result["workout_ids_filled"],
            "workout_ids_missing": result["workout_ids_missing"],
        }
        if to_create:
            preview["first_new_record"] = to_create[0]["fields"]
        print(json.dumps(preview, indent=2))
        return result

    batches_sent = 0
    api_errors = 0

    # Updates (PATCH with explicit record IDs) — full mode only
    for batch in chunked(to_update, 10):
        payload = {"records": batch, "typecast": True}
        resp = airtable_request(session, "PATCH", url, json_payload=payload)
        if resp.status_code >= 400:
            api_errors += 1
            eprint("Airtable error:")
            eprint(resp.status_code, resp.text[:2000])
        else:
            batches_sent += 1

    # Creates (POST)
    for batch in chunked(to_create, 10):
        payload = {"records": batch, "typecast": True}
        resp = airtable_request(session, "POST", url, json_payload=payload)
        if resp.status_code >= 400:
            api_errors += 1
            eprint("Airtable error:")
            eprint(resp.status_code, resp.text[:2000])
        else:
            batches_sent += 1

    result["created"] = len(to_create)
    result["updated"] = len(to_update)
    result["batches_sent"] = batches_sent
    result["api_errors"] = api_errors

    eprint(f"Created: {len(to_create)}, Updated: {len(to_update)}, "
           f"Skipped (already in Airtable): {result['skipped_existing']}.")
    if unmatched_instructors:
        eprint(f"Warning: no Airtable match for instructor(s): {sorted(unmatched_instructors)}")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Import/update Peloton CSV into Airtable")
    parser.add_argument("--csv", required=True, help="Path to Peloton CSV export")
    parser.add_argument("--base-id", default=BASE_ID, help="Airtable base ID; defaults to peloton-sync.conf")
    parser.add_argument("--table-id", default=TABLE_ID, help="Airtable table ID for Peloton; defaults to peloton-sync.conf")
    parser.add_argument("--instructor-table-id", default=INSTRUCTOR_TABLE_ID, help="Airtable table ID for the instructor lookup; defaults to peloton-sync.conf")
    parser.add_argument("--token", default=os.getenv("AIRTABLE_TOKEN"), help="Airtable personal access token; defaults to AIRTABLE_TOKEN env var")
    parser.add_argument("--dry-run", action="store_true", help="Report what would be created/updated (and print the first new record payload), but do not write to Airtable")
    parser.add_argument("--recent", type=int, default=None, metavar="N", help="Only process the N most recent workouts from the CSV")
    parser.add_argument("--full", action="store_true", help="Also update every existing row from the CSV (legacy upsert). Default is incremental: only create rows not yet in Airtable")
    parser.add_argument("--no-workout-ids", action="store_true", help="Skip the Peloton API lookup that fills Peloton_Workout_ID (offline runs, or when the extract repo is unavailable)")
    parser.add_argument("--workout-ids-cmd", default=WORKOUT_IDS_CMD or None, help="Path to peloton-workout-ids.sh; defaults to PELOTON_WORKOUT_IDS_CMD or the sibling peloton-workout-extract checkout")
    args = parser.parse_args()

    if not args.token and not args.dry_run:
        eprint("Missing Airtable token. Set AIRTABLE_TOKEN or pass --token.")
        return 2

    missing = [name for name, val in (
        ("--base-id", args.base_id),
        ("--table-id", args.table_id),
        ("--instructor-table-id", args.instructor_table_id),
    ) if not val]
    if missing:
        eprint(f"Missing {', '.join(missing)} — set them in peloton-sync.conf "
               "(or ~/.peloton-sync.conf) or pass the flags explicitly.")
        return 2

    if not os.path.exists(args.csv):
        eprint(f"CSV not found: {args.csv}")
        return 2

    raw_rows = load_csv_rows(args.csv)

    if args.recent is not None:
        ts_aliases = CSV_ALIASES["Workout_timestamp"] + ["Workout_timestamp"]
        def _get_ts(row):
            for alias in ts_aliases:
                if alias in row:
                    return row[alias]
            return ""
        raw_rows = sorted(raw_rows, key=_get_ts, reverse=True)[:args.recent]
        print(f"Trimmed CSV to {len(raw_rows)} most recent workouts.")

    stats = ImportStats(rows_read=len(raw_rows))
    airtable_field_records: List[Dict[str, Any]] = []

    for row in raw_rows:
        fields = build_airtable_fields(row)
        if not fields:
            stats.rows_skipped_missing_key += 1
            continue
        airtable_field_records.append(fields)

    # Deduplicate by merge key — keep last occurrence (most recent in CSV)
    seen: Dict[str, int] = {}
    for i, fields in enumerate(airtable_field_records):
        key = normalize_ts(fields.get(MERGE_FIELD_ID, ""))
        seen[key] = i
    airtable_field_records = [airtable_field_records[i] for i in sorted(seen.values())]

    stats.rows_prepared = len(airtable_field_records)

    write_result = upsert_records(
        token=args.token or "",
        base_id=args.base_id,
        table_id=args.table_id,
        instructor_table_id=args.instructor_table_id,
        records=airtable_field_records,
        dry_run=args.dry_run,
        full=args.full,
        workout_ids=not args.no_workout_ids,
        workout_ids_cmd=args.workout_ids_cmd,
    )
    stats.batches_sent = write_result["batches_sent"]
    stats.api_errors = write_result["api_errors"]

    summary = {
        "mode": "full" if args.full else "incremental",
        "rows_read": stats.rows_read,
        "rows_skipped_missing_key": stats.rows_skipped_missing_key,
        "rows_prepared": stats.rows_prepared,
        "created": write_result["created"],
        "updated": write_result["updated"],
        "skipped_existing": write_result["skipped_existing"],
        "workout_ids_filled": write_result["workout_ids_filled"],
        "workout_ids_missing": write_result["workout_ids_missing"],
        "batches_sent": stats.batches_sent,
        "api_errors": stats.api_errors,
        "table_id": args.table_id,
        "merge_field_id": MERGE_FIELD_ID,
        "dry_run": args.dry_run,
    }
    print(json.dumps(summary, indent=2))
    return 0 if stats.api_errors == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
