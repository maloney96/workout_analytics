{{ config(materialized='view') }}

{#
    Exercise definitions. secondary_muscle_groups is an array and is left as JSON:
    exercise-to-muscle is many-to-many, and flattening it naively triple-counts a
    bench press set. Only primary_muscle_group is exposed as a scalar for now.
#}

with latest as (
    {{ latest_by_record_id('hevy_bronze', 'exercise_templates') }}
)

select
    cast(record ->> 'id' as varchar)   as exercise_template_id,
    record ->> 'title'                 as exercise_title,
    record ->> 'type'                  as exercise_type,
    record ->> 'primary_muscle_group'  as primary_muscle_group,
    record -> 'secondary_muscle_groups' as secondary_muscle_groups,
    json_array_length(record -> 'secondary_muscle_groups') as secondary_muscle_count,
    record ->> 'equipment'             as equipment,
    cast(record ->> 'is_custom' as boolean) as is_custom,

    {#-
        The published spec's enum is wrong and incomplete. It lists
        bodyweight_reps and bodyweight_assisted_reps, neither of which the API ever
        returns; the real values are bodyweight_weighted and bodyweight_assisted,
        plus floors_duration and steps_duration which the spec omits entirely.
        Checking the spec's names produced a flag that was always false.
    -#}
    record ->> 'type' in ('bodyweight_weighted', 'bodyweight_assisted', 'reps_only')
        as is_bodyweight,

    {#-
        What the weight column actually means, which differs by type and cannot be
        compared across them. Assisted machines report a counterweight that is not a
        1:1 subtraction from bodyweight, so it is labelled rather than converted.
    -#}
    case record ->> 'type'
        when 'reps_only'           then 'none'
        when 'bodyweight_weighted' then 'added_to_bodyweight'
        when 'bodyweight_assisted' then 'assistance_not_convertible'
        else 'external_load'
    end as weight_semantics,

    ingested_at
from latest
