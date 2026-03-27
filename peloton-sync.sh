#!/usr/bin/env bash
set -euo pipefail

# Load credentials from ~/.env if present
[ -f "$HOME/.env" ] && source "$HOME/.env"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_SCRIPT="$SCRIPT_DIR/Peloton_Airtable_Import.py"
BASE_ID="appBmQA2p3z2Fdofa"
TABLE_ID="tblBuzhfztfwgE59f"
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
  CSV_PATH=$(ls -t "$HOME/Downloads/"Big__Cheese_workouts*.csv 2>/dev/null | head -1 || true)
  if [ -z "$CSV_PATH" ]; then
    echo "Error: No Peloton CSV found in ~/Downloads/ (looking for Big__Cheese_workouts*.csv)"
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

# Run
exec python3 "$PYTHON_SCRIPT" \
  --base-id "$BASE_ID" \
  --table-id "$TABLE_ID" \
  --csv "$CSV_PATH" \
  ${DRY_RUN:-} \
  ${TOKEN_ARG:-} \
  ${RECENT_ARG:-}
