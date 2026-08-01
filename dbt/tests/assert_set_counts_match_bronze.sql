-- Staging must not lose or duplicate sets while unnesting three levels of JSON.
-- Compares the flattened set count against the count computed directly from the
-- raw payloads. Returns rows (fails) on any discrepancy.

with from_bronze as (
    select sum(bronze_sets) as n
    from (
        select
            sum(json_array_length(exercise -> 'sets')) as bronze_sets
        from (
            select unnest(from_json(record -> 'exercises', '["json"]')) as exercise
            from (
                select record, row_number() over (
                    partition by record_id order by ingested_at desc) as _rn
                from {{ source('hevy_bronze', 'workouts') }}
            ) where _rn = 1
        )
    )
),

from_staging as (
    select count(*) as n from {{ ref('stg_hevy__workout_sets') }}
)

select from_bronze.n as bronze_sets, from_staging.n as staging_sets
from from_bronze, from_staging
where from_bronze.n <> from_staging.n
