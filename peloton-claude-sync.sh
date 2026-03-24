#!/bin/bash
# Peloton workout sync — watches Downloads for new CSV, copies to health-data, syncs to Airtable
# Usage:
#   peloton-sync.sh           # quick mode: last 7 days only
#   peloton-sync.sh --full    # full mode: all rows in CSV

DOWNLOADS_DIR="/Users/rick/Downloads"
HEALTH_DATA_DIR="/Users/rick/Library/Mobile Documents/com~apple~CloudDocs/kb/health-data"
LOG_FILE="/Users/rick/scripts/logs/peloton-sync.log"
STATE_FILE="/Users/rick/scripts/logs/peloton-sync-state"

mkdir -p "$(dirname "$LOG_FILE")"

# Logging helpers
log()         { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"; }
log_success() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] SUCCESS — $*" | tee -a "$LOG_FILE"; }
log_fail()    { echo "[$(date '+%Y-%m-%d %H:%M:%S')] FAILED — $*" | tee -a "$LOG_FILE" >&2; }

MODE="quick"
[[ "$1" == "--full" ]] && MODE="full"
log "--- Starting peloton sync ($MODE mode) ---"

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

# ── Step 4: Build mode instruction ───────────────────────────────────────────
if [[ "$MODE" == "full" ]]; then
    MODE_INSTRUCTION="Do a FULL HISTORICAL update — process ALL rows in the CSV, regardless of date."
else
    MODE_INSTRUCTION="Do a QUICK update — only process rows where 'Workout Timestamp' is within the past 7 days. Skip any rows older than 7 days before doing any Airtable operations."
fi

# ── Step 5: Run Claude sync ───────────────────────────────────────────────────
# Write the prompt to a temp file to avoid bash quote-parsing issues with
# heredocs nested inside "$(…)" — single quotes and special chars in the
# prompt text or in expanded variables (e.g. CSV paths with spaces/parens)
# confuse the bash parser when using that pattern.
PROMPT_FILE=$(mktemp)
TMPOUT=$(mktemp)

cat > "$PROMPT_FILE" <<PROMPT
Sync my Peloton workouts from CSV to Airtable.

MODE: $MODE_INSTRUCTION

CSV FILE: $NEWEST_CSV

---
## Instructions

### Step 0 — CSV scope
$MODE_INSTRUCTION

When in quick mode: parse the CSV, identify today's date, and discard any rows where "Workout Timestamp" is more than 7 days ago. Only consider the remaining rows for the steps below.

### Step 1 — Parse the CSV
Read the CSV at the path above. Map columns to Airtable fields:

| CSV Column | Airtable Field | Notes |
|---|---|---|
| Workout Timestamp | Workout_timestamp | Dedup key |
| Live/On-Demand | Live_OnDemand | Single select |
| Length (minutes) | Length | Numeric |
| Fitness Discipline | FitnessDiscipline | Single select |
| Title | Title | Text |
| Class Timestamp | ClassTimestampString | Text |
| Total Output | TotalOutput | Number |
| Avg. Watts | AvgWatts | Number |
| Avg. Resistance | AvgResistance | Divide by 100 (e.g. 43% -> 0.43) |
| Avg. Cadence (RPM) | AvgCadence | Number |
| Avg. Speed (mph) | AvgSpeed | Number |
| Distance (mi) | Distance | Number |
| Calories Burned | CaloriesBurned | Number |
| Avg. Heartrate | AvgHeartrate | Number |
| Avg. Incline | AvgIncline | Text |

Do NOT write: Instructor Name, Type, Avg. Pace (min/mi), PK_WorkoutTimestamp, AvgPace, OutputPerMinute, Calculation, TotalTimeInZones_min, Timestamp, Weeknum, Weeknum-lookup-string, L_Weeks, FTP-at-Time.

### Step 2 — Deduplicate
Query Airtable base appBmQA2p3z2Fdofa, table tblBuzhfztfwgE59f for all existing Workout_timestamp values. Skip any CSV row whose timestamp already exists. Only insert new rows.

### Step 3 — Handle blank/empty values
If a CSV cell is blank or 0 for a metric field (watts, HR, resistance, etc.), omit that field from the insert entirely. Always write Length, FitnessDiscipline, Workout_timestamp, and Title if present.

### Step 4 — PowerZone-Type field
Infer from Title:
- Contains "Power Zone Endurance" -> PZE
- Contains "Power Zone Max" -> PZ Max
- Contains "Power Zone" but not "Endurance" or "Max" -> PZ
- No Power Zone in title -> Non-PZ

### Step 5 — Insert
Use the Airtable MCP Server tools for all writes. Insert in small batches (10-20 rows at a time). Surface any failures immediately.

### Step 6 — Report
When done, report:
- Total rows in CSV (after mode filtering)
- Rows already in Airtable (skipped)
- Rows inserted
- Any failures and why
PROMPT

CLAUDE_ARGS=(
  --allowedTools "Read,Bash,mcp__claude_ai_Airtable__list_records_for_table,mcp__claude_ai_Airtable__create_records_for_table,mcp__claude_ai_Airtable__list_tables_for_base,mcp__claude_ai_Airtable__get_table_schema"
)

# Retry up to 3 times on rate limit, with exponential backoff (15s, 30s, 60s)
MAX_ATTEMPTS=3
ATTEMPT=0
CLAUDE_EXIT=1
while [[ $ATTEMPT -lt $MAX_ATTEMPTS ]]; do
    ATTEMPT=$(( ATTEMPT + 1 ))
    [[ $ATTEMPT -gt 1 ]] && log "Retry attempt $ATTEMPT of $MAX_ATTEMPTS..."

    /Users/rick/.local/bin/claude -p "$(cat "$PROMPT_FILE")" "${CLAUDE_ARGS[@]}" \
        2>&1 | tee -a "$LOG_FILE" | tee "$TMPOUT"

    CLAUDE_EXIT=${PIPESTATUS[0]}
    CLAUDE_OUTPUT=$(cat "$TMPOUT")

    # Check for rate limit in output; if not present, don't retry
    if echo "$CLAUDE_OUTPUT" | grep -qi "rate limit"; then
        WAIT=$(( 15 * ATTEMPT ))
        log "Rate limit hit — waiting ${WAIT}s before retry..."
        sleep "$WAIT"
    else
        break
    fi
done

rm -f "$PROMPT_FILE" "$TMPOUT"

# ── Step 6: Log result ────────────────────────────────────────────────────────
if [[ $CLAUDE_EXIT -eq 0 ]] && ! echo "$CLAUDE_OUTPUT" | grep -qi "rate limit"; then
    log_success "CSV: $NEWEST_CSV"
    echo "$CSV_MTIME" > "$STATE_FILE"
else
    log_fail "Claude exited with code $CLAUDE_EXIT. Last output: $(echo "$CLAUDE_OUTPUT" | tail -5)"
    exit $CLAUDE_EXIT
fi
