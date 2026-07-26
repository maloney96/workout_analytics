"""Configuration shared by local runs and GitHub Actions.

Real environment variables always win over `.env`, which is what makes the two
environments interchangeable: locally the values come from `.env`, and in CI the
same names arrive as env vars from GitHub Secrets. Nothing downstream has to know
which one it got.
"""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

HEVY_API_BASE = "https://api.hevyapp.com"


def load_dotenv(path: Path | None = None) -> None:
    """Load KEY=VALUE pairs from `.env` without overwriting existing env vars."""
    env_path = path or REPO_ROOT / ".env"
    if not env_path.is_file():
        return

    for raw in env_path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'\"")
        os.environ.setdefault(key, value)


def get(name: str, default: str | None = None, *, required: bool = False) -> str | None:
    load_dotenv()
    value = os.environ.get(name, default)
    if required and not value:
        raise SystemExit(
            f"Missing required config: {name}\n"
            f"  Local runs: copy .env.example to .env and set it.\n"
            f"  CI runs:    set it as a GitHub Actions secret "
            f"(make sync-secrets, or gh secret set {name})."
        )
    return value


def api_key() -> str:
    return get("HEVY_API_KEY", required=True)  # type: ignore[return-value]


def local_tz() -> str:
    return get("LOCAL_TZ", "UTC")  # type: ignore[return-value]


def duckdb_path() -> Path:
    return Path(get("DUCKDB_PATH", str(REPO_ROOT / "data" / "warehouse.duckdb")))


def bronze_path() -> Path:
    return Path(get("BRONZE_PATH", str(REPO_ROOT / "data" / "bronze")))
