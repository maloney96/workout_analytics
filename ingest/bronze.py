"""Bronze layer: land raw API payloads, then materialize them into DuckDB.

The JSONL files on disk are the source of truth and are only ever appended to.
The DuckDB tables are a materialization rebuilt from those files, so bronze can
always be reconstructed by rerunning the load — no state to corrupt.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import duckdb

from ingest import config

log = logging.getLogger(__name__)

# Bronze dataset name -> API endpoint it came from
DATASETS: dict[str, str] = {
    "workouts": "/v1/workouts",
    "workout_events": "/v1/workouts/events",
    "routines": "/v1/routines",
    "routine_folders": "/v1/routine_folders",
    "exercise_templates": "/v1/exercise_templates",
}


def run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def record_id(record: dict[str, Any]) -> str | None:
    """Stable identity of a record. Change events wrap the entity they describe."""
    if "workout" in record and isinstance(record["workout"], dict):
        return str(record["workout"].get("id"))
    value = record.get("id")
    return None if value is None else str(value)


def record_hash(record: dict[str, Any]) -> str:
    canonical = json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.md5(canonical.encode("utf-8")).hexdigest()


def dedupe(dataset: str, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop records repeated within one pass.

    /v1/routines can return the same routine on two different pages — offset
    pagination over a list that is ordered by a non-unique key. Last occurrence
    wins, since pages are walked newest first.
    """
    seen: dict[str, dict[str, Any]] = {}
    unkeyed: list[dict[str, Any]] = []

    for record in records:
        key = record_id(record)
        if key is None:
            unkeyed.append(record)
        else:
            seen[key] = record

    duplicates = len(records) - len(seen) - len(unkeyed)
    if duplicates:
        log.warning("%s: dropped %d duplicate record(s) returned within one pass",
                    dataset, duplicates)
    return [*seen.values(), *unkeyed]


def land(dataset: str, records: Iterable[dict[str, Any]], *, run: str) -> Path:
    """Append records to a run-scoped JSONL file, partitioned by ingest date."""
    ingested_at = datetime.now(timezone.utc)
    partition = config.bronze_path() / dataset / f"ingest_date={ingested_at:%Y-%m-%d}"
    partition.mkdir(parents=True, exist_ok=True)
    path = partition / f"{run}.jsonl"

    written = 0
    with path.open("a", encoding="utf-8") as handle:
        for record in records:
            handle.write(
                json.dumps(
                    {
                        "_ingested_at": ingested_at.isoformat(),
                        "_run_id": run,
                        "_endpoint": DATASETS[dataset],
                        "_record_id": record_id(record),
                        "_record_hash": record_hash(record),
                        "record": record,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
            written += 1

    log.info("landed %d %s records -> %s", written, dataset, path)
    return path


def changed_only(
    con: duckdb.DuckDBPyConnection, dataset: str, records: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Keep only records that are new or whose content changed since last landing.

    Without this, re-pulling reference data on every scheduled run would append an
    identical copy each time. Bronze stays append-only, but it becomes a log of
    changes rather than a log of snapshots.
    """
    table_exists = con.execute(
        "select count(*) from duckdb_tables() where schema_name='bronze' and table_name=?",
        [dataset],
    ).fetchone()[0]
    if not table_exists:
        return records

    known = dict(
        con.execute(
            f"""
            select record_id, record_hash from (
                select record_id, record_hash,
                       row_number() over (partition by record_id order by ingested_at desc) as rn
                from bronze.{dataset}
                where record_id is not null
            ) where rn = 1
            """
        ).fetchall()
    )

    fresh = [r for r in records if known.get(record_id(r)) != record_hash(r)]
    if len(fresh) < len(records):
        log.info("%s: %d unchanged since last run, landing %d",
                 dataset, len(records) - len(fresh), len(fresh))
    return fresh


def connect() -> duckdb.DuckDBPyConnection:
    path = config.duckdb_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path))
    con.execute("create schema if not exists bronze")
    return con


def materialize(con: duckdb.DuckDBPyConnection, dataset: str) -> int:
    """Rebuild bronze.<dataset> from every JSONL file landed for it."""
    glob = str(config.bronze_path() / dataset / "**" / "*.jsonl")
    if not list(config.bronze_path().glob(f"{dataset}/**/*.jsonl")):
        log.warning("no landed files for %s — skipping materialize", dataset)
        return 0

    con.execute(
        f"""
        create or replace table bronze.{dataset} as
        select
            cast(_ingested_at as timestamptz) as ingested_at,
            _run_id                           as run_id,
            _endpoint                         as endpoint,
            -- cast explicitly: DuckDB infers UUID-shaped ids as UUID, which then
            -- compares unequal to the plain strings the ingest computes.
            cast(_record_id as varchar)       as record_id,
            cast(_record_hash as varchar)     as record_hash,
            record
        from read_json(
            ?,
            format          = 'newline_delimited',
            union_by_name   = true,
            maximum_object_size = 33554432
        )
        """,
        [glob],
    )
    rows = con.execute(f"select count(*) from bronze.{dataset}").fetchone()[0]
    log.info("bronze.%s materialized: %d rows", dataset, rows)
    return rows


def init_state(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(
        """
        create table if not exists bronze.ingest_state (
            dataset      varchar primary key,
            high_water   timestamptz,
            last_run_id  varchar,
            last_run_at  timestamptz
        )
        """
    )


def get_high_water(con: duckdb.DuckDBPyConnection, dataset: str) -> str | None:
    init_state(con)
    row = con.execute(
        "select high_water from bronze.ingest_state where dataset = ?", [dataset]
    ).fetchone()
    return row[0].isoformat().replace("+00:00", "Z") if row and row[0] else None


def set_high_water(
    con: duckdb.DuckDBPyConnection, dataset: str, high_water: str, *, run: str
) -> None:
    init_state(con)
    con.execute(
        """
        insert into bronze.ingest_state (dataset, high_water, last_run_id, last_run_at)
        values (?, cast(? as timestamptz), ?, now())
        on conflict (dataset) do update set
            high_water  = excluded.high_water,
            last_run_id = excluded.last_run_id,
            last_run_at = excluded.last_run_at
        """,
        [dataset, high_water, run],
    )
