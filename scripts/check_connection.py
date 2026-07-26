#!/usr/bin/env python3
"""Verify the Hevy API key resolves and works, from local or CI.

Hits /v1/workouts/count, the cheapest authenticated endpoint. Prints the workout
count on success. Never prints the key itself.

    python scripts/check_connection.py
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ingest import config  # noqa: E402


def main() -> int:
    key = config.api_key()
    source = "environment" if not (config.REPO_ROOT / ".env").is_file() else ".env or environment"
    print(f"Resolved HEVY_API_KEY from {source} ({len(key)} chars).")

    request = urllib.request.Request(
        f"{config.HEVY_API_BASE}/v1/workouts/count",
        headers={"api-key": key, "accept": "application/json"},
    )

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            print(
                "Authentication failed. The key was found but rejected — check that it is "
                "current and that the account still has Hevy Pro.",
                file=sys.stderr,
            )
        else:
            print(f"HTTP {exc.code} from Hevy API: {exc.reason}", file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print(f"Could not reach {config.HEVY_API_BASE}: {exc.reason}", file=sys.stderr)
        return 1

    print(f"Connection OK. Workouts on account: {payload.get('workout_count', payload)}")
    print(f"Local timezone configured as: {config.local_tz()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
