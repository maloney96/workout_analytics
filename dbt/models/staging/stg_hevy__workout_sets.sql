{{ config(materialized='view') }}

{#
    The atomic grain: one row per logged set.

    weight_kg as stored carries float noise from a pounds-to-kilograms conversion,
    so both weight columns are canonicalised. Group and join on those, never on the
    raw value.

    set_type is supplied by the API and is reliable, so working sets never have to
    be inferred. Warmups are ~24% of all sets across the full history and ~46% in
    the HIT era, so this filter is doing real work.
#}

with latest as (
    {{ latest_by_record_id('hevy_bronze', 'workouts') }}
),

exercises as (
    select
        cast(record ->> 'id' as varchar) as workout_id,
        unnest(from_json(record -> 'exercises', '["json"]')) as exercise
    from latest
),

sets as (
    select
        workout_id,
        cast(exercise ->> 'index' as integer)                as exercise_index,
        exercise ->> 'title'                                 as exercise_title,
        cast(exercise ->> 'exercise_template_id' as varchar) as exercise_template_id,
        unnest(from_json(exercise -> 'sets', '["json"]'))    as st
    from exercises
)

select
    workout_id,
    exercise_index,
    exercise_title,
    exercise_template_id,
    cast(st ->> 'index' as integer) as set_index,

    -- Valid within a load only; indices renumber when a workout is edited.
    workout_id || '-' || cast(exercise_index as varchar)
                || '-' || cast(st ->> 'index' as varchar) as set_key,

    st ->> 'type'                            as set_type,
    st ->> 'type' <> 'warmup'                as is_working_set,
    st ->> 'type' = 'warmup'                 as is_warmup,
    st ->> 'type' = 'failure'                as is_to_failure,
    st ->> 'type' = 'dropset'                as is_dropset,

    {{ weight_lb("st ->> 'weight_kg'") }}          as weight_lb,
    {{ weight_kg_canonical("st ->> 'weight_kg'") }} as weight_kg,

    cast(st ->> 'reps' as integer)             as reps,
    cast(st ->> 'rpe' as double)               as rpe,
    cast(st ->> 'duration_seconds' as integer) as duration_seconds,
    cast(st ->> 'distance_meters' as double)   as distance_meters,
    st ->> 'custom_metric'                     as custom_metric
from sets
