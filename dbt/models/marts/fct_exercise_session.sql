{{ config(materialized='table') }}

with rest_intervals as (
    select * from {{ ref('int_exercise_rest_intervals') }}
),

trailing_max as (
    select
        *,
        -- 90-day trailing max for relative performance
        max(working_e1rm_lb) over (
            partition by canonical_exercise
            order by workout_date_local
            range between interval '90' day preceding and interval '1' day preceding
        ) as trailing_90d_max_e1rm_lb,
        
        max(working_e1rm_kg) over (
            partition by canonical_exercise
            order by workout_date_local
            range between interval '90' day preceding and interval '1' day preceding
        ) as trailing_90d_max_e1rm_kg
        
    from rest_intervals
)

select
    workout_id,
    workout_date_local,
    workout_hour_local,
    training_era,
    canonical_exercise,
    movement_pattern,
    
    working_e1rm_lb,
    working_e1rm_kg,
    
    trailing_90d_max_e1rm_lb,
    trailing_90d_max_e1rm_kg,
    
    -- The core relative performance metric
    case 
        when trailing_90d_max_e1rm_lb > 0 
        then working_e1rm_lb / trailing_90d_max_e1rm_lb 
        else null 
    end as relative_performance,
    
    -- Flags if the current session was a PR (compared to trailing 90d)
    case 
        when trailing_90d_max_e1rm_lb is null then true
        when working_e1rm_lb > trailing_90d_max_e1rm_lb then true
        else false
    end as is_pr,
    
    weight_lb,
    weight_kg,
    reps,
    
    days_since_last_exposure,
    days_since_last_workout,
    
    has_intensity_technique,
    supports_load_metrics,
    has_estimated_load

from trailing_max
