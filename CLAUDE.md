# Sync-Peloton-Airtable: Peloton Workout Sync

Three independent workflows for syncing Peloton workout data to Airtable.

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

## Architecture

```
peloton-claude-sync.sh                  # Workflow 1: MCP-based sync
peloton-sync.sh                         # Workflow 2: wrapper script
Peloton_Airtable_Import.py              # Workflow 2: direct Airtable API import
Peloton_Dedup.py                        # Dedup utility
scraper/
  peloton_class_scrape_stateful.py      # Workflow 3: class scraper
  peloton_login_save_session.py         # Workflow 3: session setup
```

## Environment

Credentials in `~/.env` (home dir, NOT repo):
- `AIRTABLE_TOKEN` — for Workflow 2
- `PELOTON_EMAIL` / `PELOTON_PASSWORD` — for Workflow 3 (scraper)

Airtable target:
- Base: `appBmQA2p3z2Fdofa`
- Table: `tblBuzhfztfwgE59f` (Peloton workouts)
- Instructor lookup: `tbltRUHnRrncwUbnQ`

## Gotchas

- Uses shared venv: `~/Dev/sleep-airtable/.venv/`
- Resistance values from CSV must be divided by 100 (45 → 0.45)
- Scraper session cookies expire (days/weeks) — re-run login script
- `peloton-sync.sh` also exists in `~/scripts/` — keep both in sync
- PowerZone type inference from class title: "Power Zone Endurance" → PZE, etc.
