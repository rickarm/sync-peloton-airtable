# sync-peloton-airtable

Tools for syncing Peloton workout data into an Airtable base.

> **Running this as an agent?** Start with [AGENT-INSTRUCTIONS.md](AGENT-INSTRUCTIONS.md):
> orientation, the secrets rules, a first-run checklist, and every procedure.

> **Single writer policy.** Peloton workouts are written to Airtable through
> **one** path only: the idempotent **Python CSV Import** (`./peloton-sync.sh`).
> It merges on `Workout_timestamp`, so it can be re-run safely and never creates
> duplicates. The old automated folder watcher is **retired** and its files have
> been removed — it was a second writer, and two writers caused duplicate rows.

Workflows:

1. **Python CSV Import** — Download a Peloton workout CSV and run `./peloton-sync.sh`
   (auto-detects the newest CSV in `PELOTON_CSV_DIR` (default `~/.local/share/peloton-sync/csv`)). **Incremental by default** —
   only creates workouts not yet in Airtable; `--full` re-syncs history. Requires
   `AIRTABLE_TOKEN` in the environment. **This is the only write path.**
2. **Workout ↔ Class Matching** — `./peloton-match.sh`, run automatically after each import.

Class metadata (zone plans, instructors) now comes from `peloton-class-resolve.sh`
in [peloton-workout-extract](https://github.com/rickarm/peloton-workout-extract);
the old Playwright class scraper that lived in `scraper/` has been removed.

---

## Credentials

Every script reads secrets from the **process environment only**. No script
reads or sources a local secrets file, and the wrappers never put a token on a
command line. The caller injects the variables, normally from a 1Password
Environment:

```bash
op run --environment "$OP_ENVIRONMENT_ID" -- ./peloton-sync.sh
```

| Variable | Needed by | Notes |
|---|---|---|
| `AIRTABLE_TOKEN` | `peloton-sync.sh` (real runs), `peloton-match.sh` (always, even `--dry-run`), `Peloton_Airtable_Import.py`, `Peloton_Match.py`, `Peloton_Dedup.py`, `Weight_Airtable_Import.py` | Airtable personal access token, scopes `data.records:read` + `data.records:write` |
| `PELOTON_EMAIL`, `PELOTON_PASSWORD` | `peloton-workout-ids.sh` in the sibling `peloton-workout-extract` repo, which the importer calls to fill `Peloton_Workout_ID` | Only used when that tool has to log in; it inherits this process's environment |

`op run --environment` needs a 1Password CLI build with Environments support
(2.33.0-beta.02 or later; stable 2.35.0 does not have it). The caller also
provides `OP_SERVICE_ACCOUNT_TOKEN`; it never comes from a file.

**Do not use `--token`.** The Python scripts still accept it as an override,
but a token on the command line is visible to every local user via `ps`. Let
the scripts read `AIRTABLE_TOKEN` from the environment.

Nothing sensitive is hardcoded in any script. `peloton-sync.conf` holds only
non-secret IDs; keep it that way.

---

## Project Structure

```
sync-peloton-airtable/
├── peloton-sync.sh                  # Python-based CSV import entry point (runs the matcher after import)
├── workout_id_lookup.py             # Resolves Peloton_Workout_ID (shells out to peloton-workout-extract)
├── peloton-match.sh                 # Workout ↔ class matcher entry point (agent-runnable)
├── Peloton_Airtable_Import.py       # Reads CSV, imports new workouts into Airtable (incremental; --full upserts)
├── Peloton_Match.py                 # Links Peloton workouts to Peloton-Rides class metadata
├── Peloton_Dedup.py                 # Removes duplicate records from Airtable
├── Weight_Airtable_Import.py        # Weight/body-fat sync (Withings via Health Auto Export)
├── peloton-sync.conf                # Non-secret IDs (username, base, tables)
└── requirements.txt                 # Python dependencies
```

---

## Configuration

> **Setting this up for your own Peloton account / your own base?** Follow
> [SETUP.md](SETUP.md) — it walks through the config file, token, and the
> required Airtable schema from scratch.

The Peloton username, Airtable base ID, and table IDs live in **`peloton-sync.conf`**
(repo root) — a shell-sourceable `KEY="value"` file that both the wrapper scripts
and the Python scripts (via `peloton_config.py`) read. To point the tools at a
different Peloton account / Airtable base without editing the repo, copy it to
`~/.peloton-sync.conf` and edit the copy — it loads after the repo file and
overrides it. Environment variables of the same names override both; CLI flags
override everything.

| Key | Default (this base) |
|---|---|
| `PELOTON_USERNAME` (CSV export filename prefix) | `Big__Cheese` |
| `AIRTABLE_BASE_ID` | `appBmQA2p3z2Fdofa` |
| `PELOTON_TABLE_ID` (workouts) | `tblBuzhfztfwgE59f` |
| `PELOTON_RIDES_TABLE_ID` (class metadata) | `tblht11eg2nJ5gh3o` |
| `PELOTON_TYPE_TABLE_ID` (matcher PZ hint) | `tblcUCbRTQbN6B4uK` |
| `PELOTON_INSTRUCTOR_TABLE_ID` | `tbltRUHnRrncwUbnQ` |

Still hardcoded (base-specific **field** IDs, in `Peloton_Airtable_Import.py`):
the merge key field `Workout_timestamp` (`fldLajy5EBHnICqj2`) and the other
`FIELD_IDS`, plus the instructor name field (`fldfA0KxrFEfYpVQM`). A copied
base gets new field IDs, so these would need updating (or the importer switched
to field names) to run against another base.

---

## Setup (New Machine)

> These steps are for a machine syncing to the **original** base. To set up
> against your own Peloton account and your own base, use [SETUP.md](SETUP.md)
> instead.

### 1. Clone the repo

```bash
git clone git@github.com:rickarm/sync-peloton-airtable.git
cd sync-peloton-airtable
```

### 2. Create a Python virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Provide credentials

See [Credentials](#credentials). Put `AIRTABLE_TOKEN` (and the Peloton
credentials) in a 1Password Environment and run the scripts under
`op run --environment "$OP_ENVIRONMENT_ID" --`. Do not create a `.env` file.

To get an Airtable token: https://airtable.com/create/tokens
Scope needed: `data.records:read`, `data.records:write` on the target base.

---

## Retired: automated folder watcher

Retired 2026-06 to enforce the single-writer policy: the launchd folder watcher
was a second writer alongside the Python importer, and two concurrent writers
are what caused duplicate workout rows. Its script (`peloton-claude-sync.sh`)
and plist were removed from the repo in 2026-10; they live in git history.
Never revive it alongside `./peloton-sync.sh`.

If a machine still has the watcher loaded, unload it:

```bash
launchctl bootout gui/$(id -u)/com.rickarmbrust.peloton-sync
# older macOS: launchctl unload ~/Library/LaunchAgents/com.rickarmbrust.peloton-sync.plist
rm ~/Library/LaunchAgents/com.rickarmbrust.peloton-sync.plist
```

---

## Workflow 1: Python CSV Import (the single write path)

### How it works

1. Download your Peloton workout history CSV with
   `peloton-workout-extract/peloton-csv-download.sh`, which saves it to the CSV
   directory: `PELOTON_CSV_DIR` (default `~/.local/share/peloton-sync/csv`). Both repos read that one
   environment variable. It may not be inside `~/Downloads`, `~/Desktop`,
   `~/Documents` (macOS protects those per app, and a process without
   permission hangs there instead of failing) or any git repo; it is created
   with mode 700 if missing. A CSV downloaded by hand in a browser lands in
   `~/Downloads`; pass its path explicitly or move it into the CSV directory.
2. The CSV filename will match `Big__Cheese_workouts*.csv` (your Peloton username).
3. Run the sync script — it auto-detects the most recent matching CSV in the CSV directory:

```bash
op run --environment "$OP_ENVIRONMENT_ID" -- ./peloton-sync.sh
```

(Examples below omit the `op run` prefix for brevity; every real run needs
`AIRTABLE_TOKEN` in the environment.)

Or specify a CSV path explicitly:

```bash
./peloton-sync.sh "/path/to/workouts.csv"
```

Dry run (reports would-create/update/skip counts and the first new record
payload, no writes to Airtable):

```bash
./peloton-sync.sh --dry-run
```

### Two modes: incremental (default) vs `--full`

The Peloton CSV export always contains your **entire workout history**, but the
two modes treat it very differently. The default is incremental: it compares
the CSV to Airtable on `Workout_timestamp` and only **creates** the workouts
Airtable doesn't have yet — rows already in Airtable are **never touched**.
`--full` is the legacy upsert: it *also* rewrites every existing row from the
CSV.

| | `./peloton-sync.sh` (default) | `./peloton-sync.sh --full` |
|---|---|---|
| New workouts (not yet in Airtable) | created | created |
| Workouts already in Airtable | **skipped — never touched** | updated (rewritten from the CSV) |
| Post-import matcher | `--unlinked-only` (links just the new workouts) | full re-score of every workout |
| Airtable API writes on a daily run | a handful | hundreds (every row) |
| When to use | **every normal run** | backfills; after a parsing/field change; old rows look wrong |

Both modes are idempotent — re-running against the same CSV creates 0 new rows.
The practical difference is that the default makes a daily run fast and leaves
history alone, while `--full` is the repair/backfill tool.

**For agents (Mandy):** run `./peloton-sync.sh --dry-run` first and sanity-check
the counts — `would_create` should be roughly the number of new workouts since
the last sync, and `would_skip_existing` should be nearly everything else — then
run `./peloton-sync.sh` to commit. Never add `--full` unless Rick explicitly
asks for a full re-sync.

### What the import does

- Reads all rows from the CSV
- Normalizes column names (handles multiple Peloton export format variations)
- Deduplicates within the CSV by `Workout_timestamp`
- Resolves instructor names to linked Airtable record IDs via the Instructor lookup table
- Creates the missing rows (and, only with `--full`, updates the existing ones)
- Prints a JSON summary on completion — `mode`, `created`, `updated`,
  `skipped_existing` (see [USAGE.md](USAGE.md) for example output; a big
  `skipped_existing` on a daily run is the expected shape)

### Running the dedup script

If duplicate records accumulate in Airtable (e.g., from running the import multiple times before dedup logic was solid), clean them up:

```bash
python3 Peloton_Dedup.py \
  --base-id appBmQA2p3z2Fdofa \
  --table-id tblBuzhfztfwgE59f \
  --dry-run   # preview first

python3 Peloton_Dedup.py \
  --base-id appBmQA2p3z2Fdofa \
  --table-id tblBuzhfztfwgE59f
```

Keeps the most recently created record for each `Workout_timestamp` and deletes the rest.

---

## Workflow 2: Workout ↔ Class Matching

After workouts are imported into the **Peloton** table, they need to be linked
(`LinkedRide`) to the matching class in the **Peloton-Rides** table so each
workout inherits the class's Power Zone duration breakdown. Because the same
class is taken repeatedly, this is a fuzzy match (instructor, duration, title,
time proximity, Power Zone type) within a ±48h window — not a single-key join.
The time signal compares the class **air time** from the time-bearing timestamp
fields (`ClassTimestampString` / `ClassTimestamp`), so same-day look-alike
classes are separated by when they aired.

This is a standalone port of the former in-app Airtable Scripting extension, so
any agent (e.g. Mandy) can run it from the command line.

### Running the matcher

```bash
# Preview — compute scores and report actions, write nothing
./peloton-match.sh --dry-run

# Score every workout, auto-link confident matches, lock linked records
./peloton-match.sh

# Faster: skip records that are already locked
./peloton-match.sh --unlinked-only

# Only the N most-recent workouts
./peloton-match.sh --recent 10
```

`peloton-sync.sh` runs `peloton-match.sh` automatically after a successful
(non-dry-run) import, so a normal CSV sync now also links the new workouts.
By default it passes `--unlinked-only` (new workouts are unlinked, and locked
rows don't need re-scoring); `./peloton-sync.sh --full` runs the full matcher
instead. The matcher is best-effort there: if it fails, the import still
succeeds.

A token is required even for `--dry-run`, because scores are computed from live
Airtable data (it reads both tables).

### What the matcher does

For every Peloton workout, it scores every Peloton-Rides record and:

- Always computes `MatchScore` (best candidate's score), so partial matches are
  visible — but skips the write when the stored score already matches and
  nothing else changes, so re-runs don't rewrite every row.
- Auto-links (`LinkedRide`) **and** sets `MatchLock` when an unlinked, unlocked
  workout has a confident, unambiguous best match (score ≥ 80).
- Sets `MatchLock` on records that already have a `LinkedRide`, so a future run
  never re-links them.
- Never overwrites a locked record's link. Re-running is safe and idempotent.

### Scoring

| Signal | Points |
|---|---|
| Instructor exact (by linked record ID) | +40 |
| Duration exact / within 1 min | +25 / +12 |
| Title similarity ≥.95 / ≥.75 / ≥.5 | +30 / +22 / +12 |
| Time proximity ≤1h / ≤3h / ≤12h | +15 / +10 / +5 |
| Power Zone hint exact / family | +10 / +5 |

Auto-match threshold is **80**, with an ambiguity guard: it will not auto-link
if the second-best candidate is within 5 points of the best and the best is
below 90 (in that case it only scores). The guard only applies at/above the
threshold — a workout whose best is below 80 is reported as `score too low`, not
`ambiguous`, so the `ambiguous` count reflects only genuine high-confidence ties
worth a human look (not low-score noise, which grows with a wider time window).

### Output

A JSON summary on stdout (aggregate counts plus a `rows` table — one entry per
workout, **newest-taken first**); a matching per-workout action log on stderr.

Two dates appear per row, and the distinction matters:

- **`taken`** — when *you did* the workout (`Workout_timestamp`). This drives the
  sort and is what "recent rides" means.
- **`class_date`** — when the *class aired* (`ClassTimestampString`). This is the
  actual match key against Peloton-Rides, so a recently-taken ride can map to an
  old class.

```json
{
  "workouts_processed": 312,
  "auto_matched": 8,
  "locked": 14,
  "scored_only": 290,
  "ambiguous": 2,
  "no_candidate": 6,
  "missing_date": 0,
  "skipped_locked": 0,
  "updates_prepared": 312,
  "batches_sent": 32,
  "api_errors": 0,
  "dry_run": false,
  "rows": [
    {
      "taken": "2026-06-08 17:35 (-07)",
      "class_date": "2026-04-21 21:00 (-07)",
      "title": "45 min Power Zone Endurance Ride",
      "action": "auto-matched, locked",
      "score": 120,
      "ride": "45 min Power Zone Endurance Ride"
    }
  ]
}
```

Tip: filter the table with `jq`, e.g. only the auto-matched rows:
`./peloton-match.sh --dry-run | jq '.rows[] | select(.action | startswith("auto-matched"))'`

---

## Dependencies

| Package | Used by | Notes |
|---|---|---|
| `requests` | `Peloton_Airtable_Import.py`, `Peloton_Match.py`, `Peloton_Dedup.py` | Airtable API calls |

Install:

```bash
pip install -r requirements.txt
```

---

## Gitignored Files

These files exist locally but are never committed:

| File | Why |
|---|---|
| `.env` | Never create one; listed only as a guard against committing secrets |
| `.venv/` | Python virtual environment |

---

## Extending / Improving

A few known gaps and natural next steps:

- **Peloton ↔ Peloton-Rides matching** — implemented in `Peloton_Match.py` / `peloton-match.sh` (Workflow 2), and run automatically after each import. Tuning the scoring weights or the auto-match threshold is the natural next step.
- **Instructor aliases** — `INSTRUCTOR_NAME_ALIASES` in `Peloton_Airtable_Import.py` maps Peloton CSV names to Airtable instructor names. Add entries there if new mismatches appear in the import warnings.
