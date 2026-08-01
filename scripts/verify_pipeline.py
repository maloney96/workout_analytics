#!/usr/bin/env python3
"""Cross-check the warehouse against the live API, independently of the SQL.

dbt's tests prove the models are internally consistent — that staging matches
bronze, that keys are unique. They cannot prove the models are faithfully wrong:
a mistaken timezone or unit conversion would be applied consistently everywhere
and every test would still pass.

So this recomputes the same figures a second time, in Python, straight from the
API payloads, using zoneinfo rather than DuckDB for the timezone and plain
arithmetic rather than a macro for the weights. Where the two disagree, one of
them is wrong.

    python scripts/verify_pipeline.py
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from collections import Counter
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ingest import config  # noqa: E402
from ingest.hevy_client import HevyClient  # noqa: E402

LB_PER_KG = 2.20462

passed = 0
failed = 0


def _summarise(value) -> str:
    """Keep large collections readable — these get compared in full, not by eye."""
    if isinstance(value, dict) and len(value) > 8:
        return f"{len(value)} keys, {sum(value.values())} total"
    if isinstance(value, (list, tuple)) and len(value) > 8:
        return f"{len(value)} values, {value[0]}..{value[-1]}"
    return repr(value)


def check(label: str, expected, actual, note: str = "") -> None:
    global passed, failed
    if expected == actual:
        passed += 1
        print(f"  PASS  {label}: {_summarise(actual)}{'  ' + note if note else ''}")
    else:
        failed += 1
        print(f"  FAIL  {label}")
        print(f"        api/python: {_summarise(expected)}")
        print(f"        warehouse : {_summarise(actual)}")
        if isinstance(expected, dict) and isinstance(actual, dict):
            for k in sorted(set(expected) | set(actual), key=str):
                if expected.get(k) != actual.get(k):
                    print(f"        differs at {k!r}: {expected.get(k)} vs {actual.get(k)}")


def connect_readable() -> duckdb.DuckDBPyConnection:
    """Open the warehouse, falling back to a copy if another process holds it.

    DuckDB permits a single process on the file and a read-only connection is not
    exempt, so an attached editor blocks this. Verification only reads, so a copy
    answers the same questions.
    """
    path = config.duckdb_path()
    try:
        return duckdb.connect(str(path), read_only=True)
    except duckdb.IOException:
        # Keep the basename: DuckDB derives the catalog name from the filename, and
        # dbt's views reference that catalog internally, so a renamed copy fails to bind.
        scratch = Path(tempfile.mkdtemp(prefix="warehouse-verify-"))
        copy = scratch / path.name
        shutil.copy2(path, copy)
        print(f"  NOTE  warehouse is locked by another process; reading a copy at {copy}\n")
        return duckdb.connect(str(copy), read_only=True)


def main() -> int:
    con = connect_readable()
    client = HevyClient()
    tz = ZoneInfo(config.local_tz())

    print("=" * 74)
    print("1. Counts, recomputed from the API rather than read from bronze")
    print("=" * 74)

    api_count = client.workout_count()
    wh_count = con.execute("select count(*) from staging.stg_hevy__workouts").fetchone()[0]
    check("workout count vs /v1/workouts/count", api_count, wh_count)

    workouts = client.fetch_all("/v1/workouts")
    py_exercises = sum(len(w["exercises"]) for w in workouts)
    py_sets = sum(len(e["sets"]) for w in workouts for e in w["exercises"])
    py_working = sum(
        1 for w in workouts for e in w["exercises"] for s in e["sets"] if s["type"] != "warmup"
    )

    wh_exercises = con.execute(
        "select count(*) from staging.stg_hevy__workout_exercises"
    ).fetchone()[0]
    wh_sets = con.execute("select count(*) from staging.stg_hevy__workout_sets").fetchone()[0]
    wh_working = con.execute(
        "select count(*) from staging.stg_hevy__workout_sets where is_working_set"
    ).fetchone()[0]

    check("exercise rows", py_exercises, wh_exercises)
    check("set rows", py_sets, wh_sets)
    check("working sets", py_working, wh_working)

    py_types = Counter(s["type"] for w in workouts for e in w["exercises"] for s in e["sets"])
    wh_types = dict(
        con.execute(
            "select set_type, count(*) from staging.stg_hevy__workout_sets group by 1"
        ).fetchall()
    )
    check("set type distribution", dict(py_types), wh_types)

    print()
    print("=" * 74)
    print("2. Timezone, recomputed with zoneinfo instead of DuckDB")
    print("=" * 74)

    py_hours = Counter()
    for w in workouts:
        utc = datetime.fromisoformat(w["start_time"].replace("Z", "+00:00"))
        py_hours[utc.astimezone(tz).hour] += 1

    wh_hours = dict(
        con.execute(
            "select workout_hour_local, count(*) from staging.stg_hevy__workouts group by 1"
        ).fetchall()
    )
    check("local hour histogram", dict(py_hours), wh_hours, f"({config.local_tz()})")

    py_dates = Counter()
    for w in workouts:
        utc = datetime.fromisoformat(w["start_time"].replace("Z", "+00:00"))
        py_dates[utc.astimezone(tz).date()] += 1
    wh_dates = dict(
        con.execute(
            "select workout_date_local, count(*) from staging.stg_hevy__workouts group by 1"
        ).fetchall()
    )
    check("local date histogram", dict(py_dates), wh_dates)

    # A UTC-vs-local date disagreement is the specific failure that would skew
    # every "per day" figure, so count how often the two genuinely differ.
    straddle = sum(
        1
        for w in workouts
        if datetime.fromisoformat(w["start_time"].replace("Z", "+00:00")).date()
        != datetime.fromisoformat(w["start_time"].replace("Z", "+00:00")).astimezone(tz).date()
    )
    print(f"  INFO  workouts whose UTC date differs from local date: {straddle}")

    print()
    print("=" * 74)
    print("3. Weight conversion, recomputed with plain arithmetic")
    print("=" * 74)

    py_weights = sorted(
        {
            round(s["weight_kg"] * LB_PER_KG, 2)
            for w in workouts
            for e in w["exercises"]
            for s in e["sets"]
            if s.get("weight_kg")
        }
    )
    wh_weights = sorted(
        r[0]
        for r in con.execute(
            "select distinct weight_lb from staging.stg_hevy__workout_sets "
            "where weight_lb is not null and weight_lb > 0"
        ).fetchall()
    )
    check("distinct weight_lb values", py_weights, wh_weights, f"({len(py_weights)} values)")

    # If the conversion factor were wrong, values would drift off gym increments.
    # The ones that miss should all be kg-native entries, which land on clean kg
    # instead — anything in neither camp would mean the factor is off.
    # Tolerance note: weight_lb is rounded to 2dp, and that lost precision converts
    # back to a kg error that grows with magnitude (up to ~0.005 kg at 450 lb). A
    # fixed-epsilon comparison rejects genuine kg values at the top of the range.
    def _near(value: float, step: float) -> bool:
        return abs(value / step - round(value / step)) * step < 0.01

    lb_native = [x for x in wh_weights if _near(x, 0.25)]
    rest = [x for x in wh_weights if not _near(x, 0.25)]
    kg_native = [x for x in rest if _near(x / LB_PER_KG, 0.5)]
    unexplained = [x for x in rest if x not in kg_native]

    print(f"  INFO  entered in pounds (0.25 lb boundary): {len(lb_native)}")
    print(f"  INFO  entered in kilograms (0.5 kg boundary): {len(kg_native)}"
          f"  e.g. {[(x, round(x / LB_PER_KG, 1)) for x in kg_native[:4]]}")
    check("every weight is explained by one unit or the other", [], unexplained)

    print()
    print("=" * 74)
    print("4. One workout, end to end")
    print("=" * 74)

    newest = max(workouts, key=lambda w: w["start_time"])
    utc = datetime.fromisoformat(newest["start_time"].replace("Z", "+00:00"))
    local = utc.astimezone(tz)
    print(f"  API   {newest['title']}")
    print(f"        start {newest['start_time']}  ->  local {local:%Y-%m-%d %H:%M} ({local:%A})")
    print(f"        {len(newest['exercises'])} exercises, "
          f"{sum(len(e['sets']) for e in newest['exercises'])} sets")

    row = con.execute(
        """
        select title, started_at_utc, started_at_local, workout_hour_local,
               day_of_week, training_era, exercise_count, duration_min
        from staging.stg_hevy__workouts where workout_id = ?
        """,
        [newest["id"]],
    ).fetchone()
    print(f"  WH    {row[0]}")
    print(f"        utc {row[1]}  ->  local {row[2]} ({row[4]})")
    print(f"        era={row[5]}  exercises={row[6]}  duration={row[7]}min")

    check("title", newest["title"], row[0])
    check("local hour", local.hour, row[3])
    check("exercise count", len(newest["exercises"]), row[6])

    print()
    print("  Working sets as the warehouse sees them:")
    for r in con.execute(
        """
        select exercise_title, set_type, weight_lb, reps, rpe
        from staging.stg_hevy__workout_sets
        where workout_id = ? and is_working_set
        order by exercise_index, set_index
        """,
        [newest["id"]],
    ).fetchall():
        print(f"    {r[0][:38]:<38} {r[1]:<8} {str(r[2] or '-'):>7} lb x {str(r[3] or '-'):<4} rpe {r[4] or '-'}")

    print()
    print("  Same sets straight from the API payload:")
    for e in newest["exercises"]:
        for s in e["sets"]:
            if s["type"] == "warmup":
                continue
            lb = round(s["weight_kg"] * LB_PER_KG, 2) if s.get("weight_kg") else None
            print(f"    {e['title'][:38]:<38} {s['type']:<8} {str(lb or '-'):>7} lb x "
                  f"{str(s['reps'] or '-'):<4} rpe {s['rpe'] or '-'}")

    print()
    print("=" * 74)
    print(f"{passed} passed, {failed} failed")
    print("=" * 74)
    con.close()
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
