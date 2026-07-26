# Data modeling options — Hevy workout analytics

Decisions to make *before* writing bronze/silver/gold models. Grounded in the actual Hevy public
API v1 schema (verified against the published OpenAPI spec, July 2026).

**Scope.** Three things this project is for:

1. Basic strength progression over time.
2. Does performance depend on how much **rest** preceded the session?
3. Does performance depend on **time of day**?

Body composition tracking is explicitly out of scope — no body measurement ingest, no
bodyweight-load modeling. See [§3.2](#32-bodyweight-exercises-without-body-weight) for how
bodyweight exercises are handled instead.

**Training style: HIT** — roughly one working set per exercise, taken to or near failure. This
simplifies the model considerably and, as it happens, makes the correlation questions *more*
answerable than they'd be for a high-volume program. See [§3.0](#30-what-hit-changes).

---

## 1. What the source actually gives you

Six read endpoints worth ingesting, all `GET`, all authenticated with an `api-key` header:

| Endpoint | Grain | Notes |
|---|---|---|
| `/v1/workouts` | workout, nested exercises → sets | paginated, newest first |
| `/v1/workouts/events?since=` | change event | `updated` (full workout) or `deleted` (id + timestamp) |
| `/v1/workouts/count` | scalar | cheap reconciliation check |
| `/v1/exercise_templates` | exercise template | includes `is_custom`, muscle groups |
| `/v1/routines` | routine, nested exercises → sets | the *prescribed* plan |
| `/v1/routine_folders` | folder | groups routines into programs |

Skipped: `/v1/body_measurements` (out of scope), `/v1/exercise_history/{id}` (redundant with
workouts), `/v1/user/info` (single row, no analytical value).

The nesting is three levels deep and that is the whole story:

```
Workout (id, title, routine_id, start_time, end_time, created_at, updated_at)
└── Exercise (index, title, notes, exercise_template_id, supersets_id)
    └── Set (index, type, weight_kg, reps, distance_meters,
              duration_seconds, rpe, custom_metric)
```

Facts that drive decisions later:

- **All units are SI** — kilograms, meters, seconds — regardless of app display. Store SI,
  convert at the presentation layer only.
- **All timestamps are UTC** (`2021-09-14T12:00:00Z`). `start_time` and `end_time` are the only
  temporal signals; sets carry no timestamps.
- **`set.type`** is one of `normal`, `warmup`, `dropset`, `failure` — a free, reliable working-set
  filter. You don't have to infer it.
- **`exercise_template.type`** is one of eight values: `weight_reps`, `reps_only`,
  `bodyweight_reps`, `bodyweight_assisted_reps`, `duration`, `weight_duration`,
  `distance_duration`, `short_distance_weight`.
- **Default `pageSize` is 5** on every list endpoint, and the spec publishes **no maximum and no
  rate limit**. A full backfill is likely hundreds of sequential calls; it needs throttling and
  resumability. Probe the real ceiling before designing the backfill.

### The one thing the API does not give you

**Actual rest between sets is not recoverable.** `rest_seconds` appears *only* on routine
exercises — it is the prescribed rest timer, not a measurement — and logged sets carry no
timestamps at all. You have `start_time` and `end_time` for the session and nothing in between.

This is a non-issue for HIT: with one working set per exercise there is no meaningful inter-set
rest to measure in the first place. So "rest" here means recovery *between sessions*:

- **Days since you last trained this specific exercise** ← the meaningful one.
- **Days since the previous session** (any training), as a secondary cut.

Design around days-since-last-exposure. It is both the version the data can answer and the
version that actually matters for your programming.

---

## 2. Three candidate paths

### Path A — Single set-grain star schema
One atomic fact (`fct_set`) plus conformed dimensions. Everything is a `GROUP BY` over it.

- **For:** one grain, no double-counting, no drilling dead-ends, minimal surface area.
- **Against:** your two analytical questions both need per-exercise-per-session normalization
  with window functions over trailing history. Writing that ad hoc against `fct_set` every time
  is where the mistakes will live.

### Path B — Set-grain spine + an analysis-ready performance fact *(recommended)*
Path A, plus one deliberately built table at **(workout × canonical exercise)** grain carrying
the normalized performance measure, the rest interval, and the time-of-day attributes
side by side. Plus a session-grain `fct_workout` rollup.

- **For:** questions 2 and 3 collapse into a single-table `GROUP BY` against a model whose
  correctness you test once. The set grain stays authoritative underneath.
- **Against:** one more model to keep consistent. Cheap to test (session totals must equal the
  sum of their sets).

### Path C — Activity/event stream
Everything as `(entity, timestamp, event_type, payload)`. Flexible, but you pay for that
flexibility on every read, and this grain is stable and well-known. Wrong tool here.

### Path D — One Big Table
Wide denormalized set-grain table. Not a competing architecture — a legitimate gold *serving*
output for a BI tool. Build it *from* Path B, not instead of it.

**Recommendation: Path B.** The set is the atomic grain — do not make the workout the fact table,
because you can always roll sets up and you can never drill workouts down. But the rest and
time-of-day questions are really about *per-exercise session performance*, so that deserves its
own tested model rather than being re-derived in every query.

---

## 3. Decisions to make before writing gold models

### 3.0 What HIT changes

Training one working set per exercise to failure has three consequences worth designing around,
and two of them are advantages:

**The analysis grain collapses.** With one working set per exercise per session, the
(workout × canonical exercise) grain *is* the working set. There is no "best set of the session"
aggregation to argue about — `fct_exercise_session` is close to a filtered projection of
`fct_set`. Keep both tables anyway (the atomic grain stays authoritative, and warmups and the
occasional second set still live in `fct_set`), but the intermediate logic gets much simpler.

**Effort is held constant, which is a real gift.** The single biggest confounder in this kind of
analysis is variable effort — a set at RPE 7 and a set at RPE 10 aren't comparable, and self-
reported RPE is sparse and unreliable. Taking every working set to failure removes that variable
by construction. When you see performance vary by rest interval or time of day, it's much more
plausibly the thing you're measuring, because intent didn't vary. Most people asking these
questions can't say that.

**Statistical power gets worse, though.** HIT produces far fewer logged sets than a volume
program — possibly one observation per exercise per week. That makes [§6](#6-before-you-trust-the-answers)
more important, not less: expect to pool across `movement_pattern` and to wait for a fair amount
of history before any per-exercise result means anything.

One modeling detail: HIT intensity techniques (drop sets, rest-pause) show up as extra rows with
`set.type = 'dropset'` attached to the same exercise. Treat those as **part of the same working
effort**, not as separate exposures — otherwise your set counts and any "sets per session" metric
will misrepresent what you did. Flag them (`has_intensity_technique`) rather than counting them.

### 3.1 The performance metric — the crux of both questions

You cannot correlate rest or time of day against raw volume. In HIT especially, volume load is
close to meaningless — it's one set, so `weight × reps` mostly reflects what the exercise *is*
rather than how well it went. And comparing absolute numbers across exercises is worse still: a
140 kg deadlift and a 20 kg lateral raise aren't on the same scale.

**Recommend** a two-step normalization at (workout × canonical exercise) grain:

1. **`working_e1rm`** — estimated 1RM of the working set. This collapses load and reps onto one
   comparable axis, which matters because HIT progression is usually "same weight, more reps"
   until a threshold, then a load jump. Reps alone would show sawtooth discontinuities at every
   load increase; e1RM smooths straight through them.
2. **`relative_performance`** — `working_e1rm` divided by the trailing max for that exercise over
   a rolling window (90 days is a reasonable default). Every exercise lands on a unit-free scale
   where ~1.0 means "at my recent best" and 0.9 means "10% off."

Group `relative_performance` by `days_since_last_exposure` or by `hour_local` and you have your
answer, with exercise scale differences and long-run progression already controlled for.

Carry **`reps_at_load`** alongside it. It's the metric you actually train against day to day, and
it's directly interpretable in a way e1RM isn't — useful as a sanity check when e1RM says
something surprising.

Sub-decisions to lock down now:

- **e1RM formula**: Epley (`w × (1 + reps/30)`) or Brzycki. Pick one. Both degrade badly above
  ~12 reps, which is a live concern if your HIT sets run long — consider estimating only within
  1–12 reps and flagging the rest rather than trusting a bad number.
- **Working set filter**: `set.type IN ('normal','failure')`, excluding `warmup`; fold `dropset`
  rows into the parent effort per §3.0 rather than treating them as working sets.
- **Rolling baseline**: trailing max is simple and interpretable, but ratchets upward and never
  recovers after a peak. A trailing median or 90th percentile is more robust if you deload.
  Start with trailing max; revisit if the series looks dominated by one outlier day.

Put each of these in `macros/` so each definition has exactly one home.

### 3.2 Bodyweight exercises without body weight

For `bodyweight_reps` (pull-ups, dips) `weight_kg` is 0 or null, so volume load and e1RM are
zero or meaningless. With body measurements out of scope, two options:

1. **A single `assumed_bodyweight_kg` dbt var.** Effective load becomes
   `assumed_bodyweight_kg + weight_kg` for bodyweight movements, minus for assisted.
2. **Exclude bodyweight movements from load-based metrics**, and track them by reps and working
   sets only.

**Recommend option 1.** A fixed constant is not just simpler than tracking real body weight —
for *progression* analysis it is arguably better, because it isolates performance change instead
of confounding it with weight change. Set it once, document that it's nominal, and never treat
the absolute number as meaningful. Add a `has_estimated_load` flag on those rows so you can
always exclude them from anything where the assumption would mislead.

### 3.3 Exercise identity — a seed, not code

`exercise_template_id` is stable, but the *same movement* fragments across templates: stock
"Bench Press (Barbell)" versus a custom "Bench Press", or a template you renamed. Progression
analysis silently breaks across the split, and so does `days_since_last_exposure`.

**Recommend** a dbt seed `exercise_crosswalk.csv`, maintained by hand:

```
exercise_template_id, canonical_exercise, movement_pattern, is_unilateral, is_compound
```

This is the highest-leverage thing to build early, because both headline questions key off
`canonical_exercise`. `movement_pattern` (horizontal push, vertical pull, hinge, squat…) gives
you a coarser grouping when a single exercise doesn't have enough sessions to say anything.
`is_unilateral` matters because a set of single-arm rows is half the work of a bilateral set —
pick a convention and apply it consistently.

Seeds are the right home: this is judgment, it changes rarely, and it belongs in version control
where you can see it change.

### 3.4 Local time is the whole point of question 3

A 6pm Pacific workout lands on the *next* UTC day in winter. Grouping by the raw timestamp gets
both the date and the hour wrong, which breaks question 3 outright and quietly skews question 2.

**Recommend** deriving in silver, from an explicit `local_tz` dbt var:

- `workout_date_local` — join key to `dim_date`
- `workout_hour_local` — integer 0–23, the grouping key for question 3
- `part_of_day` — coarse bucket (early morning / morning / midday / afternoon / evening), since
  24 hourly buckets will be too sparse to read
- `day_of_week` — needed as a control, see [§6](#6-before-you-trust-the-answers)

Keep the UTC instant alongside for ordering. Note `start_time` is when the workout was *recorded*
as starting — if you habitually start the timer late, the hour is slightly off but consistently
so, which is fine for correlation.

### 3.5 Rest intervals are a window function in silver

`days_since_last_exposure` needs computing per canonical exercise, ordered by local date:

```sql
date_diff('day',
  lag(workout_date_local) over (
    partition by canonical_exercise order by workout_date_local),
  workout_date_local) as days_since_last_exposure
```

Also carry `days_since_last_workout` (unpartitioned) for the "any training" version. Bucket
these for reporting (1, 2, 3, 4–6, 7+) rather than treating a raw day count as continuous —
you will have very few observations in the long tail, and a bucketed view makes that obvious
instead of hiding it behind a regression line.

### 3.6 Set identity is not durable — a real pipeline consequence

`exercise.index` and `set.index` are positional, and there is no stable per-set ID. Edit a workout
in the app — insert a set, reorder an exercise — and they renumber.

**Consequence:** never merge `fct_set` on `(workout_id, exercise_index, set_index)`. A set-level
incremental merge will silently corrupt history on every edit. Make the *workout* the unit of
incrementality: delete all rows for a changed `workout_id` and reinsert. In dbt,
`incremental_strategy='delete+insert'` with `unique_key='workout_id'`. Generate a surrogate set
key for joins, but treat it as valid only within a load.

### 3.7 CDC and soft deletes shape bronze

`/v1/workouts/events?since=` is a genuine change feed including deletes — so you can detect a
workout removed in the app, which a naive full-refresh pull cannot.

- **Bronze** — append-only. One row per API response with `ingested_at`, `endpoint`, `page`, and
  the raw JSON untouched. Never update it; it's your replay log, and at this volume there is no
  reason to prune.
- **Silver** — deduplicate to one row per `workout_id` on latest `updated_at`; carry `is_deleted`
  from delete events rather than physically removing rows.
- Full backfill once via `/v1/workouts`, then incremental via `/v1/workouts/events` with a
  high-water mark. Reconcile against `/v1/workouts/count` on a schedule to catch drift.

### 3.8 Deliberately deferred

- **Muscle-group bridge table.** `secondary_muscle_groups` is an array, so exercise→muscle is
  many-to-many and joining it naively triple-counts a bench press set. Doing it right needs a
  bridge with weighting factors. None of your three questions need it — use single-valued
  `primary_muscle_group` as a plain dimension attribute for now, which gets most of the value at
  zero complexity. Add the bridge only if you later want volume balance across a program.
- **Planned vs actual adherence.** `workout.routine_id` links a session to its prescribed routine,
  making adherence modelable, but it's fiddly (substitutions, skips). One caveat if you think you
  might ever want it: routines represent *current* state only, so dbt snapshots would need to
  start running now. That history is not recoverable later.

---

## 4. Proposed model DAG

```
bronze/                       raw JSON, append-only, partitioned by ingest date
  hevy_workouts_raw
  hevy_workout_events_raw
  hevy_exercise_templates_raw
  hevy_routines_raw
  hevy_routine_folders_raw

silver/  (dbt staging + intermediate)
  stg_hevy__workouts             workout grain, deduped, soft-delete flagged, local time
  stg_hevy__workout_exercises    unnested level 2
  stg_hevy__workout_sets         unnested level 3 — atomic
  stg_hevy__exercise_templates
  stg_hevy__routines, stg_hevy__routine_folders
  int_sets_enriched              + working-set flag, effective load, e1RM, dropset folding
  int_exercise_sessions          (workout × canonical exercise): working_e1rm, reps_at_load
  int_exercise_rest_intervals    + days_since_last_exposure via lag()

gold/
  dim_date
  dim_exercise                   templates + crosswalk seed + primary_muscle_group
  dim_routine, dim_routine_folder
  fct_set                        ATOMIC — one row per logged set
  fct_workout                    session rollup: duration, exercises, working sets, hour_local
  fct_exercise_session           ★ the analysis table — one row per exercise per session:
                                   working_e1rm, reps_at_load, relative_performance,
                                   days_since_last_exposure, days_since_last_workout,
                                   hour_local, part_of_day, day_of_week, is_pr
  obt_sets                       wide denormalized serving table for BI
```

Seeds: `exercise_crosswalk.csv`.

`fct_exercise_session` is the payoff — both headline questions are one `GROUP BY` against it.

---

## 5. Suggested build order

1. Bronze ingest + full backfill, with resumable pagination and rate-limit backoff.
2. `stg_*` unnesting down to `stg_hevy__workout_sets`. Reconcile set counts against the app.
3. `dim_exercise` + `exercise_crosswalk` seed. Do this early — everything downstream keys off it.
4. `fct_set` with `delete+insert` on `workout_id`, then `fct_workout` rolled up from it, tested
   so session totals equal the sum of their sets.
5. `int_exercise_sessions` + e1RM macros → per-exercise progression over time. **This alone
   satisfies goal 1**; ship it and look at it before going further.
6. `relative_performance` normalization + `days_since_last_exposure` → `fct_exercise_session`.
   Goals 2 and 3 both fall out of this model.
7. Switch ingest to the `/events` CDC feed; keep a periodic full reconcile.

Steps 1–5 are the project. Step 6 is a handful of window functions on top, so the sequencing risk
is low — but resist building it first, because the crosswalk seed in step 3 determines whether
step 6 produces anything meaningful.

---

## 6. Before you trust the answers

Both correlation questions are asking a lot of a single person's training log. Worth designing
the analysis knowing this, rather than discovering it after the fact:

- **Time of day is confounded with day of week.** Weekend morning sessions versus weekday evening
  sessions differ in sleep, food, and stress, not just clock time. Always break out
  `day_of_week` alongside `hour_local`; if your morning sessions are all Saturdays, the model is
  measuring Saturdays.
- **Rest days are confounded with everything that causes them.** Long gaps come from illness,
  travel, and deloads — all of which independently affect performance. A dip after 10 days off
  may be the illness, not the layoff. Consider flagging known-disrupted periods manually.
- **Rest is also confounded with program phase.** If your split changed, exposure frequency
  changed with it, and the comparison spans two different programs.
- **Sample size is your binding constraint.** HIT gives you roughly one observation per exercise
  per session, so a year of training might be 40–50 data points for your best-covered lift and
  far fewer for the rest. Require a minimum (say 10 sessions) before reporting anything
  per-exercise, pool up to `movement_pattern` when you're under it, and show the bucket count
  next to every result so an n=2 cell is visibly n=2. Expect the honest answer for the first few
  months to be "not enough data yet" — build the model so it says that clearly rather than
  drawing a confident line through three points.
- **Trend contaminates both.** You get stronger over time. If rest intervals or training times
  drifted across your history, that trend will masquerade as an effect — which is precisely what
  `relative_performance` against a *trailing* baseline is there to absorb.

The one confounder you *don't* have is variable effort, since every working set goes to failure
(§3.0). That's a genuine advantage over how this analysis usually goes — but it doesn't rescue
you from the ones above.

Treat the output as descriptive and hypothesis-generating. It is enough to notice a pattern worth
testing deliberately — and the natural next step is to test it on purpose, by deliberately varying
rest or training time for one lift rather than waiting for your log to happen to vary.

---

## 7. Open questions

- What is the real `pageSize` ceiling and rate limit? The spec documents neither.
- e1RM formula and the rep-range cap above which you refuse to estimate. Matters more than usual
  if HIT sets regularly run past 12 reps.
- Rolling baseline window and statistic (90-day trailing max, or something more robust).
- Unilateral convention: double the reps, or halve the volume?
- Nominal value for `assumed_bodyweight_kg`.
- Do you log intensity techniques as `dropset` rows consistently? If the logging is inconsistent,
  §3.0's dropset folding needs to be lenient rather than exact.
