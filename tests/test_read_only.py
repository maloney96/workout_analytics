"""The Hevy API must never be written to. These tests are the guardrail's proof.

Run with: python -m unittest discover -s tests
"""

from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from ingest.hevy_client import HevyClient, ReadOnlySession, ReadOnlyViolation  # noqa: E402

WRITE_METHODS = ["POST", "PUT", "PATCH", "DELETE"]


class ReadOnlySessionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.session = ReadOnlySession()

    def test_convenience_methods_are_blocked(self) -> None:
        for verb in ("post", "put", "patch", "delete"):
            with self.subTest(verb=verb):
                with self.assertRaises(ReadOnlyViolation):
                    getattr(self.session, verb)("https://api.hevyapp.com/v1/workouts")

    def test_explicit_request_calls_are_blocked(self) -> None:
        for method in WRITE_METHODS:
            with self.subTest(method=method):
                with self.assertRaises(ReadOnlyViolation):
                    self.session.request(method, "https://api.hevyapp.com/v1/workouts")

    def test_lowercase_verbs_are_blocked(self) -> None:
        with self.assertRaises(ReadOnlyViolation):
            self.session.request("post", "https://api.hevyapp.com/v1/workouts")

    def test_nothing_reaches_the_network_on_a_blocked_write(self) -> None:
        with mock.patch("requests.adapters.HTTPAdapter.send") as send:
            with self.assertRaises(ReadOnlyViolation):
                self.session.post("https://api.hevyapp.com/v1/workouts", json={})
        send.assert_not_called()

    def test_get_is_still_allowed(self) -> None:
        with mock.patch("requests.Session.request", return_value="ok") as parent:
            self.assertEqual(self.session.request("GET", "https://example.invalid"), "ok")
        parent.assert_called_once()

    def test_client_uses_the_guarded_session(self) -> None:
        client = HevyClient(api_key="test-key-not-real")
        self.assertIsInstance(client.session, ReadOnlySession)
        with self.assertRaises(ReadOnlyViolation):
            client.session.post("https://api.hevyapp.com/v1/workouts")


class NoWriteCallsAnywhereTests(unittest.TestCase):
    """Catch a bare `requests.post(...)` that bypasses the guarded session entirely."""

    BANNED = {"post", "put", "patch", "delete"}

    def test_no_module_calls_a_write_verb(self) -> None:
        offenders: list[str] = []

        for path in sorted((*REPO_ROOT.glob("ingest/*.py"), *REPO_ROOT.glob("scripts/*.py"))):
            tree = ast.parse(path.read_text(), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                if isinstance(func, ast.Attribute) and func.attr in self.BANNED:
                    # session.post(...), requests.put(...), client.session.delete(...)
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno} .{func.attr}()")

        self.assertEqual(offenders, [], f"HTTP write calls found: {offenders}")


if __name__ == "__main__":
    unittest.main()
