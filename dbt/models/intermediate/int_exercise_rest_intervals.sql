{{ config(materialized='table') }}

with sessions as (
    select * from {{ ref('int_exercise_sessions') }}
),

workouts as (
    select 
        workout_id,
        workout_date_local
    from {{ ref('fct_workout') }}
),

workout_gaps as (
    select
        workout_id,
        workout_date_local,
        date_diff('day',
            lag(workout_date_local) over (order by workout_date_local, workout_id),
            workout_date_local
        ) as days_since_last_workout
    from workouts
),

exercise_gaps as (
    select
        workout_id,
        canonical_exercise,
        date_diff('day',
            lag(workout_date_local) over (
                partition by canonical_exercise 
                order by workout_date_local, workout_id
            ),
            workout_date_local
        ) as days_since_last_exposure
    from sessions
)

select
    s.*,
    e.days_since_last_exposure,
    w.days_since_last_workout
from sessions as s
left join exercise_gaps as e 
    on s.workout_id = e.workout_id 
    and s.canonical_exercise = e.canonical_exercise
left join workout_gaps as w
    on s.workout_id = w.workout_id
