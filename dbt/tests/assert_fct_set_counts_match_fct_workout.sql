-- Ensure that the total_sets in fct_workout matches the number of rows in fct_set for each workout

with fct_set_counts as (
    select
        workout_id,
        count(*) as set_count,
        sum(case when is_working_set then 1 else 0 end) as working_set_count
    from {{ ref('fct_set') }}
    group by 1
),

fct_workout as (
    select * from {{ ref('fct_workout') }}
)

select
    w.workout_id,
    w.total_sets as expected_total_sets,
    s.set_count as actual_total_sets,
    w.working_sets as expected_working_sets,
    s.working_set_count as actual_working_sets
from fct_workout w
left join fct_set_counts s on w.workout_id = s.workout_id
where w.total_sets != coalesce(s.set_count, 0)
   or w.working_sets != coalesce(s.working_set_count, 0)
