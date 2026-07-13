# Sync-Peloton-Airtable: Health Data → Airtable

Syncs Peloton workout data and Withings weight/body-fat data into Airtable (base `appBmQA2p3z2Fdofa`).

## Development Workflow

See `KB-Development-Workflow.md` in the Knowledge Base for the full workflow. Summary:

1. Bugs and features are tracked as **GitHub Issues**
2. Claude works on a **feature branch** (worktrees for isolation in local sessions)
3. Claude pushes the branch and opens a **Pull Request**
4. Rick reviews and merges the PR
5. Adding the `claude` label to an issue triggers Claude via GitHub Actions

## Workflows

> **Single writer policy (important).** Peloton workouts have exactly **one**
> supported write path into Airtable: the idempotent Python importer
> (Workflow 2, `./peloton-sync.sh`). It upserts on `Workout_timestamp`, so it
> can be re-run safely and never creates duplicates. Duplicate rows in the past
> came from having *two* writers — the old folder watcher **and** ad-hoc agent
> MCP writes. **Never** insert Peloton workouts a second way (see the agent rule
> under Workflow 2).

### Workflow 1: Claude MCP Sync (DEPRECATED — disabled)
- **Disabled as of 2026-06.** Do not re-enable without first removing Workflow 2;
  two concurrent writers is what caused duplicate workouts.
- Watcher: launchd monitored `~/Downloads` for `Big__Cheese_workouts*.csv`
- Script: `peloton-claude-sync.sh` (kept for reference only)
- Plist: `launchd/com.rickarmbrust.peloton-sync.plist` (kept for reference only)
- To unload on Rick's machine:
  `launchctl bootout gui/$(id -u)/com.rickarmbrust.peloton-sync` (or, on older
  macOS, `launchctl unload ~/Library/LaunchAgents/com.rickarmbrust.peloton-sync.plist`),
  then `rm ~/Library/LaunchAgents/com.rickarmbrust.peloton-sync.plist`.
- Superseded by Workflow 2.

### Workflow 2: Python CSV Import (the single write path)
- Run: `./peloton-sync.sh [csv_path]` or auto-detect from Downloads
- Dry-run: `./peloton-sync.sh --dry-run`
- Requires: `AIRTABLE_TOKEN` in `~/.env`
- After a successful (non-dry-run) import it auto-runs the matcher (Workflow 2b).
- **Idempotent:** upserts on `Workout_timestamp` (and de-dupes within the CSV),
  so re-running against the same CSV produces 0 new rows.

**How Mandy/agents import a CSV:** after a `Big__Cheese_workouts*.csv` lands in
`~/Downloads`, run `./peloton-sync.sh --dry-run` first, sanity-check the
create/update counts, then run `./peloton-sync.sh` to commit. **Never** write
Peloton workout rows via the Airtable MCP (`create_records_for_table`) directly —
that path has no dedup guard and is what produced duplicate workouts. The
Airtable MCP is fine for *reads*; all *writes* go through `./peloton-sync.sh`.

### Workflow 2b: Workout ↔ Class Matching
Links **Peloton** workout rows to their **Peloton-Rides** class metadata
(`LinkedRide`) so each workout inherits the class's Power Zone breakdown.
Standalone port of the former in-app Airtable Scripting extension — runnable by
agents (e.g. Mandy) without the Airtable UI.

- Run: `./peloton-match.sh` (or `Peloton_Match.py` directly)
- Dry-run (compute + report, no writes): `./peloton-match.sh --dry-run`
- Faster (skip locked): `./peloton-match.sh --unlinked-only`
- Limit scope: `./peloton-match.sh --recent N`
- Requires: `AIRTABLE_TOKEN` in `~/.env` — **needed even for `--dry-run`** (the
  matcher reads live data to score).
- Behavior: always writes `MatchScore`; auto-links (`LinkedRide`) + sets
  `MatchLock` when an unlinked, unlocked workout has a confident, unambiguous
  best match (score ≥ 80); locks already-linked rows; never re-links a locked
  row. Idempotent.
- Scoring/threshold details live in `README.md` (Workflow 2b) and the
  `Peloton_Match.py` docstring.

**How Mandy/agents run this:** when asked to "run the Peloton matcher" or after
a CSV import, run `./peloton-match.sh --dry-run` first, sanity-check the JSON
summary (especially `auto_matched` and `ambiguous`), then run `./peloton-match.sh`
to commit. Report the JSON summary back.

### Workflow 3: Class Scraper
- Login: `python scraper/peloton_login_save_session.py` (one-time)
- Scrape: `python scraper/peloton_class_scrape_stateful.py --class-id <ID>`
- Requires: Playwright, saved session in `scraper/peloton_state.json`

## Weight Sync (Withings via HealthAutoExport)

Imports weight and body fat readings from the Health Auto Export iOS app into Airtable.

- Script: `Weight_Airtable_Import.py` (Python, uses `requests`)
- Shell wrapper: `~/scripts/weight-sync.sh`
- Source: `~/Library/Mobile Documents/iCloud~com~ifunography~HealthExport/Documents/Weight/`
- Airtable table: `tblNVTKvAwzrDOqxM` (Weight)
- Fields written: `Date` (YYYY-MM-DD), `Weight (lb)`, `Body Fat Percentage (%)`
- Merge key: `Date` — one record per day, latest reading wins on duplicates
- Body fat stored as decimal (0.1679), not percentage (16.79) — script divides by 100
- `Lean Body Mass` is a computed field — do NOT write to it
- Requires `AIRTABLE_TOKEN` in `~/.env`

```bash
~/scripts/weight-sync.sh             # sync new files only (since last run)
~/scripts/weight-sync.sh --dry-run  # preview without writing
~/scripts/weight-sync.sh --full     # force re-sync all files
```

Or run Python directly for more control:
```bash
python Weight_Airtable_Import.py --input /path/to/Weight/ --dry-run
python Weight_Airtable_Import.py --input /path/to/HealthAutoExport-2026.json
```

## Architecture

```
peloton-claude-sync.sh                  # Workflow 1: MCP-based sync
peloton-sync.sh                         # Workflow 2: wrapper script (runs matcher after import)
Peloton_Airtable_Import.py              # Workflow 2: direct Airtable API import
peloton-match.sh                        # Workflow 2b: matcher wrapper (agent-runnable)
Peloton_Match.py                        # Workflow 2b: links workouts → Peloton-Rides
Peloton_Dedup.py                        # Dedup utility
Weight_Airtable_Import.py              # Weight/body-fat sync (Withings)
scraper/
  peloton_class_scrape_stateful.py      # Workflow 3: class scraper
  peloton_login_save_session.py         # Workflow 3: session setup
```

## Environment

Credentials in `~/.env` (home dir, NOT repo):
- `AIRTABLE_TOKEN` — for Workflow 2 and Weight sync
- `PELOTON_EMAIL` / `PELOTON_PASSWORD` — for Workflow 3 (scraper)

Sync config (Peloton username, base ID, table IDs) in **`peloton-sync.conf`**
(repo root, checked in). Per-user override: `~/.peloton-sync.conf` (loaded
after, wins). Wrappers source it; Python scripts read it via
`peloton_config.py`. Env vars of the same names and CLI flags override.
Field IDs (`FIELD_IDS` in `Peloton_Airtable_Import.py`) remain hardcoded.

Airtable tables (defaults in `peloton-sync.conf`):
- `tblBuzhfztfwgE59f` — Peloton workouts
- `tblht11eg2nJ5gh3o` — Peloton-Rides (class metadata; matcher target)
- `tblcUCbRTQbN6B4uK` — Peloton_type lookup (matcher Power Zone hint)
- `tblNVTKvAwzrDOqxM` — Weight (Withings)
- `tbltRUHnRrncwUbnQ` — Instructor lookup

## Gotchas

- No venv in this project — scripts use `/opt/homebrew/bin/python3` directly (`requests` is installed system-wide)
- Resistance values from CSV must be divided by 100 (45 → 0.45)
- Scraper session cookies expire (days/weeks) — re-run login script
- The launchd watcher (Workflow 1) is **deprecated/disabled** — the `~/scripts/`
  copy it ran should be unloaded (see Workflow 1). All writes go through the repo
  `./peloton-sync.sh`.
- PowerZone type inference from class title: "Power Zone Endurance" → PZE, etc.
- Matcher (`Peloton_Match.py`) reads via the REST API, which returns linked-record
  fields (`InstructorName`, `Instructor`, `Type`) as arrays of **record IDs**, not
  `{name}` objects like the in-app `getCellValue`. Instructors are compared by
  linked record ID (both tables link the same `Peloton_Instructor` table); `Type`
  names are resolved via a `Peloton_type` lookup map for the Power Zone hint.
- Matcher dates: the match key is the class **air time**, compared from the
  time-bearing text fields `ClassTimestampString` (workout) / `ClassTimestamp`
  (ride), e.g. `"2026-04-21 21:00 (-07)"`. The matcher prefers these over the
  date-only formula fields (`ClassTimestampDate` / `ClassTimeDate`, which return
  `"2026-04-21"` via REST) — the formula dates lose the time of day and can drift
  by ±1 day across timezones. Timezone suffixes (`(-07)`, `(PDT)`) are stripped
  and times compared as wall-clock. Match window is ±48h. (Separately, the
  log/sort use `Workout_timestamp` = when the workout was *taken*.)
- Weight iCloud files: many per-date files are iCloud stubs not downloaded locally; the annual aggregate files (e.g. `HealthAutoExport-2026.json`) are the reliable source
