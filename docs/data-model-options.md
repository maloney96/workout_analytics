# Data modeling options — Hevy workout analytics

Decisions to make *before* writing bronze/silver/gold models. Everything below is grounded in
the actual Hevy public API v1 schema (verified against the published OpenAPI spec, July 2026).

---

## 1. What the source actually gives you

Nine read endpoints, all `GET`, all authenticated with an `api-key` header:

| Endpoint | Grain | Notes |
|---|---|---|
| `/v1/workouts` | workout, nested exercises → sets | paginated, newest first |
| `/v1/workouts/events?since=` | change event | `updated` (full workout) or `deleted` (id + timestamp) |
| `/v1/workouts/count` | scalar | cheap reconciliation check |
| `/v1/exercise_templates` | exercise template | includes `is_custom` |
| `/v1/routines` | routine, nested exercises → sets | the *prescribed* plan |
| `/v1/routine_folders` | folder | groups routines into programs |
| `/v1/body_measurements` | one row per date | weight, body fat, 15 circumference sites |
| `/v1/exercise_history/{id}` | set, flattened | convenience view; redundant with workouts |
| `/v1/user/info` | scalar | id, name, profile URL |

The nesting is three levels deep and that is the whole story:

```
Workout (id, title, routine_id, start_time, end_time, created_at, updated_at)
└── Exercise (index, title, notes, exercise_template_id, supersets_id)
    └── Set (index, type, weight_kg, reps, distance_meters,
              duration_seconds, rpe, custom_metric)
```

Facts worth internalizing now, because they drive several decisions later:

- **All units are SI from the API** — kilograms, meters, seconds — regardless of what the app
  displays. Store SI, convert at the presentation layer only.
- **All timestamps are UTC** (`2021-09-14T12:00:00Z`).
- **`set.type`** is one of `normal`, `warmup`, `dropset`, `failure`. This gives you a free,
  reliable "working set" filter — you don't have to infer it.
- **`exercise_template.type`** is one of eight values: `weight_reps`, `reps_only`,
  `bodyweight_reps`, `bodyweight_assisted_reps`, `duration`, `weight_duration`,
  `distance_duration`, `short_distance_weight`. This determines which set measures are
  populated and which are null.
- **`primary_muscle_group`** is a single enum value; **`secondary_muscle_groups`** is an
  *array*. Twenty muscle-group values exist.
- **Default `pageSize` is 5** on every list endpoint (10 for body measurements), and the spec
  publishes no maximum. Probe the real ceiling before backfilling; assume a full history
  backfill is hundreds of sequential calls and needs throttling plus resumability.

---

## 2. Three candidate paths

### Path A — Single set-grain star schema
One atomic fact (`fct_set`) plus conformed dimensions. Every session, weekly, or per-muscle
metric is a `GROUP BY` over that one table.

- **For:** one grain, no double-counting risk, no drilling dead-ends, minimal surface area.
- **Against:** every dashboard tile re-aggregates ~50–200k rows. On DuckDB that is milliseconds,
  so this cost is theoretical.

### Path B — Set-grain spine + a small set of derived facts *(recommended)*
Path A, plus deliberately materialized `fct_workout` (session grain), `fct_body_measurement`
(daily periodic snapshot), and a bridge table for muscle attribution.

- **For:** keeps the atomic grain authoritative while making the two queries you will actually
  run every day — "how did this session go" and "weekly volume per muscle" — single-table reads.
  The bridge table is the piece that makes muscle-group analysis correct rather than approximate.
- **Against:** more models to keep consistent. Mitigated by defining shared metric logic once
  as dbt macros and testing session totals against the sum of their sets.

### Path C — Activity/event stream (one narrow event table)
Everything as `(entity, timestamp, event_type, payload)`.

- **For:** trivially handles the CDC feed, schema-flexible.
- **Against:** you pay for that flexibility on every single read, and this dataset's grain is
  stable and well-known. Wrong tool here.

### Path D — One Big Table
Fully denormalized set-grain table with all exercise, routine, and date attributes joined on.

- Not a competing architecture — this is a legitimate **gold serving output** for a BI tool that
  joins poorly. Build it *from* Path B, don't build it *instead of* Path B.

**Recommendation: Path B.** Set is the atomic grain; do not be tempted to make the workout the
fact table, because you can always roll sets up and you can never drill workouts down.

---

## 3. Decisions to make before writing gold models

These are the ones with real consequences. Roughly in order of leverage.

### 3.1 Heterogeneous set measures — one fact or several?

Because of the eight exercise types, a set is sometimes `(weight_kg, reps)`, sometimes
`(distance_meters, duration_seconds)`, sometimes `reps` alone. Options:

1. **One `fct_set`, all measure columns nullable, carrying `exercise_type` as a mode flag.**
2. Split into `fct_set_strength` / `fct_set_cardio`.
3. Long key-value format, one row per (set, measure).

**Recommend option 1.** Splitting breaks the simplest question you'll ask ("what did I do this
week"), and the long format makes every strength query a pivot. Keep one table, carry the type,
and derive a `volume_kg` column that is non-null *only* for types where volume load is
meaningful — then `SUM(volume_kg)` is safe everywhere without a filter.

### 3.2 Bodyweight load — the reason body measurements are not a side quest

For `bodyweight_reps` (pull-ups, dips), `weight_kg` is 0 or null, so volume load is zero. For
`bodyweight_assisted_reps` it is *negative* load. Treating those as zero-volume silently deletes
a large share of real training.

**Recommend** an `int_body_weight_daily` model that forward-fills the most recent
`body_measurements.weight_kg` to every calendar date. That turns an as-of join into a plain
equi-join on date, and lets `fct_set` compute effective load as:

- `weight_reps` → `weight_kg`
- `bodyweight_reps` → `body_weight_kg + weight_kg` (added weight)
- `bodyweight_assisted_reps` → `body_weight_kg − weight_kg` (assistance)

Decide the fallback when body weight is missing (before your first measurement, or gaps):
carry backward from the earliest, or leave null and exclude. Be explicit — this is a silent
accuracy sink otherwise.

### 3.3 Muscle-group attribution — bridge table with weighting factors

`secondary_muscle_groups` is an array, so exercise → muscle is many-to-many. Joining the array
directly to `fct_set` double-counts volume (a bench press set would count fully toward chest,
triceps, *and* shoulders, inflating total volume ~3×).

**Recommend** a classic multi-valued dimension bridge:

```
bridge_exercise_muscle (exercise_template_id, muscle_group, role, weight_factor)
  primary   → 1.0
  secondary → 0.5   ← tune this; it is an analytical choice, not a fact
```

Weighted volume then sums correctly, and you can still report unweighted "sets touching muscle X"
by ignoring the factor. This single table is what makes the interesting question — weekly hard
sets per muscle group against the 10–20 set landmark — answerable rather than hand-wavy.

### 3.4 Exercise identity — a seed, not code

`exercise_template_id` is stable, but the *same movement* fragments across templates: the stock
"Bench Press (Barbell)" versus a custom "Bench Press", or a template you renamed. Progression
analysis breaks across the split.

**Recommend** a dbt seed `exercise_crosswalk.csv` that you maintain by hand:

```
exercise_template_id, canonical_exercise, movement_pattern, is_unilateral, is_compound
```

`movement_pattern` (horizontal push, vertical pull, hinge, squat, carry…) is the grouping you'll
actually want for program balance. `is_unilateral` matters because a "set" of single-arm rows is
half the work of a bilateral set — decide whether to double the reps or halve the volume, and
apply it consistently. Seeds are the right home: this is judgment, it changes rarely, and it
belongs in version control where you can see it change.

### 3.5 Derived metrics — define once, as macros

Each of these is a choice, not a formula handed down:

- **e1RM**: Epley (`w × (1 + reps/30)`) vs Brzycki. Pick one, note that both degrade badly
  above ~12 reps, and consider capping the rep range you compute it over.
- **Hard set**: use `set.type IN ('normal','failure','dropset')`, excluding `warmup`.
- **RPE**: nullable and, in practice, sparsely filled. Decide now whether RPE-derived metrics
  are best-effort or require completeness, and add a dbt test asserting coverage so you know
  when the metric is trustworthy.
- **Volume load**: `effective_weight_kg × reps`. Distinct from tonnage and from relative
  intensity — name them differently in the model so they never get conflated.

Put each in `macros/` so the definition has exactly one home.

### 3.6 Local date, not UTC date

A 6pm Pacific workout lands on the *next* UTC day in winter. Every "workouts per week" number is
wrong at the edges if you group by the raw timestamp.

**Recommend** materializing `workout_date_local` in silver using an explicit `local_tz` dbt var,
and joining `dim_date` on that. Keep the UTC instant alongside it for ordering.

### 3.7 Set identity is not durable — a real pipeline consequence

`exercise.index` and `set.index` are positional. Edit a workout in the app — insert a set, reorder
an exercise — and those indexes renumber. There is no stable per-set ID in the API.

**Consequence:** never merge `fct_set` on `(workout_id, exercise_index, set_index)`. A set-level
incremental merge will silently corrupt history on every edit. Instead make the *workout* the unit
of incrementality: delete all rows for a changed `workout_id` and reinsert. In dbt that is
`incremental_strategy='delete+insert'` with `unique_key='workout_id'`. Generate a surrogate set
key for joins, but treat it as valid only within a load.

### 3.8 CDC and soft deletes shape bronze

`/v1/workouts/events?since=` is a genuine change feed, including deletes — which means you can
detect a workout you removed in the app, something a naive full-refresh pull cannot do.

**Recommend:**
- **Bronze** — append-only. One row per API response payload with `ingested_at`, `endpoint`,
  `page`, and the raw JSON untouched. Never update bronze; it is your replay log, and with this
  volume there is no reason to ever prune it.
- **Silver** — deduplicate to one row per `workout_id` taking the latest `updated_at`, and carry
  `is_deleted` from delete events rather than physically removing rows.
- Full backfill once via `/v1/workouts`, then incremental via `/v1/workouts/events` with a
  high-water mark. Reconcile against `/v1/workouts/count` on a schedule to catch drift.

### 3.9 Planned vs actual — the differentiator, but do it second

`workout.routine_id` links an executed session back to its prescribed routine, and routines carry
their own nested exercises and sets. That makes program adherence — prescribed sets/reps/weight
versus what you actually did — a fact-to-fact comparison you can genuinely model.

It is the most interesting thing in this dataset and also the fiddliest (matching prescribed to
executed exercises when you substitute or skip). **Build the spine first, add this as a second
iteration.** Note that routines represent *current* state — if you want to know what a routine
looked like when you performed it, you must snapshot it (dbt snapshots, SCD2) starting now.
That history is not recoverable later.

---

## 4. Proposed model DAG

```
bronze/                       raw JSON, append-only, partitioned by ingest date
  hevy_workouts_raw
  hevy_workout_events_raw
  hevy_exercise_templates_raw
  hevy_routines_raw
  hevy_routine_folders_raw
  hevy_body_measurements_raw

silver/  (dbt staging + intermediate)
  stg_hevy__workouts             workout grain, deduped, soft-delete flagged
  stg_hevy__workout_exercises    unnested level 2
  stg_hevy__workout_sets         unnested level 3 — atomic
  stg_hevy__exercise_templates
  stg_hevy__routines / __routine_exercises / __routine_sets
  stg_hevy__routine_folders
  stg_hevy__body_measurements
  int_body_weight_daily          forward-filled, for bodyweight load
  int_sets_enriched              + effective load, volume, e1RM, local date

gold/
  dim_date
  dim_exercise                   templates + crosswalk seed + muscle attributes
  dim_routine, dim_routine_folder
  bridge_exercise_muscle         many-to-many with weight_factor
  fct_set                        ATOMIC — one row per logged set
  fct_workout                    session rollup: duration, volume, hard sets, density
  fct_body_measurement           daily periodic snapshot
  fct_exercise_progression       per canonical exercise per date: best set, e1RM, PR flags
  obt_sets                       wide denormalized serving table for BI
```

Seeds: `exercise_crosswalk.csv`, `muscle_group_weights.csv`.

---

## 5. Suggested build order

1. Bronze ingest + full backfill, with resumable pagination and rate-limit backoff.
2. `stg_*` unnesting down to `stg_hevy__workout_sets`. Reconcile set counts against the app.
3. `dim_exercise` + `exercise_crosswalk` seed. Do this early — it dictates everything downstream.
4. `fct_set` with `delete+insert` on `workout_id`, then `fct_workout` rolled up from it, tested
   so session totals equal the sum of their sets.
5. `bridge_exercise_muscle` + weekly volume per muscle group.
6. `int_body_weight_daily` and corrected bodyweight load.
7. Switch ingest to the `/events` CDC feed; keep a periodic full reconcile.
8. Routine snapshots and the planned-vs-actual adherence model.

---

## 6. Open questions

- What is the real `pageSize` ceiling and rate limit? The spec documents neither. Probe before
  designing the backfill DAG.
- Weighting factor for secondary muscles — 0.5 is a starting convention, not a finding.
- Unilateral convention: double the reps, or halve the volume?
- Body weight fallback before the first recorded measurement.
- Are `routine_folder` values stable enough to treat as a program dimension, or is a
  hand-maintained program/mesocycle seed more honest?
