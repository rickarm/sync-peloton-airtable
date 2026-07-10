#!/usr/bin/env python3
"""
Import/update Peloton workouts into Airtable from a Peloton CSV export.

What it does
------------
- Reads a Peloton workout export CSV
- Normalizes common Peloton column names
- Upserts records into the Airtable "Peloton" table using Workout_timestamp as the merge key
- Writes in Airtable batches of 10 records
- Supports dry-run mode
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
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests


# ----------------------------
# Airtable-specific config
# ----------------------------
# Base/table IDs come from peloton-sync.conf (overridable via
# ~/.peloton-sync.conf, env vars, or CLI flags).

from peloton_config import load_config

_CFG = load_config()
BASE_ID = _CFG.get("AIRTABLE_BASE_ID", "")
TABLE_ID = _CFG.get("PELOTON_TABLE_ID", "")
INSTRUCTOR_TABLE_ID = _CFG.get("PELOTON_INSTRUCTOR_TABLE_ID", "")

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
}

MERGE_FIELD_ID = FIELD_IDS["Workout_timestamp"]


def normalize_ts(ts: str) -> str:
    """Normalize timestamp to 'YYYY-MM-DD HH:MM' for merge-key comparison.

    Handles both Peloton CSV format ("2026-03-10 13:11 (-07)") and
    ISO format ("2026-03-10T13:11:00") so old and new records match.
    """
    if not ts:
        return ts
    s = ts.strip()
    s = re.sub(r'\s*\([+-]\d+\)\s*$', '', s)   # strip " (-07)"
    s = s.replace('T', ' ')                      # ISO T → space
    s = re.sub(r'(\d{2}:\d{2}):\d{2}.*', r'\1', s)  # strip seconds
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


def upsert_records(
    token: str,
    base_id: str,
    table_id: str,
    instructor_table_id: str,
    records: List[Dict[str, Any]],
    dry_run: bool = False,
) -> Tuple[int, int]:
    """
    Manual upsert: fetches existing records first, then updates by record ID
    or creates new records. Avoids Airtable's upsert API limitation when the
    table contains duplicate merge-key values.

    Returns: (batches_sent, api_errors)
    """
    if not records:
        return 0, 0

    if dry_run:
        first = {k: v for k, v in records[0].items() if not k.startswith("_")}
        print(json.dumps({"dry_run_first_record": first}, indent=2))
        return 0, 0

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

    eprint("Fetching instructor lookup table...")
    instructor_map = fetch_instructor_map(session, base_id, instructor_table_id)
    eprint(f"Loaded {len(instructor_map)} instructors.")
    unmatched_instructors: set = set()

    to_update: List[Dict[str, Any]] = []  # {"id": rec_id, "fields": {...}}
    to_create: List[Dict[str, Any]] = []  # {"fields": {...}}

    for fields in records:
        # Resolve instructor name → linked record ID
        raw_name = fields.pop("_instructor_name", None)
        if raw_name:
            normalized = INSTRUCTOR_NAME_ALIASES.get(raw_name, raw_name)
            rec_id = instructor_map.get(normalized)
            if rec_id:
                fields[FIELD_IDS["InstructorName"]] = [rec_id]
            else:
                unmatched_instructors.add(raw_name)
        ts = fields.get(MERGE_FIELD_ID)
        norm = normalize_ts(ts) if ts else None
        if norm and norm in existing:
            to_update.append({"id": existing[norm], "fields": fields})
        else:
            to_create.append({"fields": fields})

    batches_sent = 0
    api_errors = 0

    # Updates (PATCH with explicit record IDs)
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

    eprint(f"Updated: {len(to_update)} records, Created: {len(to_create)} records.")
    if unmatched_instructors:
        eprint(f"Warning: no Airtable match for instructor(s): {sorted(unmatched_instructors)}")
    return batches_sent, api_errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Import/update Peloton CSV into Airtable")
    parser.add_argument("--csv", required=True, help="Path to Peloton CSV export")
    parser.add_argument("--base-id", default=BASE_ID, help="Airtable base ID; defaults to peloton-sync.conf")
    parser.add_argument("--table-id", default=TABLE_ID, help="Airtable table ID for Peloton; defaults to peloton-sync.conf")
    parser.add_argument("--instructor-table-id", default=INSTRUCTOR_TABLE_ID, help="Airtable table ID for the instructor lookup; defaults to peloton-sync.conf")
    parser.add_argument("--token", default=os.getenv("AIRTABLE_TOKEN"), help="Airtable personal access token; defaults to AIRTABLE_TOKEN env var")
    parser.add_argument("--dry-run", action="store_true", help="Parse and print the first record payload, but do not write to Airtable")
    parser.add_argument("--recent", type=int, default=None, metavar="N", help="Only process the N most recent workouts from the CSV")
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

    batches_sent, api_errors = upsert_records(
        token=args.token or "",
        base_id=args.base_id,
        table_id=args.table_id,
        instructor_table_id=args.instructor_table_id,
        records=airtable_field_records,
        dry_run=args.dry_run,
    )
    stats.batches_sent = batches_sent
    stats.api_errors = api_errors

    summary = {
        "rows_read": stats.rows_read,
        "rows_skipped_missing_key": stats.rows_skipped_missing_key,
        "rows_prepared": stats.rows_prepared,
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
