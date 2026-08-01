{{ config(materialized='view') }}

{#
    Routines are current state only — the API retains no history, so a routine
    edited today looks as though it was always that way. Snapshotting would have to
    start now to capture change going forward.

    /v1/routines also returns duplicate records within a single pass, which the
    ingest drops; the dedupe here is the second line of defence.
#}

with latest as (
    {{ latest_by_record_id('hevy_bronze', 'routines') }}
)

select
    cast(record ->> 'id' as varchar)        as routine_id,
    record ->> 'title'                      as title,
    cast(record ->> 'folder_id' as integer) as folder_id,
    nullif(trim(record ->> 'notes'), '')    as notes,
    json_array_length(record -> 'exercises') as exercise_count,
    cast(record ->> 'created_at' as timestamptz) as created_at,
    cast(record ->> 'updated_at' as timestamptz) as updated_at,
    ingested_at
from latest
