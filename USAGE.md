# Usage Reference

Quick-reference for all commands. See [README.md](README.md) for setup and full context, or [SETUP.md](SETUP.md) to set up against your own Peloton account and Airtable base.

---

## peloton-sync.sh — Import CSV to Airtable

The primary day-to-day command. Downloads CSV from Peloton, runs it into Airtable.

**Step 1:** Download your workout CSV from Peloton
> Members site → Profile → Workout History → Download CSV
> Save to `~/Downloads/` — filename should match `<PELOTON_USERNAME>_workouts*.csv`
> (username, base ID, and table IDs are configured in `peloton-sync.conf`;
> per-user override: `~/.peloton-sync.conf`)

**Step 2:** Run the sync

```bash
# Daily sync (incremental, the default) — auto-detect newest CSV in ~/Downloads/
./peloton-sync.sh

# Specify a CSV path explicitly
./peloton-sync.sh "/path/to/Big__Cheese_workouts_2026.csv"

# Dry run — report would-create/skip counts, write nothing
./peloton-sync.sh --dry-run

# Full re-sync — also rewrites every existing row (rarely needed; see below)
./peloton-sync.sh --full
```

**Requires:** `AIRTABLE_TOKEN` set in `~/.env`

### Default (incremental) vs `--full` — know which one you want

Even though the Peloton CSV always contains your *entire* workout history, the
default run does **not** touch history. It compares the CSV to Airtable on
`Workout_timestamp` and only **creates** the workouts Airtable doesn't have yet.

| | `./peloton-sync.sh` (default) | `./peloton-sync.sh --full` |
|---|---|---|
| New workouts (not yet in Airtable) | created | created |
| Workouts already in Airtable | **skipped — never touched** | updated (rewritten from the CSV) |
| Post-import matcher | `--unlinked-only` (just links the new stuff) | full re-score of every workout |
| Airtable API writes on a daily run | a handful | hundreds (every row) |
| When to use | **every normal run** | backfills; after a parsing/field change; old rows look wrong |

Both modes are idempotent: re-running against the same CSV creates 0 new rows.

**For agents (Mandy):** always run `--dry-run` first and sanity-check
`would_create` (should be roughly the number of new workouts since the last
sync) and `would_skip_existing` (should be nearly everything else), then run
without flags to commit. Never add `--full` unless Rick explicitly asks for a
full re-sync.

> After a successful (non-dry-run) import, `peloton-sync.sh` automatically runs
> `peloton-match.sh` to link the new workouts to their class metadata —
> `--unlinked-only` by default, a full re-score when `--full` was given. The
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

# Full mode — also update every existing row from the CSV (legacy upsert)
python3 Peloton_Airtable_Import.py \
  --csv "/path/to/workouts.csv" \
  --full

# Only consider the N most recent workouts in the CSV
python3 Peloton_Airtable_Import.py \
  --csv "/path/to/workouts.csv" \
  --recent 25
```

**Output:** JSON summary printed to stdout on completion. On a typical daily
(incremental) run, `created` is small and `skipped_existing` is nearly
everything — that's the expected shape, not a problem:

```json
{
  "mode": "incremental",
  "rows_read": 312,
  "rows_skipped_missing_key": 0,
  "rows_prepared": 312,
  "created": 2,
  "updated": 0,
  "skipped_existing": 310,
  "batches_sent": 1,
  "api_errors": 0,
  "dry_run": false
}
```

`updated` is only ever nonzero in `--full` mode.

A dry run (with a token available) prints the would-be counts instead, plus the
first new record's payload for a spot-check:

```json
{
  "dry_run": true,
  "mode": "incremental",
  "would_create": 2,
  "would_update": 0,
  "would_skip_existing": 310,
  "first_new_record": { "...": "..." }
}
```

---

## peloton-match.sh — Link workouts to class metadata

Links each workout to its `Peloton-Rides` class row so the workout inherits the
class's per-zone breakdown. Two paths, in order:

1. **By class ID (deterministic).** The workout's `Peloton_Workout_ID` is
   resolved through the Peloton API to the class it was actually taken from.
   Used whenever exactly one `Peloton-Rides` row claims that `ClassID`. No score
   is consulted.
2. **By score (fallback).** Similarity across instructor, duration, title, time
   proximity and Power Zone type, auto-linking at >= 80. Used for pre-workout-ID
   history, and wherever several rows claim the same `ClassID`.

`--no-class-ids` skips path 1 entirely. The lookup is best-effort: a missing
helper or expired Peloton session degrades to scoring with a warning.

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

Runs automatically after a `peloton-sync.sh` import — with `--unlinked-only` on
a default sync, or as a full re-score when the sync was run with `--full`.
Flags pass straight through to `Peloton_Match.py`.

The matcher always *computes* a score for every workout it processes, but only
*writes* when something actually changes (new link, new lock, or a different
`MatchScore`). Unchanged rows are counted in `unchanged` and skipped, so
re-runs write ~0 records instead of rewriting every row.

**Output:** JSON summary on stdout (aggregate counts + a `rows` table, sorted
newest-*taken* first); per-workout action log on stderr.

Each row carries two dates: **`taken`** = when you did the workout
(`Workout_timestamp`, drives the sort = "recent rides"), and **`class_date`** =
when the class aired (`ClassTimestampString`, the actual match key — a recent
ride can map to an old class).

```json
{
  "workouts_processed": 312,
  "auto_matched": 2,
  "locked": 0,
  "scored_only": 304,
  "ambiguous": 2,
  "no_candidate": 4,
  "unchanged": 308,
  "updates_prepared": 4,
  "batches_sent": 1,
  "api_errors": 0,
  "dry_run": false,
  "rows": [
    {"taken": "2026-06-08 17:35 (-07)", "class_date": "2026-04-21 21:00 (-07)",
     "title": "45 min Power Zone Endurance Ride", "action": "auto-matched, locked",
     "score": 120, "ride": "45 min Power Zone Endurance Ride"}
  ]
}
```

A large `unchanged` and a small `updates_prepared` is the normal steady state —
it means the stored scores already match and nothing needed rewriting.

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

**Old workout rows in Airtable look wrong or are missing fields**
→ The default sync never touches existing rows. Re-sync history from the CSV
with `./peloton-sync.sh --full` (also triggers a full matcher re-score).

**Daily sync summary shows a big `skipped_existing` count**
→ Expected. That's the incremental default skipping workouts already in
Airtable; only `created` rows are new writes.

**Scraper redirects to login or returns empty data**
→ Session expired. Re-run `python scraper/peloton_login_save_session.py`

**`playwright: command not found` or import error**
→ Install dependencies: `pip install -r requirements.txt && playwright install chromium`

**Instructor names not matching in Airtable**
→ Check the import summary for `Warning: no Airtable match for instructor(s): [...]`
→ Add an alias in `INSTRUCTOR_NAME_ALIASES` in `Peloton_Airtable_Import.py`
