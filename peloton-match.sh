#!/usr/bin/env bash
set -euo pipefail

# Match Peloton workout-history records to their Peloton-Rides class metadata.
# Standalone wrapper around Peloton_Match.py — safe for agents (e.g. Mandy) to run.
#
#   ./peloton-match.sh                 # score + auto-link + lock
#   ./peloton-match.sh --dry-run       # compute + report, write nothing
#   ./peloton-match.sh --unlinked-only # skip already-locked records (faster)
#   ./peloton-match.sh --recent 10     # only the 10 most-recent workouts
#
# Any flags are passed straight through to Peloton_Match.py.

# Load credentials from ~/.env if present
[ -f "$HOME/.env" ] && source "$HOME/.env"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_SCRIPT="$SCRIPT_DIR/Peloton_Match.py"

# Load config (base/table IDs): repo defaults, then per-user override
[ -f "$SCRIPT_DIR/peloton-sync.conf" ] && source "$SCRIPT_DIR/peloton-sync.conf"
[ -f "$HOME/.peloton-sync.conf" ] && source "$HOME/.peloton-sync.conf"
for var in AIRTABLE_BASE_ID PELOTON_TABLE_ID PELOTON_RIDES_TABLE_ID PELOTON_TYPE_TABLE_ID; do
  if [ -z "${!var:-}" ]; then
    echo "Error: $var not set — check peloton-sync.conf (or ~/.peloton-sync.conf)."
    exit 1
  fi
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
  echo "Error: Matcher script not found at $PYTHON_SCRIPT"
  exit 1
fi

# Token is required even for --dry-run (matcher reads live data to score).
if [ -z "${AIRTABLE_TOKEN:-}" ]; then
  echo "Error: AIRTABLE_TOKEN not set. Add it to ~/.env as: AIRTABLE_TOKEN=pat_xxx"
  exit 1
fi

# Config-derived flags come first so anything in "$@" can still override them.
exec python3 "$PYTHON_SCRIPT" \
  --token "$AIRTABLE_TOKEN" \
  --base-id "$AIRTABLE_BASE_ID" \
  --peloton-table-id "$PELOTON_TABLE_ID" \
  --rides-table-id "$PELOTON_RIDES_TABLE_ID" \
  --type-table-id "$PELOTON_TYPE_TABLE_ID" \
  "$@"
