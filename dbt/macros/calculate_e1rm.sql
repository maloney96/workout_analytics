{% macro calculate_e1rm(weight, reps) %}
    {#
        Brzycki formula: weight * (36 / (37 - reps))
        Valid and capped for reps <= 12. Returns null for >12 reps to avoid skewed estimates.
    #}
    case
        when {{ reps }} > 12 then null
        when {{ reps }} <= 0 then null
        when {{ weight }} is null then null
        else {{ weight }} * (36.0 / (37.0 - {{ reps }}))
    end
{% endmacro %}
