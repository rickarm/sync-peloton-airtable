# Setting Up Your Own Peloton → Airtable Sync

This guide is for someone setting up the sync against **their own** Peloton
account and **their own** Airtable base — not Rick's original setup (for that,
see [README.md](README.md) → "Setup (New Machine)").

> **Current limitation (being worked on):** the importer currently writes to
> Airtable by *field ID*, and field IDs are unique per base — even an exact
> copy of the original base gets new ones. Until the importer is switched to
> field *names*, a new base won't accept imports out of the box. The config
> steps below all work today; the final import step is what's gated. Track
> progress in the repo issues.

## What you need

- A Mac with Python 3 (`python3 --version` to check) and the `requests`
  package (`pip3 install requests`)
- A [Peloton](https://members.onepeloton.com) account
- A free [Airtable](https://airtable.com) account

## 1. Get the Airtable base

Copy the **Peloton Tracker template base** into your Airtable workspace (ask
the repo owner for the current template share link, or build it by hand from
the [schema appendix](#appendix-required-airtable-schema) below).

The base must contain the four tables in the appendix with those exact table
and field names. Two of them come pre-seeded in the template:

- **Peloton_Instructor** — one row per instructor name. The importer *matches*
  instructor names from your CSV against this table; it never creates new
  rows. If a workout shows a "no Airtable match for instructor" warning, add
  that instructor's name here and re-run (re-running is always safe).
- **Peloton_type** — class-type names (Power Zone, Climb, Intervals, …) used
  by the matcher's Power Zone hint.

## 2. Create an Airtable token

1. Go to https://airtable.com/create/tokens
2. Create a token with scopes **`data.records:read`** and
   **`data.records:write`**, granted access to your copy of the base
3. Put it in `~/.env` (your home directory, **not** inside this repo):

```bash
AIRTABLE_TOKEN=pat_your_token_here
```

## 3. Clone the repo

```bash
git clone https://github.com/rickarm/sync-peloton-airtable.git
cd sync-peloton-airtable
```

## 4. Create your config file

Copy the repo's config to your home directory and edit the copy — never the
repo file. The home copy always wins:

```bash
cp peloton-sync.conf ~/.peloton-sync.conf
open -e ~/.peloton-sync.conf   # or edit with any text editor
```

Set every value for **your** account and base:

| Key | Where to find it |
|---|---|
| `PELOTON_USERNAME` | Your Peloton **leaderboard name**. Your workout CSV downloads as `<username>_workouts*.csv` — the tools use this to find the file. |
| `AIRTABLE_BASE_ID` | Open your base in a browser. The URL looks like `https://airtable.com/appXXXXXXXXXXXXXX/tblYYYY.../viw...` — the part starting with `app` is the base ID. |
| `PELOTON_TABLE_ID` | Click the **Peloton** table tab, copy the URL part starting with `tbl`. |
| `PELOTON_RIDES_TABLE_ID` | Same, from the **Peloton-Rides** table. |
| `PELOTON_TYPE_TABLE_ID` | Same, from the **Peloton_type** table. |
| `PELOTON_INSTRUCTOR_TABLE_ID` | Same, from the **Peloton_Instructor** table. |

## 5. Run your first sync

1. Download your workout CSV: [members.onepeloton.com](https://members.onepeloton.com)
   → Profile → Workout History → **Download Workouts** → it lands in
   `~/Downloads/<username>_workouts*.csv`
2. Preview without writing anything:

```bash
./peloton-sync.sh --dry-run
```

3. If the summary looks right (row counts match your workout history), run it
   for real:

```bash
./peloton-sync.sh
```

Re-running is always safe. `Workout_timestamp` is the merge key, and the
default run is **incremental** — it creates only the workouts not already in
Airtable and leaves existing rows untouched, so the same CSV twice produces
zero duplicates. Adding `--full` switches to the legacy upsert, which also
rewrites every existing row from the CSV; you want that only for a repair or
a backfill. See [README.md](README.md) for the full comparison.

> **`Warning: workout ID lookup failed ... continuing without
> Peloton_Workout_ID`** on your first run is expected and harmless. Filling
> that field needs the sibling
> [peloton-workout-extract](https://github.com/rickarm/peloton-workout-extract)
> repo, which is optional. The import completes normally without it; pass
> `--no-workout-ids` to skip the lookup and silence the warning.

## 6. Class matching (Power Zone data)

After each import, `peloton-sync.sh` automatically runs the matcher, which
links workouts to rows in **Peloton-Rides** so each workout inherits the
class's per-zone time breakdown (`TimeInZone1_min` … `TimeInZone7_min`).

The matcher can only link classes that exist in Peloton-Rides. In a fresh
base that table starts empty, so nothing links until class rows are added
(currently a manual/scraper step — automatic backfill from Peloton's API is
planned). You can run the matcher on its own any time:

```bash
./peloton-match.sh --dry-run   # report only
./peloton-match.sh             # link + lock
```

---

## Appendix: Required Airtable schema

Table and field names below are the contract the scripts depend on — they must
match exactly. "(auto)" fields are created by Airtable automatically when the
linked field on the other table is created.

### Table `Peloton` (workout history — one row per workout)

| Field | Type | Notes |
|---|---|---|
| `Workout_timestamp` | Single line text | **Primary. Merge key** — written by importer |
| `Live_OnDemand` | Single select: `On Demand`, `Live` | written by importer |
| `InstructorName` | Link → `Peloton_Instructor` | written by importer |
| `Length` | Number (1 dp) | minutes — written by importer |
| `FitnessDiscipline` | Single select: `Cycling`, `Stretching`, `Yoga`, `Strength`, `Meditation`, `Cardio` | written by importer |
| `Type` | Link → `Peloton_type` | manual tag; matcher reads it for the PZ hint |
| `Title` | Single line text | written by importer |
| `ClassTimestampString` | Single line text | class air time — written by importer, matcher's match key |
| `TotalOutput` | Number (0 dp) | written by importer |
| `AvgWatts` | Number (0 dp) | written by importer |
| `AvgResistance` | Percent (0 dp) | written by importer (stored as decimal, 45% → 0.45) |
| `AvgCadence` | Number (0 dp) | written by importer |
| `AvgSpeed` | Number (2 dp) | written by importer |
| `Distance` | Number (2 dp) | written by importer |
| `CaloriesBurned` | Number (0 dp) | written by importer |
| `AvgHeartrate` | Number (0 dp) | written by importer |
| `ClassTimestampDate` | Formula: `DATETIME_PARSE({ClassTimestampString},'YYYY-MM-DD hh:mm')` → date (ISO) | matcher fallback date |
| `LinkedRide` | Link → `Peloton-Rides` | written by matcher |
| `MatchLock` | Checkbox | written by matcher |
| `MatchScore` | Number (0 dp) | written by matcher |
| `TimeInZone1_min` … `TimeInZone7_min` | Lookup via `LinkedRide` → same-named ride field | the Power Zone payoff |
| `TotalTimeInZones_min` | Formula: sum of the seven zone lookups | |
| `PowerZone-Type` | Lookup via `LinkedRide` → `PowerZoneType` | |
| `Z2/Z3` | Lookup via `LinkedRide` → ride `Z2/Z3` | |
| `Z5+` | Lookup via `LinkedRide` → ride `Z5+` | |
| `Peloton_Workout_ID` | Single line text | Peloton's own workout ID — written by importer |
| `FTP-at-Time` | Number (0 dp) | manual — your FTP when the ride was taken |
| `OutputPerMinute` | Formula: `{TotalOutput}/{Length}` (1 dp) | |

### Table `Peloton-Rides` (class metadata — one row per class)

| Field | Type | Notes |
|---|---|---|
| `ClassTimestamp` | Single line text | **Primary.** class air time, e.g. `2026-04-21 21:00 (-07)` |
| `RideTitle` | Single line text | |
| `Instructor` | Link → `Peloton_Instructor` | |
| `RideDuration_min` | Number (0 dp) | |
| `PowerZoneType` | Single select: `PZE`, `PZ Max`, `Threshold`, `Recovery`, `Other` | |
| `TimeInZone1_min` … `TimeInZone7_min` | Duration (h:mm:ss) | per-zone planned time |
| `ClassID` | Single line text | Peloton class ID |
| `ClassURL` | URL | |
| `ClassTimeDate` | Formula: `DATETIME_PARSE({ClassTimestamp}, 'YYYY-MM-DD HH:mm (ZZ)')` → date | |
| `Z2/Z3` | Formula: `SUM({TimeInZone2_min},{TimeInZone3_min})` | |
| `Z5+` | Formula: `SUM({TimeInZone5_min}, {TimeInZone6_min}, {TimeInZone7_min})` | |
| `Peloton` | Link (auto) | reverse of `LinkedRide` |

### Table `Peloton_Instructor` (lookup — one row per instructor)

| Field | Type | Notes |
|---|---|---|
| `Name` | Single line text | **Primary.** Must match Peloton CSV instructor names |

Ships pre-seeded in the template; add rows when the importer warns about an
unmatched instructor.

### Table `Peloton_type` (lookup — one row per class type)

| Field | Type | Notes |
|---|---|---|
| `Name` | Single line text | **Primary.** e.g. `Power Zone`, `Climb`, `Intervals`, `FTP Test` |
