{{ config(materialized='table') }}

with workouts as (
    select * from {{ ref('stg_hevy__workouts') }}
),

sets as (
    select * from {{ ref('fct_set') }}
),

session_metrics as (
    select
        workout_id,
        count(distinct exercise_index) as actual_exercise_count,
        count(*) as total_sets,
        sum(case when is_working_set then 1 else 0 end) as working_sets
    from sets
    group by 1
)

select
    w.workout_id,
    w.title,
    w.routine_id,
    w.workout_date_local,
    w.workout_hour_local,
    w.part_of_day,
    w.day_of_week,
    w.day_of_week_number,
    w.is_weekend,
    w.duration_min,
    w.training_era,
    m.actual_exercise_count,
    m.total_sets,
    m.working_sets,
    w.created_at,
    w.updated_at
from workouts as w
left join session_metrics as m on w.workout_id = m.workout_id
where w.is_deleted = false
