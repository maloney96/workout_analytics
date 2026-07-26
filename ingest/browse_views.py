"""Readable views over the raw bronze JSON, created in the `main` schema.

These exist purely so the warehouse is browsable — in a DB extension, a notebook,
or `make query`. Bronze stores whole API documents, one JSON blob per row, and
`main` is the schema every tool opens by default and is otherwise empty.

They are a convenience layer, not part of the medallion architecture. The real
staging models will be dbt's, and these should be dropped once those exist.
"""

from __future__ import annotations

import logging

import duckdb

log = logging.getLogger(__name__)

# Hevy divides by this to convert entered pounds into stored kilograms, so
# multiplying back recovers exactly what was typed. See docs/data-model-options.md.
LB_PER_KG = 2.20462

VIEWS: dict[str, str] = {
    "workouts": """
        select
            cast(record ->> 'id' as varchar)          as workout_id,
            record ->> 'title'                        as title,
            cast(record ->> 'start_time' as timestamp) as start_time,
            cast(record ->> 'end_time'   as timestamp) as end_time,
            date_diff('minute',
                cast(record ->> 'start_time' as timestamp),
                cast(record ->> 'end_time'   as timestamp))   as duration_min,
            json_array_length(record -> 'exercises')  as exercise_count,
            cast(record ->> 'routine_id' as varchar)  as routine_id,
            record ->> 'description'                  as description
        from bronze.workouts
    """,
    "exercises": """
        with unnested as (
            select
                cast(record ->> 'id' as varchar)           as workout_id,
                cast(record ->> 'start_time' as timestamp) as start_time,
                unnest(from_json(record -> 'exercises', '["json"]')) as exercise
            from bronze.workouts
        )
        select
            workout_id,
            start_time,
            cast(exercise ->> 'index' as integer)     as exercise_index,
            exercise ->> 'title'                      as exercise,
            cast(exercise ->> 'exercise_template_id' as varchar) as exercise_template_id,
            cast(exercise ->> 'superset_id' as integer) as superset_id,
            json_array_length(exercise -> 'sets')     as set_count,
            exercise ->> 'notes'                      as notes
        from unnested
    """,
    "sets": """
        with ex as (
            select
                cast(record ->> 'id' as varchar)           as workout_id,
                cast(record ->> 'start_time' as timestamp) as start_time,
                unnest(from_json(record -> 'exercises', '["json"]')) as exercise
            from bronze.workouts
        ), s as (
            select
                workout_id, start_time,
                cast(exercise ->> 'index' as integer) as exercise_index,
                exercise ->> 'title'                  as exercise,
                cast(exercise ->> 'exercise_template_id' as varchar) as exercise_template_id,
                unnest(from_json(exercise -> 'sets', '["json"]')) as st
            from ex
        )
        select
            workout_id,
            start_time,
            exercise_index,
            exercise,
            exercise_template_id,
            cast(st ->> 'index' as integer)   as set_index,
            st ->> 'type'                     as set_type,
            st ->> 'type' <> 'warmup'         as is_working_set,
            -- weight_kg carries float noise from a pounds-to-kg conversion, so the
            -- rounded values are what should be grouped or compared on.
            round(cast(st ->> 'weight_kg' as double) * {lb_per_kg}, 1) as weight_lb,
            round(cast(st ->> 'weight_kg' as double), 3)               as weight_kg,
            cast(st ->> 'reps' as integer)    as reps,
            cast(st ->> 'rpe' as double)      as rpe,
            cast(st ->> 'duration_seconds' as integer) as duration_seconds,
            cast(st ->> 'distance_meters' as double)   as distance_meters
        from s
    """,
}


def create(con: duckdb.DuckDBPyConnection) -> None:
    """(Re)create the browse views. Cheap — they are views, not tables."""
    for name, sql in VIEWS.items():
        con.execute(
            f"create or replace view main.{name} as {sql.format(lb_per_kg=LB_PER_KG)}"
        )
    log.info("browse views ready in main: %s", ", ".join(VIEWS))
