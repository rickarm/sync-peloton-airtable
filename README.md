# sync-peloton-airtable

Tools for syncing Peloton workout data into an Airtable base. Three independent workflows:

1. **Automated Claude MCP Sync** — Drop a CSV in `~/Downloads`; a launchd watcher auto-copies it and syncs to Airtable via Claude Code's Airtable MCP integration. No API key setup required.
2. **Python CSV Import** — Download a Peloton workout CSV and run the Python import script directly. Requires an Airtable personal access token in `~/.env`.
3. **Class Scraper** — Scrape class metadata (segments, zone allocations, description) from the Peloton website using a saved browser session.

---

## Credentials and API Keys

| Workflow | What's needed | Where it lives |
|---|---|---|
| Automated Claude MCP Sync | Nothing — Airtable auth is handled by the claude.ai MCP integration built into Claude Code | N/A |
| Python CSV Import | `AIRTABLE_TOKEN` (Airtable personal access token) | `~/.env` (never committed) |
| Class Scraper | `PELOTON_EMAIL`, `PELOTON_PASSWORD` | `~/.env` (never committed) |

Nothing sensitive is hardcoded in any script.

---

## Project Structure

```
sync-peloton-airtable/
├── peloton-claude-sync.sh           # Claude MCP sync entry point (used by launchd)
├── peloton-sync.sh                  # Python-based CSV import entry point (runs the matcher after import)
├── peloton-match.sh                 # Workout ↔ class matcher entry point (agent-runnable)
├── Peloton_Airtable_Import.py       # Reads CSV, upserts records into Airtable
├── Peloton_Match.py                 # Links Peloton workouts to Peloton-Rides class metadata
├── Peloton_Dedup.py                 # Removes duplicate records from Airtable
├── requirements.txt                 # Python dependencies
├── .env.example                     # Template showing required env vars
├── launchd/
│   └── com.rickarmbrust.peloton-sync.plist  # macOS LaunchAgent for folder watching
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

## Airtable Configuration

These IDs are hardcoded in `peloton-sync.sh` and the Python scripts. Update them here if the base is ever re-created.

| What | ID |
|---|---|
| Base | `appBmQA2p3z2Fdofa` |
| Peloton workouts table | `tblBuzhfztfwgE59f` |
| Peloton-Rides (class metadata) table | `tblht11eg2nJ5gh3o` |
| Instructor lookup table | `tbltRUHnRrncwUbnQ` |
| Peloton_type lookup table (matcher) | `tblcUCbRTQbN6B4uK` |
| Merge key field (Workout_timestamp) | `fldLajy5EBHnICqj2` |

---

## Setup (New Machine)

### 1. Clone the repo

```bash
git clone git@github.com:richardarmbrust/sync-peloton-airtable.git
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

## Workflow 1: Automated Claude MCP Sync

This is the preferred day-to-day workflow. It requires no API keys and runs automatically when you drop a new Peloton CSV in your Downloads folder.

### How it works

1. You download a Peloton workout CSV from [members.onepeloton.com](https://members.onepeloton.com) → Profile → Workout History → Download. The file will be named `Big__Cheese_workouts*.csv`.
2. The launchd watcher fires when any file changes in `~/Downloads`.
3. The script scans Downloads for `Big__Cheese_workouts*.csv`, copies any new file to `~/Library/Mobile Documents/com~apple~CloudDocs/kb/health-data`, then syncs to Airtable via Claude Code.
4. Airtable deduplication skips any workout already in the table — re-runs are always safe.

### Prerequisites

- [Claude Code](https://claude.ai/code) installed at `~/.local/bin/claude`
- The claude.ai Airtable MCP integration enabled in Claude Code (handles all Airtable auth)

### Setup (one time)

1. Copy the script to `~/scripts/`:

```bash
cp peloton-claude-sync.sh ~/scripts/peloton-sync.sh
chmod +x ~/scripts/peloton-sync.sh
mkdir -p ~/scripts/logs
```

2. Install the LaunchAgent:

```bash
cp launchd/com.rickarmbrust.peloton-sync.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.rickarmbrust.peloton-sync.plist
```

3. Verify it's registered:

```bash
launchctl list | grep peloton
# Should show: -  0  com.rickarmbrust.peloton-sync
```

### Running manually

```bash
# Quick update — only processes workouts from the past 7 days (default)
~/scripts/peloton-sync.sh

# Full historical update — processes all rows in the CSV
~/scripts/peloton-sync.sh --full
```

### What it does

- **Downloads watcher**: on each trigger, scans `~/Downloads` for `Big__Cheese_workouts*.csv` and copies any file not already in the health-data folder
- **Change detection**: uses a state file (`~/scripts/logs/peloton-sync-state`) to track the last-synced CSV modification time — skips the sync entirely if nothing has changed, preventing spurious runs from unrelated Downloads activity
- **Quick mode** (default): filters CSV to the past 7 days before querying Airtable
- **Deduplication**: queries all existing `Workout_timestamp` values in Airtable and skips rows already present
- **Field mapping**: maps CSV columns to Airtable fields, divides `Avg. Resistance` by 100, infers `PowerZone-Type` from the class title
- **Logging**: writes timestamped entries to `~/scripts/logs/peloton-sync.log`; on success logs the full CSV path, on failure logs the exit code and last output lines

### Logs

| File | Contents |
|---|---|
| `~/scripts/logs/peloton-sync.log` | Timestamped run log — success/failure, CSV path, copy events |
| `~/Library/Logs/peloton-sync-stdout.log` | Raw stdout from launchd |
| `~/Library/Logs/peloton-sync-stderr.log` | Raw stderr from launchd |

### Field mapping reference

| CSV Column | Airtable Field | Notes |
|---|---|---|
| Workout Timestamp | `Workout_timestamp` | Dedup key |
| Live/On-Demand | `Live_OnDemand` | Single select |
| Length (minutes) | `Length` | Numeric |
| Fitness Discipline | `FitnessDiscipline` | Single select |
| Title | `Title` | Text; also used to infer `PowerZone-Type` |
| Class Timestamp | `ClassTimestampString` | Text |
| Total Output | `TotalOutput` | Number |
| Avg. Watts | `AvgWatts` | Number |
| Avg. Resistance | `AvgResistance` | Divided by 100 (e.g. `43%` → `0.43`) |
| Avg. Cadence (RPM) | `AvgCadence` | Number |
| Avg. Speed (mph) | `AvgSpeed` | Number |
| Distance (mi) | `Distance` | Number |
| Calories Burned | `CaloriesBurned` | Number |
| Avg. Heartrate | `AvgHeartrate` | Number |
| Avg. Incline | `AvgIncline` | Text |

Skipped columns (linked-record or formula fields): `Instructor Name`, `Type`, `Avg. Pace (min/mi)`, `PK_WorkoutTimestamp`, `AvgPace`, `OutputPerMinute`, `Calculation`, `TotalTimeInZones_min`, `Timestamp`, `Weeknum`, `Weeknum-lookup-string`, `L_Weeks`, `FTP-at-Time`.

Blank or zero metric fields are omitted from the insert rather than written as `0` or `null`.

### PowerZone-Type inference

| Title contains | `PowerZone-Type` value |
|---|---|
| "Power Zone Endurance" | `PZE` |
| "Power Zone Max" | `PZ Max` |
| "Power Zone" (not Endurance or Max) | `PZ` |
| Anything else | `Non-PZ` |

---

## Workflow 2: Python CSV Import

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

Dry run (parses and prints the first record payload, no writes to Airtable):

```bash
./peloton-sync.sh --dry-run
```

### What the import does

- Reads all rows from the CSV
- Normalizes column names (handles multiple Peloton export format variations)
- Deduplicates within the CSV by `Workout_timestamp`
- Resolves instructor names to linked Airtable record IDs via the Instructor lookup table
- **Upserts** into Airtable: updates existing records by timestamp, creates new ones
- Prints a JSON summary on completion

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
time proximity, Power Zone type) within a ±24h window — not a single-key join.

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
(non-dry-run) import, so a normal CSV sync now also links the new workouts. The
matcher is best-effort there: if it fails, the import still succeeds.

A token is required even for `--dry-run`, because scores are computed from live
Airtable data (it reads both tables).

### What the matcher does

For every Peloton workout, it scores every Peloton-Rides record and:

- Always writes `MatchScore` (best candidate's score), so partial matches are visible.
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
below 90 (in that case it only scores).

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
