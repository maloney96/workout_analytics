-- Guards the timezone bug that came from bronze inferring naive timestamps: if the
-- UTC offset were lost, local time would equal UTC for every row. New York is never
-- UTC, so any workout where they match means the conversion silently did nothing.

select workout_id, started_at_utc, started_at_local
from {{ ref('stg_hevy__workouts') }}
where started_at_local = cast(started_at_utc as timestamp)
