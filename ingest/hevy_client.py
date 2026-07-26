"""Hevy API v1 client.

Page-size ceilings are per-endpoint and undocumented — these values were found by
probing the live API, which rejects anything larger with a 400. The API sends no
rate-limit headers at all, so backoff here is defensive rather than informed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Iterator

import requests
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from ingest import config

log = logging.getLogger(__name__)

# Endpoint -> (max pageSize accepted by the API, key holding the records)
ENDPOINTS: dict[str, tuple[int, str]] = {
    "/v1/workouts": (10, "workouts"),
    "/v1/workouts/events": (10, "events"),
    "/v1/routines": (10, "routines"),
    "/v1/routine_folders": (10, "routine_folders"),
    "/v1/exercise_templates": (100, "exercise_templates"),
}


class RetryableError(Exception):
    """A transient failure worth retrying."""


@dataclass
class Page:
    endpoint: str
    page: int
    page_count: int
    records: list[dict[str, Any]]


class HevyClient:
    def __init__(self, api_key: str | None = None, *, timeout: int = 30) -> None:
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(
            {"api-key": api_key or config.api_key(), "accept": "application/json"}
        )

    @retry(
        retry=retry_if_exception_type((RetryableError, requests.ConnectionError, requests.Timeout)),
        wait=wait_exponential(multiplier=1, min=2, max=60),
        stop=stop_after_attempt(6),
        reraise=True,
    )
    def get(self, path: str, **params: Any) -> dict[str, Any]:
        response = self.session.get(
            f"{config.HEVY_API_BASE}{path}", params=params, timeout=self.timeout
        )

        if response.status_code == 429 or response.status_code >= 500:
            raise RetryableError(f"{response.status_code} from {path}")
        if response.status_code == 401:
            raise SystemExit("Hevy rejected the API key (401). Check HEVY_API_KEY.")
        response.raise_for_status()
        return response.json()

    def workout_count(self) -> int:
        return int(self.get("/v1/workouts/count")["workout_count"])

    def paginate(self, endpoint: str, **params: Any) -> Iterator[Page]:
        """Yield every page of a list endpoint, at the largest page size it allows."""
        page_size, records_key = ENDPOINTS[endpoint]
        page = 1
        page_count = 1

        while page <= page_count:
            body = self.get(endpoint, page=page, pageSize=page_size, **params)
            page_count = int(body.get("page_count", 1))
            records = body.get(records_key, []) or []

            log.info("%s page %d/%d (%d records)", endpoint, page, page_count, len(records))
            yield Page(endpoint, page, page_count, records)

            if not records:
                break
            page += 1

    def fetch_all(self, endpoint: str, **params: Any) -> list[dict[str, Any]]:
        return [record for page in self.paginate(endpoint, **params) for record in page.records]
