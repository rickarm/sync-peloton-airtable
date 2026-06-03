#!/usr/bin/env python3
"""
Import Withings weight/body-fat data from HealthAutoExport JSON files into Airtable.

Each JSON file contains one day's reading with two metrics:
  - weight_body_mass (lb)
  - body_fat_percentage (%)

What it does
------------
- Reads one JSON file, a directory of JSON files, or globs a pattern
- Extracts date, weight, and body fat from each reading
- Deduplicates by date (keeps the reading with the latest timestamp per day)
- Upserts into the Airtable Weight table using "Date" as the merge key
- Writes in batches of 10 records
- Supports dry-run mode

How to run
----------
export AIRTABLE_TOKEN="pat_xxx"

# Single file
python Weight_Airtable_Import.py --input /path/to/HealthAutoExport-2026.json

# Directory (all JSON files)
python Weight_Airtable_Import.py --input /path/to/Weight/

# Dry run
python Weight_Airtable_Import.py --input /path/to/Weight/ --dry-run
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple


# ── Airtable config ───────────────────────────────────────────────────────────

BASE_ID = "appBmQA2p3z2Fdofa"
TABLE_ID = "tblNVTKvAwzrDOqxM"

# Airtable field names (must match exactly)
FIELD_DATE = "Date"
FIELD_WEIGHT = "Weight (lb)"
FIELD_BODY_FAT = "Body Fat Percentage (%)"

MERGE_FIELD = FIELD_DATE  # upsert key


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class WeightReading:
    date: str           # YYYY-MM-DD (merge key)
    timestamp: str      # full ISO timestamp for dedup preference (latest wins)
    weight_lb: Optional[float] = None
    body_fat_decimal: Optional[float] = None  # stored as 0.1579, not 15.79


@dataclass
class ImportStats:
    files_read: int = 0
    readings_parsed: int = 0
    readings_deduped: int = 0
    readings_prepared: int = 0
    batches_sent: int = 0
    api_errors: int = 0


# ── Parsing ───────────────────────────────────────────────────────────────────

def parse_date(ts: str) -> str:
    """Extract YYYY-MM-DD from a timestamp like '2026-01-06 06:03:35 -0800'."""
    return ts.strip()[:10]


def parse_readings_from_file(path: str) -> List[WeightReading]:
    """Parse one HealthAutoExport JSON file into WeightReading objects."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"  Warning: could not read {path}: {e}", file=sys.stderr)
        return []

    metrics = data.get("data", {}).get("metrics", [])

    # Index entries by timestamp for each metric
    by_ts: Dict[str, WeightReading] = {}

    for metric in metrics:
        name = metric.get("name", "")
        for entry in metric.get("data", []):
            ts = entry.get("date") or entry.get("start") or ""
            if not ts:
                continue
            date = parse_date(ts)
            if ts not in by_ts:
                by_ts[ts] = WeightReading(date=date, timestamp=ts)
            reading = by_ts[ts]

            qty = entry.get("qty")
            if qty is None:
                continue

            if "weight_body_mass" in name:
                reading.weight_lb = round(float(qty), 2)
            elif "body_fat_percentage" in name:
                # JSON qty is percentage (e.g. 16.926); Airtable stores as decimal (0.16926)
                reading.body_fat_decimal = round(float(qty) / 100, 6)

    return list(by_ts.values())


def collect_json_files(input_path: str) -> List[str]:
    """Return sorted list of JSON files from a file path or directory."""
    if os.path.isfile(input_path):
        return [input_path]
    if os.path.isdir(input_path):
        files = glob.glob(os.path.join(input_path, "*.json"))
        return sorted(files)
    # Treat as glob pattern
    return sorted(glob.glob(input_path))


def deduplicate_readings(readings: List[WeightReading]) -> List[WeightReading]:
    """Keep one reading per date — prefer the reading with the latest timestamp."""
    best: Dict[str, WeightReading] = {}
    for r in readings:
        if r.date not in best or r.timestamp > best[r.date].timestamp:
            best[r.date] = r
    return sorted(best.values(), key=lambda r: r.date)


# ── Airtable API ──────────────────────────────────────────────────────────────

def airtable_request(
    session: Any,
    method: str,
    url: str,
    *,
    json_payload: Optional[Dict] = None,
    params: Optional[Dict] = None,
    max_retries: int = 5,
) -> Any:
    backoff = 1.0
    resp = None
    for _ in range(max_retries):
        resp = session.request(method, url, json=json_payload, params=params, timeout=60)
        if resp.status_code < 400:
            return resp
        if resp.status_code in (429, 500, 502, 503, 504):
            retry_after = resp.headers.get("Retry-After")
            wait = float(retry_after) if retry_after else backoff
            print(f"  Transient error {resp.status_code}; retrying in {wait:.1f}s", file=sys.stderr)
            time.sleep(wait)
            backoff = min(backoff * 2, 16)
            continue
        return resp
    return resp


def fetch_existing_dates(session: Any, base_id: str, table_id: str) -> Dict[str, str]:
    """Return {date_string: record_id} for all existing records."""
    url = f"https://api.airtable.com/v0/{base_id}/{table_id}"
    existing: Dict[str, str] = {}
    offset = None
    while True:
        params: Dict[str, Any] = {"fields[]": MERGE_FIELD, "pageSize": 100}
        if offset:
            params["offset"] = offset
        resp = airtable_request(session, "GET", url, params=params)
        if resp.status_code >= 400:
            print(f"  Error fetching existing records: {resp.status_code} {resp.text[:500]}", file=sys.stderr)
            break
        data = resp.json()
        for rec in data.get("records", []):
            date_val = rec.get("fields", {}).get(MERGE_FIELD)
            if date_val:
                # Airtable date fields return YYYY-MM-DD
                existing[str(date_val)[:10]] = rec["id"]
        offset = data.get("offset")
        if not offset:
            break
    return existing


def chunked(items: list, size: int) -> Iterable[list]:
    for i in range(0, len(items), size):
        yield items[i:i + size]


def upsert_readings(
    token: str,
    base_id: str,
    table_id: str,
    readings: List[WeightReading],
    dry_run: bool = False,
) -> Tuple[int, int]:
    if not readings:
        return 0, 0

    if dry_run:
        sample = readings[0]
        print(json.dumps({
            "dry_run_sample": {
                FIELD_DATE: sample.date,
                FIELD_WEIGHT: sample.weight_lb,
                FIELD_BODY_FAT: sample.body_fat_decimal,
            }
        }, indent=2))
        return 0, 0

    import requests as _requests
    url = f"https://api.airtable.com/v0/{base_id}/{table_id}"
    session = _requests.Session()
    session.headers.update({
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    })

    print("Fetching existing records...", file=sys.stderr)
    existing = fetch_existing_dates(session, base_id, table_id)
    print(f"Found {len(existing)} existing records.", file=sys.stderr)

    to_update: List[Dict] = []
    to_create: List[Dict] = []

    for r in readings:
        fields: Dict[str, Any] = {FIELD_DATE: r.date}
        if r.weight_lb is not None:
            fields[FIELD_WEIGHT] = r.weight_lb
        if r.body_fat_decimal is not None:
            fields[FIELD_BODY_FAT] = r.body_fat_decimal

        if r.date in existing:
            to_update.append({"id": existing[r.date], "fields": fields})
        else:
            to_create.append({"fields": fields})

    print(f"To update: {len(to_update)}, to create: {len(to_create)}", file=sys.stderr)

    batches_sent = 0
    api_errors = 0

    for batch in chunked(to_update, 10):
        resp = airtable_request(session, "PATCH", url, json_payload={"records": batch, "typecast": True})
        if resp.status_code >= 400:
            api_errors += 1
            print(f"  Update error: {resp.status_code} {resp.text[:500]}", file=sys.stderr)
        else:
            batches_sent += 1

    for batch in chunked(to_create, 10):
        resp = airtable_request(session, "POST", url, json_payload={"records": batch, "typecast": True})
        if resp.status_code >= 400:
            api_errors += 1
            print(f"  Create error: {resp.status_code} {resp.text[:500]}", file=sys.stderr)
        else:
            batches_sent += 1

    return batches_sent, api_errors


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="Import Withings weight data into Airtable")
    parser.add_argument("--input", required=True,
                        help="JSON file, directory of JSON files, or glob pattern")
    parser.add_argument("--base-id", default=BASE_ID)
    parser.add_argument("--table-id", default=TABLE_ID)
    parser.add_argument("--token", default=os.getenv("AIRTABLE_TOKEN"),
                        help="Airtable PAT; defaults to AIRTABLE_TOKEN env var")
    parser.add_argument("--dry-run", action="store_true",
                        help="Parse and preview first record without writing to Airtable")
    args = parser.parse_args()

    if not args.token and not args.dry_run:
        print("Missing Airtable token. Set AIRTABLE_TOKEN or pass --token.", file=sys.stderr)
        return 2

    stats = ImportStats()

    files = collect_json_files(args.input)
    if not files:
        print(f"No JSON files found at: {args.input}", file=sys.stderr)
        return 2

    stats.files_read = len(files)
    print(f"Processing {len(files)} file(s)...")

    all_readings: List[WeightReading] = []
    for path in files:
        readings = parse_readings_from_file(path)
        all_readings.extend(readings)

    stats.readings_parsed = len(all_readings)

    deduped = deduplicate_readings(all_readings)
    stats.readings_deduped = len(deduped)
    stats.readings_prepared = len(deduped)

    print(f"Parsed {stats.readings_parsed} readings → {stats.readings_deduped} unique dates")

    batches_sent, api_errors = upsert_readings(
        token=args.token or "",
        base_id=args.base_id,
        table_id=args.table_id,
        readings=deduped,
        dry_run=args.dry_run,
    )
    stats.batches_sent = batches_sent
    stats.api_errors = api_errors

    print(json.dumps({
        "files_read": stats.files_read,
        "readings_parsed": stats.readings_parsed,
        "unique_dates": stats.readings_deduped,
        "batches_sent": stats.batches_sent,
        "api_errors": stats.api_errors,
        "dry_run": args.dry_run,
    }, indent=2))

    return 0 if stats.api_errors == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
