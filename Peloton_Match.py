#!/usr/bin/env python3
"""
Match Peloton workout-history records to their Peloton-Rides class metadata.

This is a standalone port of the in-app Airtable "Peloton -> Peloton-Rides
matcher v3" Scripting extension, so it can be run directly from the command
line (or by an agent) instead of from the Airtable UI.

What it does
------------
For every record in the Peloton (workout history) table, it scores every
Peloton-Rides (class metadata) record as a candidate and:

  - Always writes MatchScore (the best candidate's score), so partial matches
    are visible.
  - Auto-links (sets LinkedRide) + sets MatchLock when an unlinked, unlocked
    workout has a confident, unambiguous best match (score >= 80).
  - Locks (MatchLock) records that already have a LinkedRide so a future run
    never re-links them.
  - Never overwrites the LinkedRide of a locked record.

Linking a workout to its class lets the workout inherit the class's Power Zone
duration breakdown. Because the same class is taken repeatedly, matching is by
similarity (instructor, duration, title, time proximity, Power Zone type)
within a +/- 24h window rather than by a single key.

Scoring (mirrors the v3 extension)
----------------------------------
  instructor exact            +40
  duration exact / +-1 min    +25 / +12
  title similarity >=.95/.75/.5  +30 / +22 / +12
  time proximity <=1h/3h/12h  +15 / +10 / +5
  power-zone hint exact/family  +10 / +5
Auto-match threshold: 80, with an ambiguity guard (don't auto-link if the
second-best candidate is within 5 points of the best and best < 90).

How to run
----------
export AIRTABLE_TOKEN="pat_xxx"

  python Peloton_Match.py --dry-run        # compute + report, write nothing
  python Peloton_Match.py                   # score + auto-link + lock
  python Peloton_Match.py --unlinked-only   # skip locked records (faster)
  python Peloton_Match.py --recent 10       # only the 10 most-recent workouts

A token is required even for --dry-run because scores are computed from live
Airtable data (reads, not just writes).

Note on linked fields
---------------------
The Airtable REST API returns linked-record fields (InstructorName, Instructor,
Type) as arrays of *record IDs*, not {name} objects like the in-app
getCellValue. So instructors are compared by linked record ID (both tables link
the same Peloton_Instructor table), and Type names are resolved via a lookup
map for the Power Zone hint.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple


# ── Airtable config ───────────────────────────────────────────────────────────

BASE_ID = "appBmQA2p3z2Fdofa"
PELOTON_TABLE_ID = "tblBuzhfztfwgE59f"      # Peloton (workout history)
RIDES_TABLE_ID = "tblht11eg2nJ5gh3o"        # Peloton-Rides (class metadata)
TYPE_TABLE_ID = "tblcUCbRTQbN6B4uK"         # Peloton_type (for Power Zone hint)

# Thresholds (mirror the extension)
AUTO_MATCH_THRESHOLD = 80
MAX_TIME_WINDOW_HOURS = 24

# Peloton (workout) field names
P_LINKED_RIDE = "LinkedRide"
P_MATCH_LOCK = "MatchLock"
P_MATCH_SCORE = "MatchScore"
P_WORKOUT_TS = "Workout_timestamp"          # when the workout was actually done
P_CLASS_TS_STRING = "ClassTimestampString"  # when the class aired (the match key)
P_CLASS_TS_DATE = "ClassTimestampDate"      # formula date of the class air time
P_TITLE = "Title"
P_INSTRUCTOR = "InstructorName"
P_LENGTH = "Length"
P_DISCIPLINE = "FitnessDiscipline"
P_TYPE = "Type"

PELOTON_FIELDS = [
    P_LINKED_RIDE, P_MATCH_LOCK, P_MATCH_SCORE, P_WORKOUT_TS, P_CLASS_TS_STRING,
    P_CLASS_TS_DATE, P_TITLE, P_INSTRUCTOR, P_LENGTH, P_DISCIPLINE, P_TYPE,
]

# Peloton-Rides field names
R_CLASS_TIME_DATE = "ClassTimeDate"
R_CLASS_TIMESTAMP = "ClassTimestamp"
R_RIDE_TITLE = "RideTitle"
R_INSTRUCTOR = "Instructor"
R_DURATION = "RideDuration_min"
R_PZ_TYPE = "PowerZoneType"
R_CLASS_ID = "ClassID"

RIDES_FIELDS = [
    R_CLASS_TIME_DATE, R_CLASS_TIMESTAMP, R_RIDE_TITLE, R_INSTRUCTOR,
    R_DURATION, R_PZ_TYPE, R_CLASS_ID,
]

# Peloton_type primary field (links target)
TYPE_NAME_FIELD = "Name"


def eprint(*args: Any, **kwargs: Any) -> None:
    print(*args, file=sys.stderr, **kwargs)


# ── Scoring helpers (ported from the v3 extension) ─────────────────────────────

def parse_date_safe(value: Any) -> Optional[datetime]:
    """Parse a cell value into a naive datetime (wall-clock), or None.

    Mirrors the extension's parseDateSafe: strips any "(...)" suffix (e.g. the
    "(-07)" timezone marker on ClassTimestampString) and parses what remains.
    Timezone info is dropped so formula-date and text-date fields compare on the
    same wall-clock basis.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if not isinstance(value, str):
        return None

    cleaned = re.sub(r"\(.+?\)", "", value).strip()
    if not cleaned:
        return None

    # Try ISO first (handles "2026-04-17T07:00:00.000Z" and offsets).
    iso = cleaned.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(iso)
        return dt.replace(tzinfo=None)
    except ValueError:
        pass

    # Strip a trailing numeric offset (e.g. "+07:00" / "-0700") then try formats.
    no_tz = re.sub(r"[+-]\d{2}:?\d{2}$", "", cleaned).replace("T", " ").strip()
    for fmt in (
        "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
        "%Y-%m-%d", "%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M", "%m/%d/%Y",
    ):
        try:
            return datetime.strptime(no_tz, fmt)
        except ValueError:
            continue
    return None


def normalize_text(text: Any) -> str:
    if not text:
        return ""
    s = str(text).lower()
    s = s.replace("&", " and ")
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def hour_delta(a: datetime, b: datetime) -> float:
    return abs((a - b).total_seconds()) / 3600.0


def token_set(text: Any) -> set:
    return {t for t in normalize_text(text).split(" ") if t}


def token_similarity(a: Any, b: Any) -> float:
    a_set = token_set(a)
    b_set = token_set(b)
    if not a_set or not b_set:
        return 0.0
    overlap = len(a_set & b_set)
    return overlap / max(len(a_set), len(b_set))


def contains_power_zone_hint(text: Any) -> str:
    t = normalize_text(text)
    if not t:
        return ""
    if "power zone endurance" in t:
        return "endurance"
    if "power zone max" in t:
        return "max"
    if "power zone" in t:
        return "power zone"
    return ""


# ── Cell accessors (REST semantics) ────────────────────────────────────────────

def linked_ids(fields: Dict[str, Any], key: str) -> List[str]:
    """Linked-record fields come back as arrays of record-ID strings."""
    val = fields.get(key)
    if isinstance(val, list):
        return [v for v in val if isinstance(v, str)]
    return []


def as_string(fields: Dict[str, Any], key: str) -> str:
    val = fields.get(key)
    if val is None:
        return ""
    if isinstance(val, list):
        return " ".join(str(v) for v in val)
    return str(val)


def as_number(fields: Dict[str, Any], key: str) -> Optional[float]:
    val = fields.get(key)
    if isinstance(val, (int, float)):
        return float(val)
    return None


# ── Matching ────────────────────────────────────────────────────────────────

class Ride:
    __slots__ = ("id", "date", "title", "duration", "instructor_ids", "pz_type", "class_id")

    def __init__(self, rec: Dict[str, Any]):
        f = rec.get("fields", {})
        self.id = rec["id"]
        self.date = parse_date_safe(f.get(R_CLASS_TIME_DATE))
        self.title = as_string(f, R_RIDE_TITLE)
        self.duration = as_number(f, R_DURATION)
        self.instructor_ids = set(linked_ids(f, R_INSTRUCTOR))
        self.pz_type = as_string(f, R_PZ_TYPE)
        self.class_id = as_string(f, R_CLASS_ID)


def score_match(wf: Dict[str, Any], wdate: datetime, type_map: Dict[str, str],
                ride: Ride) -> Dict[str, Any]:
    score = 0
    reasons: List[str] = []

    if ride.date is None:
        return {"score": -999, "reasons": ["missing ride date"]}

    delta = hour_delta(wdate, ride.date)
    if delta > MAX_TIME_WINDOW_HOURS:
        return {"score": -999, "reasons": ["outside time window"]}

    workout_title = as_string(wf, P_TITLE)
    workout_length = as_number(wf, P_LENGTH)
    workout_instr = set(linked_ids(wf, P_INSTRUCTOR))
    workout_discipline = as_string(wf, P_DISCIPLINE)
    workout_type_names = " ".join(
        type_map.get(tid, "") for tid in linked_ids(wf, P_TYPE)
    )

    # Instructor: both tables link the same Peloton_Instructor table, so compare
    # by record ID (more robust than the extension's name normalization).
    if workout_instr and ride.instructor_ids and (workout_instr & ride.instructor_ids):
        score += 40
        reasons.append("instructor exact +40")

    if workout_length is not None and ride.duration is not None:
        if workout_length == ride.duration:
            score += 25
            reasons.append("duration exact +25")
        elif abs(workout_length - ride.duration) <= 1:
            score += 12
            reasons.append("duration near +12")

    sim = token_similarity(workout_title, ride.title)
    if sim >= 0.95:
        score += 30
        reasons.append("title ~exact +30")
    elif sim >= 0.75:
        score += 22
        reasons.append("title strong +22")
    elif sim >= 0.5:
        score += 12
        reasons.append("title partial +12")

    if delta <= 1:
        score += 15
        reasons.append("time <=1h +15")
    elif delta <= 3:
        score += 10
        reasons.append("time <=3h +10")
    elif delta <= 12:
        score += 5
        reasons.append("time <=12h +5")

    workout_hint = (
        contains_power_zone_hint(workout_title)
        or contains_power_zone_hint(workout_discipline)
        or contains_power_zone_hint(workout_type_names)
    )
    ride_hint = contains_power_zone_hint(ride.title) or normalize_text(ride.pz_type)

    if workout_hint and ride_hint:
        if workout_hint == ride_hint:
            score += 10
            reasons.append("pz type exact +10")
        elif workout_hint == "power zone" and "power zone" in ride_hint:
            score += 5
            reasons.append("pz family +5")

    return {"score": score, "reasons": reasons, "delta": delta}


def find_best_candidates(wf: Dict[str, Any], wdate: datetime,
                         type_map: Dict[str, str], rides: List[Ride]
                         ) -> Tuple[Optional[Dict], Optional[Dict]]:
    best: Optional[Dict] = None
    second: Optional[Dict] = None

    for ride in rides:
        result = score_match(wf, wdate, type_map, ride)
        if result["score"] < 0:
            continue
        candidate = {
            "ride_id": ride.id,
            "ride_title": ride.title,
            "class_id": ride.class_id,
            "score": result["score"],
            "reasons": result["reasons"],
            "delta": result.get("delta"),
        }
        if best is None or candidate["score"] > best["score"]:
            second = best
            best = candidate
        elif second is None or candidate["score"] > second["score"]:
            second = candidate

    return best, second


# ── Airtable API ──────────────────────────────────────────────────────────────

def airtable_request(session: Any, method: str, url: str, *,
                     json_payload: Optional[Dict] = None,
                     params: Optional[Dict] = None,
                     max_retries: int = 5) -> Any:
    backoff = 1.0
    resp = None
    for _ in range(max_retries):
        resp = session.request(method, url, json=json_payload, params=params, timeout=60)
        if resp.status_code < 400:
            return resp
        if resp.status_code in (429, 500, 502, 503, 504):
            retry_after = resp.headers.get("Retry-After")
            wait = float(retry_after) if retry_after else backoff
            eprint(f"  Transient error {resp.status_code}; retrying in {wait:.1f}s")
            time.sleep(wait)
            backoff = min(backoff * 2, 16)
            continue
        return resp
    return resp


def fetch_records(session: Any, base_id: str, table_id: str,
                  fields: List[str]) -> List[Dict[str, Any]]:
    """Fetch all records for the given field names (paginated, 100/page)."""
    url = f"https://api.airtable.com/v0/{base_id}/{table_id}"
    records: List[Dict[str, Any]] = []
    offset = None
    while True:
        params: Dict[str, Any] = {"fields[]": fields, "pageSize": 100}
        if offset:
            params["offset"] = offset
        resp = airtable_request(session, "GET", url, params=params)
        if resp.status_code >= 400:
            eprint(f"  Error fetching {table_id}: {resp.status_code} {resp.text[:500]}")
            break
        data = resp.json()
        records.extend(data.get("records", []))
        offset = data.get("offset")
        if not offset:
            break
    return records


def fetch_type_map(session: Any, base_id: str) -> Dict[str, str]:
    """Return {record_id: type_name} for the Peloton_type table."""
    recs = fetch_records(session, base_id, TYPE_TABLE_ID, [TYPE_NAME_FIELD])
    return {r["id"]: r.get("fields", {}).get(TYPE_NAME_FIELD, "") for r in recs}


def chunked(items: list, size: int) -> Iterable[list]:
    for i in range(0, len(items), size):
        yield items[i:i + size]


# ── Main ──────────────────────────────────────────────────────────────────────

def run(token: str, base_id: str, peloton_table_id: str, rides_table_id: str,
        dry_run: bool, unlinked_only: bool, recent: Optional[int]) -> Dict[str, Any]:
    import requests
    session = requests.Session()
    session.headers.update({
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    })

    eprint("Fetching Peloton-Rides, Peloton workouts, and type lookup...")
    type_map = fetch_type_map(session, base_id)
    ride_recs = fetch_records(session, base_id, rides_table_id, RIDES_FIELDS)
    workout_recs = fetch_records(session, base_id, peloton_table_id, PELOTON_FIELDS)
    rides = [Ride(r) for r in ride_recs]
    eprint(f"Loaded {len(workout_recs)} workouts, {len(rides)} rides, "
           f"{len(type_map)} types.")

    # Two distinct dates per workout:
    #  - match_date: when the CLASS aired — the join key against Peloton-Rides.
    #  - taken_date: when YOU did the workout — what "recent" means for review.
    def match_date(rec: Dict[str, Any]) -> Optional[datetime]:
        f = rec.get("fields", {})
        return parse_date_safe(f.get(P_CLASS_TS_DATE)) or parse_date_safe(f.get(P_CLASS_TS_STRING))

    def taken_date(rec: Dict[str, Any]) -> Optional[datetime]:
        return parse_date_safe(rec.get("fields", {}).get(P_WORKOUT_TS))

    # Process newest-*taken* first, so the log and JSON rows match the user's
    # sense of "recent rides" (recently imported). Undated workouts sort last.
    workout_recs.sort(key=lambda r: taken_date(r) or datetime.min, reverse=True)

    if recent is not None:
        workout_recs = [r for r in workout_recs if taken_date(r) is not None][:recent]
        eprint(f"--recent {recent}: limited to {len(workout_recs)} most-recently-taken workouts.")

    updates: List[Dict[str, Any]] = []
    rows: List[Dict[str, Any]] = []  # per-workout table for the JSON output
    counts = {
        "workouts_processed": 0,
        "auto_matched": 0,
        "locked": 0,
        "scored_only": 0,
        "ambiguous": 0,
        "no_candidate": 0,
        "missing_date": 0,
        "skipped_locked": 0,
    }

    for rec in workout_recs:
        f = rec.get("fields", {})
        rec_id = rec["id"]
        title = as_string(f, P_TITLE) or rec_id
        # taken = when YOU did the ride (disambiguates repeated titles, drives the
        # sort). class_when = when the class aired (the actual match key).
        taken = as_string(f, P_WORKOUT_TS) or "no workout date"
        class_when = as_string(f, P_CLASS_TS_STRING) or as_string(f, P_CLASS_TS_DATE) or "no class date"
        has_link = bool(linked_ids(f, P_LINKED_RIDE))
        locked = bool(f.get(P_MATCH_LOCK))

        if unlinked_only and locked:
            counts["skipped_locked"] += 1
            continue

        counts["workouts_processed"] += 1

        wdate = match_date(rec)
        if wdate is None:
            counts["missing_date"] += 1
            rows.append({"taken": taken, "class_date": class_when, "title": title,
                         "action": "missing class date", "score": None, "ride": ""})
            eprint(f"  [missing class date] taken {taken} | {title}")
            continue

        best, second = find_best_candidates(f, wdate, type_map, rides)

        if best is None:
            fields: Dict[str, Any] = {P_MATCH_SCORE: None}
            if has_link and not locked:
                fields[P_MATCH_LOCK] = True
                counts["locked"] += 1
            counts["no_candidate"] += 1
            updates.append({"id": rec_id, "fields": fields})
            rows.append({"taken": taken, "class_date": class_when, "title": title,
                         "action": "no candidate", "score": None, "ride": ""})
            eprint(f"  [no candidate] taken {taken} | {title} (class {class_when})")
            continue

        ambiguous = (
            second is not None
            and best["score"] < AUTO_MATCH_THRESHOLD + 10
            and (best["score"] - second["score"]) <= 5
        )

        fields = {P_MATCH_SCORE: best["score"]}
        action = "scored only"

        if has_link:
            if not locked:
                fields[P_MATCH_LOCK] = True
                counts["locked"] += 1
                action = "linked, scored, locked"
            else:
                action = "linked, rescored"
            counts["scored_only"] += 1
        elif not locked and best["score"] >= AUTO_MATCH_THRESHOLD and not ambiguous:
            fields[P_LINKED_RIDE] = [{"id": best["ride_id"]}]
            fields[P_MATCH_LOCK] = True
            counts["auto_matched"] += 1
            action = "auto-matched, locked"
        elif locked:
            action = "locked, scored only"
            counts["scored_only"] += 1
        elif ambiguous:
            action = "ambiguous, scored only"
            counts["ambiguous"] += 1
        else:
            action = "score too low"
            counts["scored_only"] += 1

        updates.append({"id": rec_id, "fields": fields})
        rows.append({"taken": taken, "class_date": class_when, "title": title,
                     "action": action, "score": best["score"], "ride": best["ride_title"]})
        eprint(f"  [{action}] taken {taken} | {title} -> {best['ride_title']} "
               f"(class {class_when}; score {best['score']}; {'; '.join(best['reasons'])})")

    # Write
    api_errors = 0
    batches_sent = 0
    if dry_run:
        eprint(f"\nDRY RUN: would update {len(updates)} record(s); writing nothing.")
    else:
        import requests as _requests  # noqa: F401 (session already created)
        url = f"https://api.airtable.com/v0/{base_id}/{peloton_table_id}"
        for batch in chunked(updates, 10):
            resp = airtable_request(session, "PATCH", url,
                                    json_payload={"records": batch})
            if resp.status_code >= 400:
                api_errors += 1
                eprint(f"  Update error: {resp.status_code} {resp.text[:500]}")
            else:
                batches_sent += 1

    summary = {
        **counts,
        "updates_prepared": len(updates),
        "batches_sent": batches_sent,
        "api_errors": api_errors,
        "dry_run": dry_run,
        # per-workout table, newest-taken first: taken (workout date), class_date
        # (class air time = match key), title, action, score, ride
        "rows": rows,
    }
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Match Peloton workouts to Peloton-Rides class metadata.")
    parser.add_argument("--base-id", default=BASE_ID)
    parser.add_argument("--peloton-table-id", default=PELOTON_TABLE_ID)
    parser.add_argument("--rides-table-id", default=RIDES_TABLE_ID)
    parser.add_argument("--token", default=os.getenv("AIRTABLE_TOKEN"),
                        help="Airtable PAT; defaults to AIRTABLE_TOKEN env var")
    parser.add_argument("--dry-run", action="store_true",
                        help="Compute and report matches without writing")
    parser.add_argument("--unlinked-only", action="store_true",
                        help="Skip records that are already locked (faster)")
    parser.add_argument("--recent", type=int, default=None,
                        help="Only process the N most-recent workouts")
    args = parser.parse_args()

    if not args.token:
        eprint("Missing Airtable token. Set AIRTABLE_TOKEN or pass --token "
               "(required even for --dry-run, since scores are read from Airtable).")
        return 2

    summary = run(
        token=args.token,
        base_id=args.base_id,
        peloton_table_id=args.peloton_table_id,
        rides_table_id=args.rides_table_id,
        dry_run=args.dry_run,
        unlinked_only=args.unlinked_only,
        recent=args.recent,
    )

    print(json.dumps(summary, indent=2))
    return 0 if summary["api_errors"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
