"""Test fixtures.

No socket is ever opened. Every upstream call goes through
`httpx.MockTransport`, which means the real client code -- URL building,
status handling, JSON parsing, the sanity checks -- is exercised, but the
bytes come from a function in this file. Running `./test.sh` with
FX_UPSTREAM_BASE pointing at a closed port changes nothing.
"""

from __future__ import annotations

from datetime import date

import httpx
import pytest
from fastapi.testclient import TestClient

from app import clock
from app.cache import TTLCache
from app.main import app, get_upstream
from app.upstream import Upstream

#: A Wednesday, so "today" is a normal publication day in every test.
TODAY = date(2026, 9, 2)

#: The rate on 2026-08-28 is the one from the brief's example.
DEFAULT_SERIES = {
    "2026-08-26": 46.9,
    "2026-08-27": 47.0,
    "2026-08-28": 47.1234,
    "2026-08-31": 47.5,
    "2026-09-01": 47.8,
    "2026-09-02": 47.9,
}

DEFAULT_CURRENCIES = {
    "EUR": "Euro",
    "TRY": "Turkish Lira",
    "USD": "United States Dollar",
    "GBP": "British Pound",
    "JPY": "Japanese Yen",
}


@pytest.fixture(autouse=True)
def frozen_today(monkeypatch):
    """Freeze "today" so date policy is deterministic."""
    monkeypatch.setattr(clock, "utc_today", lambda: TODAY)


class FrankfurterStub:
    """A stand-in that behaves the way the real upstream does.

    In particular it does what Frankfurter actually does on a weekend: it
    answers with the most recent published day on or before the one asked
    for, and says so in the `date` field.
    """

    def __init__(self, series=None, currencies=DEFAULT_CURRENCIES):
        self.series = dict(DEFAULT_SERIES if series is None else series)
        self.currencies = currencies
        self.calls: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        segment = request.url.path.rstrip("/").rsplit("/", 1)[-1]

        if segment == "currencies":
            if self.currencies is None:
                return httpx.Response(404, json={"message": "not found"})
            return httpx.Response(200, json=self.currencies)

        base = request.url.params.get("base", "EUR")
        symbols = request.url.params.get("symbols", "")

        if segment == "latest":
            effective = max(self.series)
        else:
            earlier = [day for day in self.series if day <= segment]
            if not earlier:
                return httpx.Response(404, json={"message": "not found"})
            effective = max(earlier)

        if symbols not in DEFAULT_CURRENCIES:
            return httpx.Response(404, json={"message": "not found"})

        return httpx.Response(
            200,
            json={
                "amount": 1.0,
                "base": base,
                "date": effective,
                "rates": {symbols: self.series[effective]},
            },
        )

    # convenience
    @property
    def rate_calls(self) -> list[httpx.Request]:
        return [r for r in self.calls if not r.url.path.endswith("/currencies")]


def build_client(handler=None, cache_clock=None):
    """Return (TestClient, handler). The handler records every request."""
    handler = FrankfurterStub() if handler is None else handler
    async_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    cache = TTLCache(64) if cache_clock is None else TTLCache(64, clock=cache_clock)
    upstream = Upstream(async_client, cache=cache)

    app.dependency_overrides[get_upstream] = lambda: upstream
    client = TestClient(app, raise_server_exceptions=False)
    return client, handler


@pytest.fixture
def client():
    client, handler = build_client()
    yield client, handler
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


def convert(client, **params) -> httpx.Response:
    return client.get("/tools/convert", params=params)
