{{ config(materialized='table') }}

select
    s.set_key,
    s.workout_id,
    s.workout_date_local,
    s.workout_hour_local,
    s.training_era,
    s.exercise_index,
    s.set_index,
    s.exercise_template_id,
    s.canonical_exercise,
    s.movement_pattern,
    s.supports_load_metrics,
    s.has_estimated_load,
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
    
    -- Workout dimensions
    w.part_of_day,
    w.day_of_week,
    w.day_of_week_number,
    w.is_weekend,
    w.duration_min as workout_duration_min,
    w.actual_exercise_count,
    w.title as workout_title,
    w.routine_id,
    
    -- Session performance & rest metrics
    es.working_e1rm_lb,
    es.trailing_90d_max_e1rm_lb,
    es.relative_performance,
    es.is_pr,
    es.days_since_last_exposure,
    es.days_since_last_workout

from {{ ref('fct_set') }} as s
left join {{ ref('fct_workout') }} as w
    on s.workout_id = w.workout_id
left join {{ ref('fct_exercise_session') }} as es
    on s.workout_id = es.workout_id
    and coalesce(s.canonical_exercise, '') = coalesce(es.canonical_exercise, '')
