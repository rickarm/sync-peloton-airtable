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

### Workflow 1: Claude MCP Sync (preferred, automated)
- Watcher: launchd monitors `~/Downloads` for `Big__Cheese_workouts*.csv`
- Script: `peloton-claude-sync.sh`
- Plist: `launchd/com.rickarmbrust.peloton-sync.plist`
- No API keys needed (uses Claude Code's Airtable MCP)
- State tracking: `~/scripts/logs/peloton-sync-state`

### Workflow 2: Python CSV Import
- Run: `./peloton-sync.sh [csv_path]` or auto-detect from Downloads
- Dry-run: `./peloton-sync.sh --dry-run`
- Requires: `AIRTABLE_TOKEN` in `~/.env`

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
peloton-sync.sh                         # Workflow 2: wrapper script
Peloton_Airtable_Import.py              # Workflow 2: direct Airtable API import
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

Airtable tables:
- `tblBuzhfztfwgE59f` — Peloton workouts
- `tblNVTKvAwzrDOqxM` — Weight (Withings)
- `tbltRUHnRrncwUbnQ` — Instructor lookup

## Gotchas

- Resistance values from CSV must be divided by 100 (45 → 0.45)
- Scraper session cookies expire (days/weeks) — re-run login script
- `peloton-sync.sh` also exists in `~/scripts/` — keep both in sync
- PowerZone type inference from class title: "Power Zone Endurance" → PZE, etc.
- Weight iCloud files: many per-date files are iCloud stubs not downloaded locally; the annual aggregate files (e.g. `HealthAutoExport-2026.json`) are the reliable source
