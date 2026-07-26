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

Design phase. The data model is specified but not yet built.

**Start here: [docs/data-model-options.md](docs/data-model-options.md)** — the source schema,
three candidate modeling approaches with a recommendation, the decisions that have to be made
before writing gold models, the proposed DAG, and the confounders to watch when interpreting
results.

## Setup

Requires a Hevy Pro account. Generate an API key at <https://hevy.com/settings?developer>.

```bash
make setup       # copies .env.example to .env
# fill in HEVY_API_KEY
make check       # verifies the key resolves and the API answers
```

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

## Running

| Where | Orchestrator | Entry point |
|---|---|---|
| Local | Airflow (Docker) | `dags/` |
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
  seeds/            exercise crosswalk — hand-maintained
scripts/            check_connection.py, sync_secrets.sh
docs/               design notes
data/               DuckDB file and raw landing zone (gitignored)
```
