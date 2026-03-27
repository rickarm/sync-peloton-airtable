#!/bin/bash
# Peloton workout sync — watches Downloads for new CSV, copies to health-data, syncs to Airtable
# Usage:
#   peloton-sync.sh           # sync (default)
#   peloton-sync.sh --dry-run # preview without writing to Airtable
#   peloton-sync.sh --full    # accepted but ignored (always syncs full CSV; upsert is safe)

DOWNLOADS_DIR="/Users/rick/Downloads"
HEALTH_DATA_DIR="/Users/rick/Library/Mobile Documents/com~apple~CloudDocs/kb/health-data"
LOG_FILE="/Users/rick/scripts/logs/peloton-sync.log"
STATE_FILE="/Users/rick/scripts/logs/peloton-sync-state"
PYTHON="/Users/rick/Dev/sleep-airtable/.venv/bin/python3"
IMPORT_SCRIPT="/Users/rick/Dev/sync-peloton-airtable/Peloton_Airtable_Import.py"
BASE_ID="appBmQA2p3z2Fdofa"
TABLE_ID="tblBuzhfztfwgE59f"

mkdir -p "$(dirname "$LOG_FILE")"

# Load credentials
[ -f "$HOME/.env" ] && source "$HOME/.env"

# Logging helpers
log()         { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"; }
log_success() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] SUCCESS — $*" | tee -a "$LOG_FILE"; }
log_fail()    { echo "[$(date '+%Y-%m-%d %H:%M:%S')] FAILED — $*" | tee -a "$LOG_FILE" >&2; }

DRY_RUN=""
[[ "$1" == "--dry-run" ]] && DRY_RUN="--dry-run"

log "--- Starting peloton sync ---"

# ── Step 1: Copy new Peloton CSVs from Downloads to health-data ──────────────
NEW_CSV_COPIED=""
while IFS= read -r -d '' src_file; do
    filename=$(basename "$src_file")
    dest_file="$HEALTH_DATA_DIR/$filename"
    if [[ ! -f "$dest_file" ]]; then
        cp "$src_file" "$dest_file"
        log "Copied $filename from Downloads → health-data"
        NEW_CSV_COPIED="$dest_file"
    fi
done < <(find "$DOWNLOADS_DIR" -maxdepth 1 -name "Big__Cheese_workouts*.csv" -print0 2>/dev/null)

# ── Step 2: Find newest CSV in health-data ────────────────────────────────────
NEWEST_CSV=$(ls -t "$HEALTH_DATA_DIR"/*.csv 2>/dev/null | head -1)
if [[ -z "$NEWEST_CSV" ]]; then
    log_fail "No CSV found in $HEALTH_DATA_DIR"
    exit 1
fi

# ── Step 3: Skip if nothing has changed since last sync ───────────────────────
CSV_MTIME=$(stat -f "%m" "$NEWEST_CSV" 2>/dev/null || echo "0")
LAST_MTIME=$(cat "$STATE_FILE" 2>/dev/null || echo "0")
if [[ "$CSV_MTIME" == "$LAST_MTIME" ]] && [[ -z "$NEW_CSV_COPIED" ]]; then
    log "No changes detected since last sync (CSV unchanged). Skipping."
    exit 0
fi

log "Using CSV: $NEWEST_CSV"

# ── Step 4: Validate prerequisites ───────────────────────────────────────────
if [[ ! -f "$PYTHON" ]]; then
    log_fail "Python not found at $PYTHON"
    exit 1
fi
if [[ ! -f "$IMPORT_SCRIPT" ]]; then
    log_fail "Import script not found at $IMPORT_SCRIPT"
    exit 1
fi
if [[ -z "${AIRTABLE_TOKEN:-}" ]] && [[ -z "$DRY_RUN" ]]; then
    log_fail "AIRTABLE_TOKEN not set. Add it to ~/.env"
    exit 1
fi

# ── Step 5: Run Python sync ───────────────────────────────────────────────────
TMPOUT=$(mktemp)

"$PYTHON" "$IMPORT_SCRIPT" \
    --base-id "$BASE_ID" \
    --table-id "$TABLE_ID" \
    --csv "$NEWEST_CSV" \
    --token "${AIRTABLE_TOKEN:-}" \
    ${DRY_RUN:+"$DRY_RUN"} \
    2>&1 | tee -a "$LOG_FILE" | tee "$TMPOUT"

PYTHON_EXIT=${PIPESTATUS[0]}
PYTHON_OUTPUT=$(cat "$TMPOUT")
rm -f "$TMPOUT"

# ── Step 6: Log result ────────────────────────────────────────────────────────
if [[ $PYTHON_EXIT -eq 0 ]]; then
    log_success "CSV: $NEWEST_CSV"
    [[ -z "$DRY_RUN" ]] && echo "$CSV_MTIME" > "$STATE_FILE"
else
    log_fail "Import script exited with code $PYTHON_EXIT. Last output: $(echo "$PYTHON_OUTPUT" | tail -3)"
    exit $PYTHON_EXIT
fi
