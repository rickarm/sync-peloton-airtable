#!/usr/bin/env bash
set -euo pipefail

# Load credentials from ~/.env if present
[ -f "$HOME/.env" ] && source "$HOME/.env"

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

# Parse args
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN="--dry-run" ;;
    --recent=*) RECENT_ARG="--recent ${arg#--recent=}" ;;
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

# Auto-detect CSV if not provided
if [ -z "$CSV_PATH" ]; then
  CSV_PATH=$(ls -t "$HOME/Downloads/${PELOTON_USERNAME}_workouts"*.csv 2>/dev/null | head -1 || true)
  if [ -z "$CSV_PATH" ]; then
    echo "Error: No Peloton CSV found in ~/Downloads/ (looking for ${PELOTON_USERNAME}_workouts*.csv)"
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
  echo "Error: AIRTABLE_TOKEN not set. Add it to ~/.env as: AIRTABLE_TOKEN=pat_xxx"
  exit 1
fi

# Build token arg (only if token is set)
TOKEN_ARG=""
if [ -n "${AIRTABLE_TOKEN:-}" ]; then
  TOKEN_ARG="--token $AIRTABLE_TOKEN"
fi

# Run import (capture status without aborting on failure)
set +e
python3 "$PYTHON_SCRIPT" \
  --base-id "$BASE_ID" \
  --table-id "$TABLE_ID" \
  --csv "$CSV_PATH" \
  ${DRY_RUN:-} \
  ${TOKEN_ARG:-} \
  ${RECENT_ARG:-}
IMPORT_STATUS=$?
set -e

# After a successful real import, link the new workouts to their class metadata.
# Best-effort: a matcher failure must not fail the import.
if [ "$IMPORT_STATUS" -eq 0 ] && [ -z "$DRY_RUN" ]; then
  MATCH_SCRIPT="$SCRIPT_DIR/peloton-match.sh"
  if [ -x "$MATCH_SCRIPT" ] || [ -f "$MATCH_SCRIPT" ]; then
    echo "Running Peloton -> Peloton-Rides matcher..."
    bash "$MATCH_SCRIPT" || echo "Warning: matcher failed (import still succeeded)."
  fi
fi

exit "$IMPORT_STATUS"
