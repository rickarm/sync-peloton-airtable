# Usage Reference

Quick-reference for all commands. See [README.md](README.md) for setup and full context.

---

## peloton-sync.sh — Import CSV to Airtable

The primary day-to-day command. Downloads CSV from Peloton, runs it into Airtable.

**Step 1:** Download your workout CSV from Peloton
> Members site → Profile → Workout History → Download CSV
> Save to `~/Downloads/` — filename should match `Big__Cheese_workouts*.csv`

**Step 2:** Run the sync

```bash
# Auto-detect most recent CSV in ~/Downloads/
./peloton-sync.sh

# Specify a CSV path explicitly
./peloton-sync.sh "/path/to/Big__Cheese_workouts_2026.csv"

# Dry run — prints first record payload, no writes to Airtable
./peloton-sync.sh --dry-run
./peloton-sync.sh "/path/to/file.csv" --dry-run
```

**Requires:** `AIRTABLE_TOKEN` set in `~/.env`

> After a successful (non-dry-run) import, `peloton-sync.sh` automatically runs
> `peloton-match.sh` to link the new workouts to their class metadata. The
> matcher is best-effort — if it fails, the import still succeeds.

---

## Peloton_Airtable_Import.py — Import (direct)

Run the importer directly if you need more control than the shell script provides.

```bash
# Basic import
python3 Peloton_Airtable_Import.py \
  --csv "/path/to/workouts.csv" \
  --base-id appBmQA2p3z2Fdofa \
  --table-id tblBuzhfztfwgE59f

# With token passed explicitly (instead of from env)
python3 Peloton_Airtable_Import.py \
  --csv "/path/to/workouts.csv" \
  --base-id appBmQA2p3z2Fdofa \
  --table-id tblBuzhfztfwgE59f \
  --token pat_xxx

# Dry run
python3 Peloton_Airtable_Import.py \
  --csv "/path/to/workouts.csv" \
  --base-id appBmQA2p3z2Fdofa \
  --dry-run
```

**Output:** JSON summary printed to stdout on completion.

```json
{
  "rows_read": 312,
  "rows_skipped_missing_key": 0,
  "rows_prepared": 312,
  "batches_sent": 32,
  "api_errors": 0,
  "dry_run": false
}
```

---

## peloton-match.sh — Link workouts to class metadata

Links **Peloton** workout records to their **Peloton-Rides** class metadata
(`LinkedRide`) so each workout inherits the class's Power Zone breakdown. Safe
for agents (e.g. Mandy) to run. Idempotent — re-running never re-links locked
records.

```bash
# Preview — compute scores + report actions, write nothing
./peloton-match.sh --dry-run

# Score all workouts, auto-link confident matches (score >= 80), lock linked records
./peloton-match.sh

# Skip already-locked records (faster)
./peloton-match.sh --unlinked-only

# Only the N most-recent workouts
./peloton-match.sh --recent 10
```

**Requires:** `AIRTABLE_TOKEN` set in `~/.env` — required even for `--dry-run`,
because scores are read from live Airtable data.

Runs automatically after `peloton-sync.sh` import. Flags pass straight through
to `Peloton_Match.py`.

**Output:** JSON summary on stdout (aggregate counts + a `rows` table, newest
first, with `date`/`title`/`action`/`score`/`ride`); per-workout action log on
stderr (each line prefixed with the workout date).

```json
{
  "workouts_processed": 312,
  "auto_matched": 8,
  "locked": 14,
  "scored_only": 290,
  "ambiguous": 2,
  "no_candidate": 6,
  "updates_prepared": 312,
  "batches_sent": 32,
  "api_errors": 0,
  "dry_run": false,
  "rows": [
    {"date": "2026-04-17 07:00 (-07)", "title": "45 min Power Zone Endurance Ride",
     "action": "auto-matched, locked", "score": 120, "ride": "45 min Power Zone Endurance Ride"}
  ]
}
```

The `rows` table is the easy way to scan results — e.g. pipe to `jq` to see just
the auto-matches: `./peloton-match.sh --dry-run | jq '.rows[] | select(.action | startswith("auto-matched"))'`

---

## Peloton_Match.py — Matcher (direct)

Run the matcher directly for full control over base/table IDs.

```bash
# Dry run
python3 Peloton_Match.py --dry-run

# Default IDs (Health-Tracking base)
python3 Peloton_Match.py

# Override IDs / token explicitly
python3 Peloton_Match.py \
  --base-id appBmQA2p3z2Fdofa \
  --peloton-table-id tblBuzhfztfwgE59f \
  --rides-table-id tblht11eg2nJ5gh3o \
  --token pat_xxx
```

---

## Peloton_Dedup.py — Remove duplicate records

Run this if the same workout appears more than once in Airtable. Keeps the most recently created record for each timestamp, deletes the rest.

```bash
# Dry run first — shows what would be deleted
python3 Peloton_Dedup.py \
  --base-id appBmQA2p3z2Fdofa \
  --table-id tblBuzhfztfwgE59f \
  --dry-run

# Live run
python3 Peloton_Dedup.py \
  --base-id appBmQA2p3z2Fdofa \
  --table-id tblBuzhfztfwgE59f

# With token passed explicitly
python3 Peloton_Dedup.py \
  --base-id appBmQA2p3z2Fdofa \
  --table-id tblBuzhfztfwgE59f \
  --token pat_xxx
```

**Requires:** `AIRTABLE_TOKEN` set in `~/.env` (or passed via `--token`)

---

## Scraper — peloton_login_save_session.py

Run **once per machine** (or when the session expires) to authenticate and save a browser session.

```bash
cd scraper
python peloton_login_save_session.py
```

A Chrome window will open. Log into Peloton manually if the auto-fill doesn't complete, then press **Enter** in the terminal.

Saves `scraper/peloton_state.json` (gitignored).

**Requires:** `PELOTON_EMAIL` and `PELOTON_PASSWORD` in `~/.env`

```bash
# Save to a custom path
python peloton_login_save_session.py --state-file /path/to/state.json
```

**Session expires** after days to weeks. Signs: scraper redirects to login or returns empty data. Re-run this script to refresh.

---

## Scraper — peloton_class_scrape_stateful.py

Scrapes class metadata from a Peloton class page. Uses saved session from above.

```bash
cd scraper

# By full class URL
python peloton_class_scrape_stateful.py \
  --url 'https://members.onepeloton.com/classes/cycling?modal=classDetailsModal&classId=CLASS_ID_HERE'

# By class ID only
python peloton_class_scrape_stateful.py --class-id CLASS_ID_HERE

# URL as positional argument (no flag needed)
python peloton_class_scrape_stateful.py 'https://members.onepeloton.com/classes/...'
```

**Output:** JSON to stdout. Pipe to a file to save:

```bash
python peloton_class_scrape_stateful.py --class-id abc123 > class_abc123.json
```

**Sample output fields:**
```
class_id, class_detail_url, ride_title, instructor, discipline,
duration_minutes, class_timestamp, description, segments, zone_allocations
```

**Requires:** `scraper/peloton_state.json` (run `peloton_login_save_session.py` first)

---

## Environment Variables Reference

All set in `~/.env` (home directory, not project folder).

| Variable | Used by | Description |
|---|---|---|
| `AIRTABLE_TOKEN` | `peloton-sync.sh`, `Peloton_Airtable_Import.py`, `peloton-match.sh`, `Peloton_Match.py`, `Peloton_Dedup.py` | Airtable personal access token |
| `PELOTON_EMAIL` | `scraper/peloton_login_save_session.py` | Peloton account email |
| `PELOTON_PASSWORD` | `scraper/peloton_login_save_session.py` | Peloton account password |

---

## Common Issues

**`AIRTABLE_TOKEN not set`**
→ Add `AIRTABLE_TOKEN=pat_xxx` to `~/.env`

**`No Peloton CSV found in ~/Downloads/`**
→ Download the CSV from Peloton first, or pass the path explicitly: `./peloton-sync.sh "/path/to/file.csv"`

**Scraper redirects to login or returns empty data**
→ Session expired. Re-run `python scraper/peloton_login_save_session.py`

**`playwright: command not found` or import error**
→ Install dependencies: `pip install -r requirements.txt && playwright install chromium`

**Instructor names not matching in Airtable**
→ Check the import summary for `Warning: no Airtable match for instructor(s): [...]`
→ Add an alias in `INSTRUCTOR_NAME_ALIASES` in `Peloton_Airtable_Import.py`
