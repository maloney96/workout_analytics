{{ config(materialized='view') }}

{#
    One row per workout. Local time is derived here because almost everything
    downstream needs it and because getting it wrong is silent: an evening Eastern
    workout lands on the following UTC day.

    training_era is joined in at staging rather than left to a mart. It is a pure
    function of the date, every downstream question needs it, and repeating the
    join is how the two eras end up accidentally pooled.
#}

with latest as (
    {{ latest_by_record_id('hevy_bronze', 'workouts') }}
),

renamed as (
    select
        cast(record ->> 'id' as varchar)         as workout_id,
        record ->> 'title'                       as title,
        record ->> 'description'                 as description,
        cast(record ->> 'routine_id' as varchar) as routine_id,

        cast(record ->> 'start_time' as timestamptz) as started_at_utc,
        cast(record ->> 'end_time'   as timestamptz) as ended_at_utc,

        {{ to_local("record ->> 'start_time'") }} as started_at_local,
        {{ to_local("record ->> 'end_time'")   }} as ended_at_local,

        cast(record ->> 'created_at' as timestamptz) as created_at,
        cast(record ->> 'updated_at' as timestamptz) as updated_at,

        json_array_length(record -> 'exercises')     as exercise_count,
        ingested_at
    from latest
),

derived as (
    select
        *,
        cast(started_at_local as date)                as workout_date_local,
        extract(hour from started_at_local)           as workout_hour_local,
        {{ part_of_day('extract(hour from started_at_local)') }} as part_of_day,
        dayname(started_at_local)                     as day_of_week,
        extract(isodow from started_at_local)         as day_of_week_number,
        extract(isodow from started_at_local) >= 6    as is_weekend,
        date_diff('minute', started_at_utc, ended_at_utc) as duration_min
    from renamed
)

select
    d.workout_id,
    d.title,
    d.description,
    d.routine_id,
    d.started_at_utc,
    d.ended_at_utc,
    d.started_at_local,
    d.ended_at_local,
    d.workout_date_local,
    d.workout_hour_local,
    d.part_of_day,
    d.day_of_week,
    d.day_of_week_number,
    d.is_weekend,
    d.duration_min,
    d.exercise_count,
    e.era as training_era,
    d.created_at,
    d.updated_at,
    d.ingested_at
from derived d
left join {{ ref('training_era') }} e
    on d.workout_date_local between e.start_date and e.end_date
