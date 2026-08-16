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
        count(distinct exercise_index) as total_exercises,
        count(*) as total_sets,
        sum(case when is_working_set then 1 else 0 end) as total_working_sets
    from sets
    group by 1
)

select
    w.workout_id,
    w.title,
    w.routine_id,
    w.workout_date_local,
    w.workout_hour_local as hour_local,
    w.part_of_day,
    w.day_of_week,
    w.day_of_week_number,
    w.is_weekend,
    w.duration_min as session_duration,
    w.training_era,
    coalesce(m.total_exercises, 0) as total_exercises,
    coalesce(m.total_sets, 0) as total_sets,
    coalesce(m.total_working_sets, 0) as total_working_sets,
    w.created_at,
    w.updated_at
from workouts as w
left join session_metrics as m on w.workout_id = m.workout_id
where w.is_deleted = false
