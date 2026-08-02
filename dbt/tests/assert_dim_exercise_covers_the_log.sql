-- dim_exercise inner-joins the API template list to the crosswalk, so a logged
-- exercise missing from EITHER side disappears from the dimension without erroring -
-- and then quietly drops out of every fact that joins to it. assert_every_exercise_is_mapped
-- catches the stale-crosswalk case specifically; this catches the rest, including a
-- template the API stops returning after the workout referencing it was already ingested.

select
    e.exercise_template_id,
    max(e.exercise_title) as exercise_title,
    count(distinct e.workout_id) as sessions
from {{ ref('stg_hevy__workout_exercises') }} as e
left join {{ ref('dim_exercise') }} as d
    on e.exercise_template_id = d.exercise_template_id
where d.exercise_template_id is null
group by e.exercise_template_id
