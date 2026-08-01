-- A workout cannot end before it starts, and a multi-day duration means the
-- timestamps were misparsed rather than that the session really lasted that long.

select workout_id, started_at_utc, ended_at_utc, duration_min
from {{ ref('stg_hevy__workouts') }}
where duration_min < 0
   or duration_min > 480
