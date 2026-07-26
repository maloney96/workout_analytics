#!/usr/bin/env python3
"""Bronze ingest entry point. Called by both the Airflow DAG and GitHub Actions.

    python ingest/run_ingest.py                 # incremental (CDC for workouts)
    python ingest/run_ingest.py --full-refresh  # re-pull all workouts from page 1
    python ingest/run_ingest.py --datasets workouts exercise_templates

Workouts arrive one of two ways. The first run walks /v1/workouts end to end;
later runs ask /v1/workouts/events for everything changed since the last high
water mark, which is the only way to learn about workouts deleted in the app.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ingest import bronze, config  # noqa: E402
from ingest.hevy_client import HevyClient  # noqa: E402

log = logging.getLogger("ingest")

EPOCH = "1970-01-01T00:00:00Z"
STATIC_DATASETS = ("exercise_templates", "routines", "routine_folders")


def _bump(timestamp: str) -> str:
    """Nudge a high-water mark forward by 1ms.

    /v1/workouts/events treats `since` as inclusive, so passing back the exact
    timestamp of the newest event returns that same event on every subsequent run.
    """
    parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    return (parsed + timedelta(milliseconds=1)).isoformat().replace("+00:00", "Z")


def ingest_static(client: HevyClient, con, dataset: str, run: str) -> int:
    """Small reference datasets, re-pulled in full but landed only when changed."""
    records = bronze.dedupe(dataset, client.fetch_all(bronze.DATASETS[dataset]))
    fresh = bronze.changed_only(con, dataset, records)
    if fresh:
        bronze.land(dataset, fresh, run=run)
        bronze.materialize(con, dataset)
    return len(fresh)


def ingest_workouts_full(client: HevyClient, con, run: str) -> int:
    expected = client.workout_count()
    log.info("full backfill: account reports %d workouts", expected)

    records = bronze.dedupe("workouts", client.fetch_all("/v1/workouts"))
    bronze.land("workouts", records, run=run)
    bronze.materialize(con, "workouts")

    if len(records) != expected:
        log.warning("fetched %d workouts but count endpoint said %d", len(records), expected)

    latest = max((r.get("updated_at") or r.get("start_time") for r in records), default=EPOCH)
    bronze.set_high_water(con, "workouts", _bump(latest), run=run)
    return len(records)


def ingest_workout_events(client: HevyClient, con, run: str, since: str) -> int:
    log.info("incremental: fetching workout events since %s", since)
    events = client.fetch_all("/v1/workouts/events", since=since)

    if not events:
        log.info("no changes since last run")
        return 0

    bronze.land("workout_events", events, run=run)
    bronze.materialize(con, "workout_events")

    updated = [e for e in events if e.get("type") == "updated"]
    deleted = [e for e in events if e.get("type") == "deleted"]
    log.info("%d updated, %d deleted", len(updated), len(deleted))

    # Updated events carry the full workout, so they also refresh the workouts dataset.
    if updated:
        workouts = bronze.dedupe("workouts", [e["workout"] for e in updated])
        bronze.land("workouts", workouts, run=run)
        bronze.materialize(con, "workouts")

    stamps = [
        e["workout"]["updated_at"] if e.get("type") == "updated" else e.get("deleted_at")
        for e in events
    ]
    bronze.set_high_water(con, "workouts", _bump(max(s for s in stamps if s)), run=run)
    return len(events)


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest Hevy data into the bronze layer")
    parser.add_argument(
        "--full-refresh",
        action="store_true",
        help="re-pull every workout instead of using the change feed",
    )
    parser.add_argument(
        "--datasets",
        nargs="+",
        choices=[*bronze.DATASETS, "all"],
        default=["all"],
        help="limit the run to specific datasets",
    )
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )

    wanted = set(bronze.DATASETS) if "all" in args.datasets else set(args.datasets)
    started = datetime.now(timezone.utc)
    run = bronze.run_id()

    client = HevyClient()
    con = bronze.connect()
    bronze.init_state(con)
    counts: dict[str, int] = {}

    try:
        for dataset in STATIC_DATASETS:
            if dataset in wanted:
                counts[dataset] = ingest_static(client, con, dataset, run)

        if "workouts" in wanted or "workout_events" in wanted:
            high_water = bronze.get_high_water(con, "workouts")
            if args.full_refresh or high_water is None:
                counts["workouts"] = ingest_workouts_full(client, con, run)
            else:
                counts["workout_events"] = ingest_workout_events(client, con, run, high_water)
    finally:
        con.close()

    elapsed = (datetime.now(timezone.utc) - started).total_seconds()
    log.info("run %s finished in %.1fs", run, elapsed)
    for dataset, n in counts.items():
        log.info("  %-20s %d records", dataset, n)
    log.info("warehouse: %s", config.duckdb_path())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
