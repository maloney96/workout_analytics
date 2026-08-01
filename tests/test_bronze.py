"""Bronze identity, dedupe and change-detection.

These paths only misbehave when the API repeats a record or a workout is edited in
the app — neither of which happens on demand, so they would otherwise go untested
until they silently corrupted history.

Run with: python -m unittest discover -s tests
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from ingest import bronze  # noqa: E402


def workout(wid: str, title: str = "Push", updated: str = "2026-07-01T10:00:00Z") -> dict:
    return {"id": wid, "title": title, "updated_at": updated, "exercises": []}


class RecordIdentityTests(unittest.TestCase):
    def test_id_is_read_from_the_record(self) -> None:
        self.assertEqual(bronze.record_id(workout("abc")), "abc")

    def test_change_events_report_the_wrapped_workout_id(self) -> None:
        event = {"type": "updated", "workout": workout("abc")}
        self.assertEqual(bronze.record_id(event), "abc")

    def test_numeric_ids_become_strings(self) -> None:
        self.assertEqual(bronze.record_id({"id": 42}), "42")

    def test_missing_id_is_none_rather_than_an_error(self) -> None:
        self.assertIsNone(bronze.record_id({"no_id": True}))

    def test_hash_ignores_key_order(self) -> None:
        a = {"id": "x", "title": "Push", "reps": 5}
        b = {"reps": 5, "title": "Push", "id": "x"}
        self.assertEqual(bronze.record_hash(a), bronze.record_hash(b))

    def test_hash_changes_when_content_changes(self) -> None:
        self.assertNotEqual(
            bronze.record_hash(workout("x", title="Push")),
            bronze.record_hash(workout("x", title="Pull")),
        )


class DedupeTests(unittest.TestCase):
    def test_duplicate_within_one_pass_is_dropped(self) -> None:
        """/v1/routines really does return the same record on two pages."""
        records = [workout("a"), workout("b"), workout("a")]
        result = bronze.dedupe("routines", records)
        self.assertEqual(len(result), 2)
        self.assertEqual({bronze.record_id(r) for r in result}, {"a", "b"})

    def test_last_occurrence_wins(self) -> None:
        records = [workout("a", title="old"), workout("a", title="new")]
        result = bronze.dedupe("routines", records)
        self.assertEqual(result[0]["title"], "new")

    def test_records_without_an_id_are_all_kept(self) -> None:
        records = [{"no_id": 1}, {"no_id": 2}]
        self.assertEqual(len(bronze.dedupe("workout_events", records)), 2)

    def test_nothing_is_dropped_when_there_are_no_duplicates(self) -> None:
        records = [workout("a"), workout("b"), workout("c")]
        self.assertEqual(len(bronze.dedupe("workouts", records)), 3)


class ChangeDetectionTests(unittest.TestCase):
    """changed_only decides whether reference data is re-landed on every run."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self._orig_duckdb, self._orig_bronze = bronze.config.duckdb_path, bronze.config.bronze_path
        bronze.config.duckdb_path = lambda: root / "warehouse.duckdb"
        bronze.config.bronze_path = lambda: root / "bronze"
        self.con = bronze.connect()

    def tearDown(self) -> None:
        self.con.close()
        bronze.config.duckdb_path, bronze.config.bronze_path = self._orig_duckdb, self._orig_bronze
        self.tmp.cleanup()

    def test_everything_is_new_on_a_first_run(self) -> None:
        records = [workout("a"), workout("b")]
        self.assertEqual(bronze.changed_only(self.con, "workouts", records), records)

    def test_unchanged_records_are_not_relanded(self) -> None:
        records = [workout("a"), workout("b")]
        bronze.land("workouts", records, run="run1")
        bronze.materialize(self.con, "workouts")
        self.assertEqual(bronze.changed_only(self.con, "workouts", records), [])

    def test_an_edited_record_is_relanded(self) -> None:
        bronze.land("workouts", [workout("a", title="Push")], run="run1")
        bronze.materialize(self.con, "workouts")

        edited = workout("a", title="Push (edited)", updated="2026-07-02T10:00:00Z")
        fresh = bronze.changed_only(self.con, "workouts", [edited])
        self.assertEqual(len(fresh), 1)
        self.assertEqual(fresh[0]["title"], "Push (edited)")

    def test_uuid_ids_still_match(self) -> None:
        """DuckDB infers UUID-shaped strings as UUID, which broke this once."""
        uid = "819a0718-33ac-4bfb-ad53-0331c4d042f5"
        records = [workout(uid)]
        bronze.land("workouts", records, run="run1")
        bronze.materialize(self.con, "workouts")
        self.assertEqual(bronze.changed_only(self.con, "workouts", records), [])

    def test_bronze_keeps_both_versions_after_an_edit(self) -> None:
        """Bronze is append-only: history is retained, staging picks the latest."""
        bronze.land("workouts", [workout("a", title="Push")], run="run1")
        bronze.land(
            "workouts",
            [workout("a", title="Push (edited)", updated="2026-07-02T10:00:00Z")],
            run="run2",
        )
        bronze.materialize(self.con, "workouts")

        rows = self.con.execute("select count(*) from bronze.workouts").fetchone()[0]
        distinct = self.con.execute(
            "select count(distinct record_id) from bronze.workouts"
        ).fetchone()[0]
        self.assertEqual((rows, distinct), (2, 1))

        latest = self.con.execute(
            """
            select record ->> 'title' from (
                select record, row_number() over (
                    partition by record_id order by ingested_at desc) rn
                from bronze.workouts
            ) where rn = 1
            """
        ).fetchone()[0]
        self.assertEqual(latest, "Push (edited)")

    def test_utc_offsets_survive_materialize(self) -> None:
        """read_json inference silently dropped these once; a 4-hour error."""
        bronze.land(
            "workouts",
            [{"id": "a", "start_time": "2026-07-24T23:04:57+00:00", "exercises": []}],
            run="run1",
        )
        bronze.materialize(self.con, "workouts")
        stored = self.con.execute(
            "select record ->> 'start_time' from bronze.workouts"
        ).fetchone()[0]
        self.assertEqual(stored, "2026-07-24T23:04:57+00:00")


if __name__ == "__main__":
    unittest.main()
