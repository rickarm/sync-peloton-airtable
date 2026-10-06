#!/usr/bin/env bash
set -euo pipefail

# Credentials come from the process environment only (AIRTABLE_TOKEN), injected
# by the caller, e.g.: op run --environment "$OP_ENVIRONMENT_ID" -- <this script>
# No local secrets file is read, and the token is never put on a command line.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_SCRIPT="$SCRIPT_DIR/Peloton_Airtable_Import.py"

# Load config (username, base/table IDs): repo defaults, then per-user override
[ -f "$SCRIPT_DIR/peloton-sync.conf" ] && source "$SCRIPT_DIR/peloton-sync.conf"
[ -f "$HOME/.peloton-sync.conf" ] && source "$HOME/.peloton-sync.conf"
for var in PELOTON_USERNAME AIRTABLE_BASE_ID PELOTON_TABLE_ID; do
  if [ -z "${!var:-}" ]; then
    echo "Error: $var not set — check peloton-sync.conf (or ~/.peloton-sync.conf)."
    exit 1
  fi
done

BASE_ID="$AIRTABLE_BASE_ID"
TABLE_ID="$PELOTON_TABLE_ID"
DRY_RUN=""
CSV_PATH=""
RECENT_ARG=""
FULL=""

# Parse args
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN="--dry-run" ;;
    --recent=*) RECENT_ARG="--recent ${arg#--recent=}" ;;
    --full) FULL="--full" ;;
    *) CSV_PATH="$arg" ;;
  esac
done

# Validate Python is available
if ! command -v python3 &>/dev/null; then
  echo "Error: python3 not found. Install Python 3 first."
  exit 1
fi

# Validate requests is installed
if ! python3 -c "import requests" &>/dev/null; then
  echo "Error: 'requests' Python package not installed. Run: pip3 install requests"
  exit 1
fi

# Validate the Python script exists
if [ ! -f "$PYTHON_SCRIPT" ]; then
  echo "Error: Import script not found at $PYTHON_SCRIPT"
  exit 1
fi

# CSV directory: PELOTON_CSV_DIR (environment), else ~/.local/share/peloton-sync/csv.
# Same rules as csv_dir.py in peloton-workout-extract, which downloads into it:
# never inside ~/Downloads, ~/Desktop or ~/Documents (macOS protects them per
# app, and an unpermitted process hangs there instead of failing), never inside
# a git repo. Created with mode 700 if missing.
refuse_protected_csv_dir() {
  local dir="$1" name
  for name in Downloads Desktop Documents; do
    case "$dir/" in
      "$HOME/$name/"*)
        echo "Error: PELOTON_CSV_DIR=$dir is inside ~/$name, which macOS protects per app. Pick another directory." >&2
        exit 1 ;;
    esac
  done
}

resolve_csv_dir() {
  local dir="${PELOTON_CSV_DIR:-$HOME/.local/share/peloton-sync/csv}"
  dir="${dir/#\~/$HOME}"
  case "$dir" in /*) ;; *) dir="$PWD/$dir" ;; esac
  dir="${dir%/}"
  refuse_protected_csv_dir "$dir"     # lexically first: don't touch protected folders
  [ -d "$dir" ] || mkdir -p -m 700 "$dir"
  dir="$(cd "$dir" && pwd -P)"
  refuse_protected_csv_dir "$dir"     # again after following symlinks
  if [ "$(git -C "$dir" rev-parse --is-inside-work-tree 2>/dev/null || true)" = "true" ]; then
    echo "Error: PELOTON_CSV_DIR=$dir is inside a git repository. Pick a directory outside any repo." >&2
    exit 1
  fi
  printf '%s\n' "$dir"
}

# Auto-detect CSV if not provided: the newest export in the CSV directory
if [ -z "$CSV_PATH" ]; then
  CSV_DIR="$(resolve_csv_dir)"
  CSV_PATH=$(ls -t "$CSV_DIR/${PELOTON_USERNAME}_workouts"*.csv 2>/dev/null | head -1 || true)
  if [ -z "$CSV_PATH" ]; then
    echo "Error: No Peloton CSV found in $CSV_DIR (looking for ${PELOTON_USERNAME}_workouts*.csv). Download one first: peloton-csv-download.sh"
    exit 1
  fi
  echo "Auto-detected: $CSV_PATH"
fi

# Validate CSV file exists
if [ ! -f "$CSV_PATH" ]; then
  echo "Error: CSV file not found: $CSV_PATH"
  exit 1
fi

# Validate token (skip for dry-run)
if [ -z "${AIRTABLE_TOKEN:-}" ] && [ -z "$DRY_RUN" ]; then
  echo "Error: AIRTABLE_TOKEN not set. Run via: op run --environment \"\$OP_ENVIRONMENT_ID\" -- $0" >&2
  exit 1
fi

# Run import (capture status without aborting on failure)
set +e
python3 "$PYTHON_SCRIPT" \
  --base-id "$BASE_ID" \
  --table-id "$TABLE_ID" \
  --csv "$CSV_PATH" \
  ${DRY_RUN:-} \
  ${RECENT_ARG:-} \
  ${FULL:-}
IMPORT_STATUS=$?
set -e

# After a successful real import, link the new workouts to their class metadata.
# Default: --unlinked-only (new workouts are unlinked; locked rows are skipped).
# --full re-scores everything, e.g. after scoring changes or new ride metadata.
# Best-effort: a matcher failure must not fail the import.
if [ "$IMPORT_STATUS" -eq 0 ] && [ -z "$DRY_RUN" ]; then
  MATCH_SCRIPT="$SCRIPT_DIR/peloton-match.sh"
  if [ -x "$MATCH_SCRIPT" ] || [ -f "$MATCH_SCRIPT" ]; then
    echo "Running Peloton -> Peloton-Rides matcher..."
    MATCH_ARGS=""
    [ -z "$FULL" ] && MATCH_ARGS="--unlinked-only"
    bash "$MATCH_SCRIPT" ${MATCH_ARGS:-} || echo "Warning: matcher failed (import still succeeded)."
  fi
fi

exit "$IMPORT_STATUS"
