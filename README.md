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
cp .env.example .env   # then fill in HEVY_API_KEY
```

## Layout

```
dags/            Airflow DAGs — ingest and dbt orchestration
ingest/          Hevy API client, pagination, CDC via /workouts/events
dbt/             dbt project: staging → intermediate → marts
  seeds/         exercise crosswalk, muscle weighting — hand-maintained
docs/            design notes
data/            DuckDB file and raw landing zone (gitignored)
```
