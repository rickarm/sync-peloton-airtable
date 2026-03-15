#!/usr/bin/env python3
"""
Delete duplicate Peloton records from Airtable.

Fetches all records, groups by Workout_timestamp, keeps the most recently
created record for each timestamp, and deletes the rest.

Usage:
  python3 Peloton_Dedup.py --base-id appBmQA2p3z2Fdofa --table-id tblBuzhfztfwgE59f --token pat_xxx
  python3 Peloton_Dedup.py --base-id appBmQA2p3z2Fdofa --table-id tblBuzhfztfwgE59f --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections import defaultdict
from typing import Any, Dict, List, Optional

import requests

MERGE_FIELD_ID = "fldLajy5EBHnICqj2"  # Workout_timestamp


def normalize_ts(ts: str) -> str:
    """Normalize to 'YYYY-MM-DD HH:MM' so ISO and Peloton CSV formats match."""
    if not ts:
        return ts
    s = ts.strip()
    s = re.sub(r'\s*\([+-]\d+\)\s*$', '', s)   # strip " (-07)"
    s = s.replace('T', ' ')                      # ISO T → space
    s = re.sub(r'(\d{2}:\d{2}):\d{2}.*', r'\1', s)  # strip seconds
    return s.strip()


def eprint(*args: Any) -> None:
    print(*args, file=sys.stderr)


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
    resp = None
    for attempt in range(max_retries):
        resp = session.request(method, url, json=json_payload, params=params, timeout=60)
        if resp.status_code < 400:
            return resp
        if resp.status_code in (429, 500, 502, 503, 504):
            retry_after = resp.headers.get("Retry-After")
            sleep_for = float(retry_after) if retry_after else backoff
            eprint(f"Transient error {resp.status_code}; retrying in {sleep_for:.1f}s")
            time.sleep(sleep_for)
            backoff = min(backoff * 2, 16)
            continue
        return resp
    return resp


def fetch_all_records(
    session: requests.Session,
    base_id: str,
    table_id: str,
) -> List[Dict[str, Any]]:
    """Returns all records as [{"id": ..., "createdTime": ..., "timestamp": ...}]."""
    url = f"https://api.airtable.com/v0/{base_id}/{table_id}"
    all_records = []
    offset = None
    page = 0

    while True:
        p: Dict[str, Any] = {
            "fields[]": MERGE_FIELD_ID,
            "pageSize": 100,
            "returnFieldsByFieldId": "true",
        }
        if offset:
            p["offset"] = offset
        resp = airtable_request(session, "GET", url, params=p)
        if resp.status_code >= 400:
            eprint(f"Error fetching records: {resp.status_code} {resp.text[:500]}")
            sys.exit(1)
        data = resp.json()
        for rec in data.get("records", []):
            ts = rec.get("fields", {}).get(MERGE_FIELD_ID, "")
            all_records.append({
                "id": rec["id"],
                "createdTime": rec["createdTime"],
                "timestamp": normalize_ts(ts),
            })
        offset = data.get("offset")
        page += 1
        if page % 10 == 0:
            eprint(f"  Fetched {len(all_records)} records so far...")
        if not offset:
            break

    return all_records


def delete_records(
    session: requests.Session,
    base_id: str,
    table_id: str,
    record_ids: List[str],
    dry_run: bool,
) -> int:
    """Deletes records in batches of 10. Returns error count."""
    if not record_ids:
        return 0

    url = f"https://api.airtable.com/v0/{base_id}/{table_id}"
    errors = 0

    for i in range(0, len(record_ids), 10):
        batch = record_ids[i:i + 10]
        if dry_run:
            print(f"  [dry-run] Would delete: {batch}")
            continue
        params = {"records[]": batch}
        resp = airtable_request(session, "DELETE", url, params=params)
        if resp.status_code >= 400:
            errors += 1
            eprint(f"Delete error: {resp.status_code} {resp.text[:500]}")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Deduplicate Peloton Airtable records")
    parser.add_argument("--base-id", required=True)
    parser.add_argument("--table-id", required=True)
    parser.add_argument("--token", default=os.getenv("AIRTABLE_TOKEN"))
    parser.add_argument("--dry-run", action="store_true", help="Show what would be deleted without deleting")
    args = parser.parse_args()

    if not args.token and not args.dry_run:
        eprint("Missing AIRTABLE_TOKEN. Set it or pass --token.")
        return 2

    session = requests.Session()
    session.headers.update({
        "Authorization": f"Bearer {args.token or ''}",
        "Content-Type": "application/json",
    })

    eprint("Fetching all records...")
    all_records = fetch_all_records(session, args.base_id, args.table_id)
    eprint(f"Total records: {len(all_records)}")

    # Group by timestamp
    groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    no_timestamp: List[str] = []
    for rec in all_records:
        if rec["timestamp"]:
            groups[rec["timestamp"]].append(rec)
        else:
            no_timestamp.append(rec["id"])

    # Find duplicates — keep most recently created, delete the rest
    to_delete: List[str] = []
    duplicate_count = 0
    for ts, recs in groups.items():
        if len(recs) > 1:
            duplicate_count += 1
            # Sort by createdTime asc — keep first (oldest, likely has LinkedRide data), delete rest
            recs_sorted = sorted(recs, key=lambda r: r["createdTime"])
            to_delete.extend(r["id"] for r in recs_sorted[1:])

    eprint(f"Unique timestamps: {len(groups)}")
    eprint(f"Timestamps with duplicates: {duplicate_count}")
    eprint(f"Records with no timestamp: {len(no_timestamp)}")
    eprint(f"Records to delete: {len(to_delete) + len(no_timestamp)}")

    all_to_delete = to_delete + no_timestamp

    if not all_to_delete:
        print(json.dumps({"message": "No duplicates found. Table is clean.", "total_records": len(all_records)}))
        return 0

    if args.dry_run:
        eprint("[dry-run] Would delete the following record IDs:")
        for rid in all_to_delete[:20]:
            eprint(f"  {rid}")
        if len(all_to_delete) > 20:
            eprint(f"  ... and {len(all_to_delete) - 20} more")
    else:
        eprint(f"Deleting {len(all_to_delete)} duplicate records...")

    errors = delete_records(session, args.base_id, args.table_id, all_to_delete, args.dry_run)

    summary = {
        "total_records_before": len(all_records),
        "unique_timestamps": len(groups),
        "duplicate_timestamps": duplicate_count,
        "records_deleted": 0 if args.dry_run else len(all_to_delete),
        "errors": errors,
        "dry_run": args.dry_run,
    }
    print(json.dumps(summary, indent=2))
    return 0 if errors == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
