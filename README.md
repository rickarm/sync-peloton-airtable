# sync-peloton-airtable

Tools for syncing Peloton workout data into an Airtable base. Two independent workflows:

1. **CSV Import** — Download a Peloton workout CSV export and upsert it into Airtable. This is the primary, day-to-day workflow.
2. **Class Scraper** — Scrape class metadata (segments, zone allocations, description) from the Peloton website using a saved browser session.

---

## Project Structure

```
sync-peloton-airtable/
├── peloton-sync.sh                  # Entry point for the CSV import workflow
├── Peloton_Airtable_Import.py       # Reads CSV, upserts records into Airtable
├── Peloton_Dedup.py                 # Removes duplicate records from Airtable
├── requirements.txt                 # Python dependencies
├── .env.example                     # Template showing required env vars
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
| Instructor lookup table | `tbltRUHnRrncwUbnQ` |
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

## Workflow 1: CSV Import

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

## Workflow 2: Class Scraper

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
| `requests` | `Peloton_Airtable_Import.py`, `Peloton_Dedup.py` | Airtable API calls |
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

- **Peloton ↔ Peloton-Rides matching** — `Peloton_Airtable_Import.py` notes this is not yet implemented. After import, workout records aren't yet linked to ride/class records.
- **Scraper → Airtable integration** — The scraper currently outputs JSON to stdout. There's no script yet that takes scraper output and writes it into an Airtable table.
- **Automation** — The CSV import is currently manual (download CSV, run script). A launchd job or cron could automate this if Peloton ever exposes a proper API or if the scraper is extended to pull workout history directly.
- **Instructor aliases** — `INSTRUCTOR_NAME_ALIASES` in `Peloton_Airtable_Import.py` maps Peloton CSV names to Airtable instructor names. Add entries there if new mismatches appear in the import warnings.
