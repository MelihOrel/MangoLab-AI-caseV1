"""FX_UPSTREAM_BASE is the only thing that decides where requests go."""

from __future__ import annotations

from app import config
from conftest import build_client

ASK = {"amount": 1, "from": "EUR", "to": "TRY", "date": "2026-08-28"}


def test_requests_go_to_the_configured_base(monkeypatch):
    monkeypatch.setattr(config, "UPSTREAM_BASE", "http://fake.invalid:9999")
    monkeypatch.setattr(config, "UPSTREAM_PREFIX", "/v1")

    client, stub = build_client()
    assert client.get("/tools/convert", params=ASK).status_code == 200

    for request in stub.calls:
        assert str(request.url).startswith("http://fake.invalid:9999/v1/")


def test_the_path_prefix_can_be_turned_off(monkeypatch):
    monkeypatch.setattr(config, "UPSTREAM_BASE", "http://fake.invalid:9999")
    monkeypatch.setattr(config, "UPSTREAM_PREFIX", "")

    client, stub = build_client()
    assert client.get("/tools/convert", params=ASK).status_code == 200

    assert str(stub.rate_calls[0].url).startswith("http://fake.invalid:9999/2026-08-28")


def test_the_date_is_the_upstream_path_and_the_pair_is_query():
    client, stub = build_client()
    client.get("/tools/convert", params=ASK)

    request = stub.rate_calls[0]
    assert request.url.path.endswith("/2026-08-28")
    assert request.url.params["base"] == "EUR"
    assert request.url.params["symbols"] == "TRY"
