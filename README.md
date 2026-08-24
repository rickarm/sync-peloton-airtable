# sync-peloton-airtable

Tools for syncing Peloton workout data into an Airtable base.

> **Single writer policy.** Peloton workouts are written to Airtable through
> **one** path only: the idempotent **Python CSV Import** (`./peloton-sync.sh`).
> It merges on `Workout_timestamp`, so it can be re-run safely and never creates
> duplicates. The old automated folder watcher is **retired** (see Workflow 1) —
> it was a second writer, and two writers caused duplicate workout rows.

Workflows:

1. **Automated Claude MCP Sync** — *(retired)* a launchd watcher that
   auto-synced CSVs dropped in `~/Downloads`. Disabled to enforce a single writer.
2. **Python CSV Import** — Download a Peloton workout CSV and run `./peloton-sync.sh`
   (auto-detects the newest CSV in `~/Downloads`). **Incremental by default** —
   only creates workouts not yet in Airtable; `--full` re-syncs history. Requires
   an Airtable personal access token in `~/.env`. **This is the only write path.**
3. **Class Scraper** — Scrape class metadata (segments, zone allocations, description) from the Peloton website using a saved browser session.

---

## Credentials and API Keys

| Workflow | What's needed | Where it lives |
|---|---|---|
| Python CSV Import | `AIRTABLE_TOKEN` (Airtable personal access token) | `~/.env` (never committed) |
| Class Scraper | `PELOTON_EMAIL`, `PELOTON_PASSWORD` | `~/.env` (never committed) |
| Workout ID lookup (optional) | a working `peloton-workout-extract` checkout; its own 1Password token | path via `PELOTON_WORKOUT_IDS_CMD` in `peloton-sync.conf` |

Nothing sensitive is hardcoded in any script.

---

## Project Structure

```
sync-peloton-airtable/
├── peloton-claude-sync.sh           # (RETIRED) old launchd watcher entry point — reference only
├── peloton-sync.sh                  # Python-based CSV import entry point (runs the matcher after import)
├── workout_id_lookup.py             # Resolves Peloton_Workout_ID (shells out to peloton-workout-extract)
├── peloton-match.sh                 # Workout ↔ class matcher entry point (agent-runnable)
├── Peloton_Airtable_Import.py       # Reads CSV, imports new workouts into Airtable (incremental; --full upserts)
├── Peloton_Match.py                 # Links Peloton workouts to Peloton-Rides class metadata
├── Peloton_Dedup.py                 # Removes duplicate records from Airtable
├── requirements.txt                 # Python dependencies
├── .env.example                     # Template showing required env vars
├── launchd/
│   └── com.rickarmbrust.peloton-sync.plist  # (RETIRED) LaunchAgent for the old watcher — reference only
└── scraper/
    ├── peloton_login_save_session.py      # Run once to authenticate and save session
    ├── peloton_class_scrape_stateful.py   # Scrapes class metadata using saved session
    ├── Peloton Scraper README.md          # Scraper-specific notes
    └── archive/                           # Older scraper iterations (reference only)
        ├── peloton_class_scrape.py
        ├── peloton_class_scrape_v2.py
        ├── peloton_class_scrape_v3.py
        ├── peloton_class_scrape_env_home.py
        └── peloton_class_scrape_stateful_working.py
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
playwright install chromium   # only needed for the scraper
```

### 3. Set credentials in `~/.env`

The shell script and Python scripts load credentials from `~/.env` (your home directory, not the project folder). Add the following:

```bash
# Airtable — required for CSV import and dedup
AIRTABLE_TOKEN=pat_your_airtable_personal_access_token

# Peloton — required only for the class scraper
PELOTON_EMAIL=your@email.com
PELOTON_PASSWORD=your-peloton-password
```

To get an Airtable token: https://airtable.com/create/tokens
Scope needed: `data.records:read`, `data.records:write` on the target base.

### 4. Save a Peloton browser session (scraper only)

The scraper authenticates via a saved Playwright browser session rather than API credentials. Run this once per machine (or when the session expires):

```bash
cd scraper
python peloton_login_save_session.py
```

A browser window will open. Log into Peloton, then press Enter in the terminal. This saves `scraper/peloton_state.json` (gitignored — never commit it).

---

## Workflow 1: Automated Claude MCP Sync (RETIRED)

Retired 2026-06 to enforce the single-writer policy: the launchd folder watcher
was a second writer alongside the Python importer, and two concurrent writers
are what caused duplicate workout rows. All syncing now goes through
**Workflow 2** (`./peloton-sync.sh`).

`peloton-claude-sync.sh` and `launchd/com.rickarmbrust.peloton-sync.plist`
remain in the repo for reference only. The full setup, behavior, and
field-mapping documentation for this workflow lives in git history if it's ever
needed again (and it should only ever be revived if Workflow 2 is retired first
— never run both).

If a machine still has the watcher loaded, unload it:

```bash
launchctl bootout gui/$(id -u)/com.rickarmbrust.peloton-sync
# older macOS: launchctl unload ~/Library/LaunchAgents/com.rickarmbrust.peloton-sync.plist
rm ~/Library/LaunchAgents/com.rickarmbrust.peloton-sync.plist
```

---

## Workflow 2: Python CSV Import (the single write path)

### How it works

1. Download your Peloton workout history CSV from [members.onepeloton.com](https://members.onepeloton.com) → Profile → Workout History → Download.
2. The CSV filename will match `Big__Cheese_workouts*.csv` (your Peloton username).
3. Run the sync script — it auto-detects the most recent matching CSV in `~/Downloads/`:

```bash
./peloton-sync.sh
```

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

## Workflow 2b: Workout ↔ Class Matching

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

## Workflow 3: Class Scraper

### How it works

The scraper loads a saved Playwright session (`scraper/peloton_state.json`) and navigates to a Peloton class page to extract structured metadata not available in the CSV export.

### Usage

```bash
cd scraper

# By class URL
python peloton_class_scrape_stateful.py \
  --url 'https://members.onepeloton.com/classes/cycling?modal=classDetailsModal&classId=CLASS_ID'

# By class ID only
python peloton_class_scrape_stateful.py --class-id CLASS_ID
```

### Output

JSON to stdout:

```json
{
  "class_id": "...",
  "class_detail_url": "...",
  "ride_title": "...",
  "instructor": "...",
  "discipline": "cycling",
  "duration_minutes": 45,
  "class_timestamp": "...",
  "description": "...",
  "segments": [...],
  "zone_allocations": [...]
}
```

### Session expiry

The saved session (`peloton_state.json`) will expire eventually (typically days to weeks). When the scraper fails to load class content or redirects to a login page, re-run:

```bash
cd scraper
python peloton_login_save_session.py
```

---

## Dependencies

| Package | Used by | Notes |
|---|---|---|
| `requests` | `Peloton_Airtable_Import.py`, `Peloton_Match.py`, `Peloton_Dedup.py` | Airtable API calls |
| `playwright` | `peloton_class_scrape_stateful.py`, `peloton_login_save_session.py` | Browser automation |
| `python-dotenv` | `peloton_login_save_session.py` | Optional; loads `.env` files |

Install all at once:

```bash
pip install -r requirements.txt
playwright install chromium
```

---

## Gitignored Files

These files exist locally but are never committed:

| File | Why |
|---|---|
| `.env` | Contains secrets |
| `scraper/peloton_state.json` | Contains browser session cookies |
| `debug.json`, `cycling_debug.*` | Scraper debug artifacts |
| `.venv/` | Python virtual environment |

---

## Extending / Improving

A few known gaps and natural next steps:

- **Peloton ↔ Peloton-Rides matching** — implemented in `Peloton_Match.py` / `peloton-match.sh` (Workflow 2b), and run automatically after each import. Tuning the scoring weights or the auto-match threshold is the natural next step.
- **Scraper → Airtable integration** — The scraper currently outputs JSON to stdout. There's no script yet that takes scraper output and writes it into an Airtable table.
- **Instructor aliases** — `INSTRUCTOR_NAME_ALIASES` in `Peloton_Airtable_Import.py` maps Peloton CSV names to Airtable instructor names. Add entries there if new mismatches appear in the import warnings.
