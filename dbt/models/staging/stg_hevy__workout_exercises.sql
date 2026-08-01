{{ config(materialized='view') }}

{#
    One row per exercise within a workout.

    exercise_index is positional and renumbers when a workout is edited in the app,
    so it is safe as a key only within a load. Never merge on it — see
    docs/data-model-options.md section 3.6.

    The field is superset_id, singular. The published OpenAPI spec says
    supersets_id, which is wrong; using the spec's name silently yields all nulls.
#}

with latest as (
    {{ latest_by_record_id('hevy_bronze', 'workouts') }}
),

exploded as (
    select
        cast(record ->> 'id' as varchar) as workout_id,
        unnest(from_json(record -> 'exercises', '["json"]')) as exercise
    from latest
)

select
    workout_id,
    cast(exercise ->> 'index' as integer)                as exercise_index,
    exercise ->> 'title'                                 as exercise_title,
    cast(exercise ->> 'exercise_template_id' as varchar) as exercise_template_id,
    cast(exercise ->> 'superset_id' as integer)          as superset_id,
    exercise ->> 'superset_id' is not null               as is_superset,
    nullif(trim(exercise ->> 'notes'), '')               as notes,
    json_array_length(exercise -> 'sets')                as set_count
from exploded
