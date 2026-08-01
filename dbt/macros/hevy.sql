{#
    Shared definitions. Each of these encodes a decision documented in
    docs/data-model-options.md, and each belongs in exactly one place.
#}

{% macro to_local(utc_expr) -%}
    {#- Bronze stores offset-aware UTC strings, so the cast is unambiguous. -#}
    timezone('{{ var("local_tz") }}', cast({{ utc_expr }} as timestamptz))
{%- endmacro %}


{% macro part_of_day(hour_expr) -%}
    {#- 24 hourly buckets are too sparse to read at this sample size. -#}
    case
        when {{ hour_expr }} <  5 then 'night'
        when {{ hour_expr }} < 11 then 'morning'
        when {{ hour_expr }} < 14 then 'midday'
        when {{ hour_expr }} < 18 then 'afternoon'
        else 'evening'
    end
{%- endmacro %}


{% macro weight_lb(kg_expr) -%}
    {#-
        Hevy stores pounds divided by 2.20462, which leaves float noise: 90 lb is
        held as 40.82336184920758 kg. Multiplying back recovers what was typed.
        Group and compare on this, never on raw weight_kg.
    -#}
    round(cast({{ kg_expr }} as double) * 2.20462, 2)
{%- endmacro %}


{% macro weight_kg_canonical(kg_expr) -%}
    {#- Same noise problem; 3dp is enough to separate real increments. -#}
    round(cast({{ kg_expr }} as double), 3)
{%- endmacro %}


{% macro latest_by_record_id(source_name, table_name) -%}
    {#-
        Bronze is append-only and keeps a row per landed version, so every staging
        model starts by taking the most recent version of each record.
    -#}
    select record, ingested_at, record_id
    from (
        select
            record,
            ingested_at,
            record_id,
            row_number() over (
                partition by record_id order by ingested_at desc
            ) as _rn
        from {{ source(source_name, table_name) }}
    )
    where _rn = 1
{%- endmacro %}
