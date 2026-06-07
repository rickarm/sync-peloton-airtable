# Peloton Scraper

> **Status: superseded.** Workout-URL scraping now lives in the [`peloton-workout-extract`](https://github.com/rickarm/peloton-workout-extract) project. This folder is retained as reference for **class-URL** scraping (scraping a class before it has been taken), tracked in [issue #4](https://github.com/rickarm/peloton-workout-extract/issues/4). Do not delete without checking that issue.

A small Playwright-based script for pulling Peloton class metadata from a Peloton class or workout URL.

## What this does

The script:

- logs into Peloton using credentials from `~/.env`
- opens a Peloton class URL, workout URL, or `classId`
- extracts visible metadata such as:
  - title
  - instructor
  - discipline
  - duration
  - class ID
  - description
  - image URL
- tries to open **More info** and **View Details**
- does a best-effort scrape of visible class-plan segments and power-zone allocations

## Files

- `peloton_class_scrape_env_home.py` — scraper script
- optional output file like `class.json`

## Requirements

Use a Python virtual environment.

Install packages:

```bash
python -m pip install playwright python-dotenv
python -m playwright install chromium
```

## Suggested folder setup

For your setup:

```bash
~/kb/_dev/peloton-scraper
```

Example:

```bash
mkdir -p ~/kb/_dev/peloton-scraper
cd ~/kb/_dev/peloton-scraper

python3 -m venv .venv
source .venv/bin/activate

python -m pip install --upgrade pip
python -m pip install playwright python-dotenv
python -m playwright install chromium
```

## Credentials

This script automatically tries to load environment variables from:

1. `~/.env`
2. `./.env` in the current working directory
3. `.env` next to the script

Expected variables:

```env
PELOTON_EMAIL=you@example.com
PELOTON_PASSWORD=your-password
```

Since you keep your env file at `~/.env`, that is enough.

## Put the script in place

Put the script here:

```bash
~/kb/_dev/peloton-scraper/peloton_class_scrape_env_home.py
```

## Usage

### Activate the environment

```bash
cd ~/kb/_dev/peloton-scraper
source .venv/bin/activate
```

### Run with a Peloton URL as a positional argument

```bash
python peloton_class_scrape_env_home.py "PASTE_PELOTON_URL_HERE"
```

Example:

```bash
python peloton_class_scrape_env_home.py "https://members.onepeloton.com/profile/workouts/ad1f5acc61564e07a7f78b7aede7477e?modal=classDetailsModal&classId=3f536cc3322c4b329de2a589bb4b2c4d"
```

### Run with `--url`

```bash
python peloton_class_scrape_env_home.py --url "PASTE_PELOTON_URL_HERE"
```

### Run with a class ID

```bash
python peloton_class_scrape_env_home.py --class-id "3f536cc3322c4b329de2a589bb4b2c4d"
```

### Save output to JSON

```bash
python peloton_class_scrape_env_home.py "PASTE_PELOTON_URL_HERE" --save-json class.json
```

### Show the browser while debugging

```bash
python peloton_class_scrape_env_home.py "PASTE_PELOTON_URL_HERE" --headful
```

## Recommended shell alias

Add this to `~/.zshrc`:

```bash
alias pelotonmeta='cd ~/kb/_dev/peloton-scraper && source .venv/bin/activate && python peloton_class_scrape_env_home.py'
```

Reload your shell:

```bash
source ~/.zshrc
```

Then run:

```bash
pelotonmeta "PASTE_PELOTON_URL_HERE"
```

Or save to a file:

```bash
pelotonmeta "PASTE_PELOTON_URL_HERE" --save-json class.json
```

## Example output

The script prints JSON like:

```json
{
  "class_id": "3f536cc3322c4b329de2a589bb4b2c4d",
  "class_detail_url": "https://members.onepeloton.com/...",
  "ride_title": "60 min Power Zone Endurance Ride",
  "subtitle": null,
  "instructor": "Matt Wilpers",
  "discipline": "cycling",
  "duration_minutes": 60,
  "class_timestamp": null,
  "description": "Build your aerobic base...",
  "image_url": "https://...",
  "segments": [],
  "zone_allocations": []
}
```

## Notes and caveats

- This is a best-effort scraper based on Peloton’s current web UI.
- Peloton can change selectors or page structure at any time.
- Some metadata may not appear unless you are logged in and the UI renders correctly.
- Detailed class-plan and zone data are more fragile than basic metadata.
- Use conservatively for personal use.

## Troubleshooting

### `externally-managed-environment`

If Homebrew Python blocks `pip install`, use the virtual environment steps above. Do not install into the system Python.

### Browser does not launch

Re-run:

```bash
python -m playwright install chromium
```

### Login fields are not found

Peloton may have changed the login flow or selectors. Try:

```bash
python peloton_class_scrape_env_home.py "PASTE_PELOTON_URL_HERE" --headful
```

so you can see what the browser is doing.

### Credentials are missing

Check that `~/.env` contains:

```env
PELOTON_EMAIL=you@example.com
PELOTON_PASSWORD=your-password
```

## Next possible enhancements

- output fields mapped directly to your Airtable `Peloton-Rides` schema
- write JSON to Airtable automatically
- bulk process multiple class URLs
- more robust extraction of class-plan segments and zone timing
