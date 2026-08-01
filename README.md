# workout-analytics

A local, self-hosted data pipeline over my [Hevy](https://hevy.com) training log.

Pulls workout history from the Hevy public API, lands it as raw JSON, and models it into a
medallion (bronze/silver/gold) warehouse.

Three questions it's built to answer:

1. **Progression** — how are my lifts trending over time?
2. **Recovery** — do I perform better after more or fewer rest days?
3. **Timing** — is workout time of day correlated with performance?

Training style is HIT (roughly one working set per exercise, taken to failure), which shapes the
model: the analysis grain is one working set per exercise per session, and effort is effectively
constant across observations. Body composition tracking is out of scope.

## Stack

| Layer | Tool |
|---|---|
| Orchestration | Apache Airflow |
| Transformation | dbt |
| Warehouse | DuckDB |
| Runtime | Docker Compose, local hardware |

## Status

Bronze and staging are built and tested. 384 workouts / 9,570 sets spanning 2024-07 to 2026-07,
refreshed incrementally off the API change feed. Next: the exercise crosswalk seed, then
`fct_set` / `fct_workout`, then per-exercise progression.

**Start here: [docs/data-model-options.md](docs/data-model-options.md)** — the source schema,
three candidate modeling approaches with a recommendation, the decisions that have to be made
before writing gold models, the proposed DAG, and the confounders to watch when interpreting
results.

## Setup

Requires a Hevy Pro account. Generate an API key at <https://hevy.com/settings?developer>.

```bash
make setup       # builds .venv (Python 3.12) and copies .env.example to .env
# fill in HEVY_API_KEY
make check       # verifies the key resolves and the API answers
```

The venv is built from `requirements.lock.txt` on Python 3.12, matching what the workflow pins,
so local and CI resolve identical versions. Airflow is deliberately excluded — it comes from the
Docker image. To add or bump a package, edit `requirements.txt`, run `make lock`, and commit the
refreshed lockfile.

## The Hevy API is read-only, and that is enforced

Hevy is the system of record for this training log; the warehouse is derived and disposable. The
pipeline must never write back.

The API key cannot be scoped — the same credential that reads workouts can also create and
overwrite them through 8 write endpoints, including `POST /v1/workouts` and
`PUT /v1/workouts/{id}`. So the guarantee is enforced in code rather than left to convention:

- [`ReadOnlySession`](ingest/hevy_client.py) subclasses `requests.Session` and raises
  `ReadOnlyViolation` on any method other than GET/HEAD/OPTIONS. Because `.post()`, `.put()`,
  `.patch()` and `.delete()` all funnel through `request()`, every write path is blocked — before
  a request is built, so nothing reaches the network.
- [`tests/test_read_only.py`](tests/test_read_only.py) proves it, and additionally walks the AST
  of every module in `ingest/` and `scripts/` to fail the build on a bare `requests.post(...)`
  that would bypass the guarded session entirely.
- CI runs those tests on every push.

```bash
make test      # unit tests, no API key needed
```

What this does *not* cover: anything run outside this codebase with the same key — `curl`, the
Hevy app itself, or a future script that builds its own HTTP client. The AST check catches the
last case only for files under `ingest/` and `scripts/`.

## Configuration and secrets

One key, two places it can live, no code that knows the difference.

[ingest/config.py](ingest/config.py) resolves config with **real environment variables taking
precedence over `.env`**. Locally, values come from the gitignored `.env`. In GitHub Actions,
the same names arrive as env vars from repository secrets. Nothing downstream branches on which.

To push local values up:

```bash
make sync-secrets          # reads .env, sets GitHub secrets via gh
make secrets-status        # gh secret list
./scripts/sync_secrets.sh --dry-run
```

Only `HEVY_API_KEY`, `LOCAL_TZ`, and `ASSUMED_BODYWEIGHT_KG` are synced — paths stay local, since
a runner's filesystem isn't yours. CI enforces that anything in that sync list also appears in
`.env.example`, so the two can't drift apart silently.

## Browsing the warehouse

From the terminal, no extra tooling needed:

```bash
make query                                          # list bronze tables
make query Q="select * from bronze.workouts"
python scripts/query.py "select 1" --csv            # pipe elsewhere
```

In VS Code, the [DBCode](https://marketplace.visualstudio.com/items?itemName=dbcode.dbcode)
extension is preconfigured in `.vscode/settings.json` with two connections.

**Where the data is.** The catalog is `warehouse`. Raw API payloads live in the `bronze` schema
as one JSON document per row — accurate, but unreadable in a table view. Read from `staging`:

| Model | Grain |
|---|---|
| `staging.stg_hevy__workouts` | one row per session, with local time, part of day, and training era |
| `staging.stg_hevy__workout_exercises` | one row per exercise within a session |
| `staging.stg_hevy__workout_sets` | atomic — one row per set, with `weight_lb`, `reps`, `rpe`, `is_working_set` |
| `staging.stg_hevy__exercise_templates` | exercise definitions, muscle group, `weight_semantics` |
| `staging.training_era` | seeded methodology date ranges |

Start with `select * from staging.stg_hevy__workout_sets`.

**DuckDB allows only one process to hold the database file — and a read-only connection still
blocks the writer.** So an editor connected to `warehouse.duckdb` will make `make ingest` and
`dbt build` fail with `Could not set lock on file`. Two ways around it:

- Use the **live** connection and disconnect (DBCode: right-click → Disconnect) before running
  the pipeline. Always current, but you have to remember.
- Use the **snapshot** connection and run `make snapshot` to refresh it. Never blocks anything;
  shows data as of the last snapshot.

## Running

```bash
make pipeline    # ingest, then dbt build + tests
make ingest      # bronze only
make dbt         # transform only
make verify      # cross-check the warehouse against the live API
make dbt-docs    # browse the model graph
```

## How it's verified

Three layers, because they catch different failures:

| Layer | Command | Catches |
|---|---|---|
| Unit tests (23) | `make test` | dedupe, change detection, read-only enforcement |
| dbt tests (46) | `make dbt-test` | uniqueness, referential integrity, set-count parity with bronze |
| API cross-check (12) | `make verify` | conversions that are wrong *consistently* |

The third matters most. dbt can only prove staging agrees with bronze — if a timezone
or unit conversion were wrong, it would be wrong everywhere and every test would still
pass. So [scripts/verify_pipeline.py](scripts/verify_pipeline.py) recomputes counts,
local times and weights a second time in Python, straight from the API, using
`zoneinfo` and plain arithmetic rather than the SQL. Where the two disagree, one is wrong.

| Where | Orchestrator | Entry point |
|---|---|---|
| Local | `make`, Airflow later | [Makefile](Makefile), `dags/` |
| Remote | GitHub Actions | [.github/workflows/pipeline.yml](.github/workflows/pipeline.yml) |

Both call the same `ingest/` scripts and the same dbt project — Airflow is not a dependency of the
pipeline itself, only a scheduler for it. That's why `requirements.txt` omits Airflow.

The remote workflow runs daily and on manual dispatch (with a `full_refresh` toggle). Because
runners are ephemeral, it carries `data/` between runs via the Actions cache and publishes the
DuckDB file as a build artifact. [ci.yml](.github/workflows/ci.yml) runs on every push and needs
no secrets, so it stays safe on pull requests.

## Layout

```
.github/workflows/  ci.yml (no secrets) and pipeline.yml (scheduled run)
dags/               Airflow DAGs — ingest and dbt orchestration
ingest/             Hevy API client, pagination, CDC via /workouts/events
  config.py         single config resolution for local and CI
dbt/                dbt project: staging → intermediate → marts
  models/staging/   unnests bronze JSON to workout / exercise / set grain
  macros/hevy.sql   local time, pounds canonicalisation, bronze dedupe
  seeds/            training_era; exercise crosswalk to come
  tests/            singular tests, incl. set-count parity against bronze
scripts/            check_connection.py, sync_secrets.sh
docs/               design notes
data/               DuckDB file and raw landing zone (gitignored)
```
