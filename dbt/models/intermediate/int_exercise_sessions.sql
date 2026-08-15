{{ config(materialized='table') }}

with sets as (
    select * from {{ ref('fct_set') }}
),

-- Filter out warmups, but keep dropsets to flag them
working_sets as (
    select
        *,
        case 
            when not is_dropset then {{ calculate_e1rm('weight_lb', 'reps') }}
            else null
        end as set_e1rm_lb,
        case 
            when not is_dropset then {{ calculate_e1rm('weight_kg', 'reps') }}
            else null
        end as set_e1rm_kg
    from sets
    where is_working_set = true
),

exercise_sessions as (
    select
        workout_id,
        workout_date_local,
        workout_hour_local,
        training_era,
        canonical_exercise,
        movement_pattern,
        
        -- The primary load performance metric for the session
        max(set_e1rm_lb) as working_e1rm_lb,
        max(set_e1rm_kg) as working_e1rm_kg,
        
        -- The raw load used (helpful for context alongside e1RM)
        max(case when not is_dropset then weight_lb else null end) as weight_lb,
        max(case when not is_dropset then weight_kg else null end) as weight_kg,
        max(case when not is_dropset then reps else null end) as reps,
        
        -- Intensity technique flag
        bool_or(is_dropset) as has_intensity_technique,
        
        -- Flags from dimension
        bool_or(supports_load_metrics) as supports_load_metrics,
        bool_or(has_estimated_load) as has_estimated_load
        
    from working_sets
    where canonical_exercise is not null
    group by 
        workout_id,
        workout_date_local,
        workout_hour_local,
        training_era,
        canonical_exercise,
        movement_pattern
)

select * from exercise_sessions
