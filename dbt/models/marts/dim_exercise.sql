{#
    One row per exercise template that has actually been logged.

    Inner join, not left: the API exposes 459 templates but only 172 appear in the
    training log, and the 287 that don't can never turn up in a fact. Carrying them
    would mean 287 rows with a null canonical_exercise, which is exactly the shape
    that makes a downstream null look like a mapping bug rather than an unused row.

    The inner join does mean a logged template missing from either side would vanish
    silently, so both sides are guarded:
      - tests/assert_every_exercise_is_mapped.sql   - crosswalk went stale
      - tests/assert_dim_exercise_covers_the_log.sql - anything else drops a row
#}

with templates as (
    select * from {{ ref('stg_hevy__exercise_templates') }}
),

crosswalk as (
    select * from {{ ref('exercise_crosswalk') }}
)

select
    t.exercise_template_id,

    {#- Current title from the API. The crosswalk carries its own source_title as an
        editing aid; this is the one that tracks renames, so read this one. -#}
    t.exercise_title,

    x.canonical_exercise,
    x.movement_pattern,
    x.is_unilateral,
    x.is_compound,

    t.primary_muscle_group,
    t.secondary_muscle_count,
    t.equipment,
    t.exercise_type,
    t.is_custom,
    t.is_bodyweight,
    t.weight_semantics,

    {#-
        Whether a load-based metric (volume load, e1RM) means anything for this movement.

        Keyed off exercise_type, NOT off weight_semantics - external_load is the else
        branch of the staging case statement, so it also swallows Plank, Dead Hang,
        Treadmill, Running, Air Bike, Stair Machine and Walking Lunge (Dumbbell): nine
        templates that record duration, distance or steps and have no load-and-reps pair
        to build a metric from. Testing weight_semantics alone marks all nine true.

        That leaves the two rep-based types. reps_only carries no weight, and assisted
        machines report a counterweight that is not a 1:1 subtraction from bodyweight, so
        neither belongs in a load series either.
    -#}
    t.exercise_type in ('weight_reps', 'bodyweight_weighted') as supports_load_metrics,

    {#-
        Load depends on the nominal assumed_bodyweight_kg var rather than on anything
        recorded, so the absolute number is not meaningful even though the trend is.
        Per §3.2, flag it so it can be excluded wherever the assumption would mislead.
    -#}
    t.weight_semantics = 'added_to_bodyweight' as has_estimated_load

from templates as t
inner join crosswalk as x
    on t.exercise_template_id = x.exercise_template_id
