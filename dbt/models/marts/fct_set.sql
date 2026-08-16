{{
    config(
        materialized='incremental',
        unique_key='workout_id',
        incremental_strategy='delete+insert'
    )
}}

with sets as (
    select * from {{ ref('stg_hevy__workout_sets') }}
),

workouts as (
    select * from {{ ref('stg_hevy__workouts') }}
),

dim_exercise as (
    select * from {{ ref('dim_exercise') }}
)

select
    s.set_key,
    s.workout_id,
    w.workout_date_local,
    w.workout_hour_local,
    w.training_era,
    s.exercise_index,
    s.set_index,
    s.exercise_template_id,
    e.canonical_exercise,
    e.movement_pattern,
    e.supports_load_metrics,
    e.has_estimated_load,
    s.set_type,
    s.is_working_set,
    s.is_warmup,
    s.is_to_failure,
    s.is_dropset,
    s.weight_lb,
    s.weight_kg,
    s.reps,
    s.rpe,
    s.duration_seconds,
    s.distance_meters,
    w.updated_at
from sets as s
inner join workouts as w
    on s.workout_id = w.workout_id
left join dim_exercise as e
    on s.exercise_template_id = e.exercise_template_id
where w.is_deleted = false

{% if is_incremental() %}
    and s.workout_id in (
        select workout_id 
        from workouts 
        where updated_at >= (select coalesce(max(updated_at), '1970-01-01'::timestamp) from {{ this }})
    )
{% endif %}
