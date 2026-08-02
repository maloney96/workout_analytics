-- The crosswalk is hand-maintained, so it goes stale the moment a new exercise is
-- logged in the app. An unmapped template would not error - it would drop out of every
-- canonical_exercise join and silently shrink the analysis, which is the failure mode
-- that is hardest to notice. Fail the build instead.

select
    e.exercise_template_id,
    max(e.exercise_title) as exercise_title,
    count(distinct e.workout_id) as sessions
from {{ ref('stg_hevy__workout_exercises') }} as e
left join {{ ref('exercise_crosswalk') }} as x
    on e.exercise_template_id = x.exercise_template_id
where x.exercise_template_id is null
group by e.exercise_template_id
