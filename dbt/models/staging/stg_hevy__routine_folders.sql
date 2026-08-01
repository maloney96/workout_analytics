{{ config(materialized='view') }}

with latest as (
    {{ latest_by_record_id('hevy_bronze', 'routine_folders') }}
)

select
    cast(record ->> 'id' as integer) as folder_id,
    record ->> 'title'               as title,
    cast(record ->> 'index' as integer) as folder_index,
    cast(record ->> 'created_at' as timestamptz) as created_at,
    cast(record ->> 'updated_at' as timestamptz) as updated_at,
    ingested_at
from latest
