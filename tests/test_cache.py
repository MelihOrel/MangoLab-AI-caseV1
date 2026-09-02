"""Caching: the same question must not be asked upstream twice, and a cached
rate must never be handed out for a different day."""

from __future__ import annotations

from app import config
from conftest import build_client

ASK = {"amount": 250, "from": "EUR", "to": "TRY", "date": "2026-08-28"}


def test_repeating_the_same_question_does_not_re_ask_upstream():
    client, stub = build_client()
    first = client.get("/tools/convert", params=ASK).json()
    second = client.get("/tools/convert", params=ASK).json()

    assert first == second
    assert len(stub.rate_calls) == 1


def test_a_different_day_is_a_different_cache_entry():
    """The bug in tool.py: keying on the pair alone reuses another day's rate."""
    client, stub = build_client()
    a = client.get("/tools/convert", params=ASK).json()
    b = client.get("/tools/convert", params={**ASK, "date": "2026-08-27"}).json()

    assert len(stub.rate_calls) == 2
    assert a["rate"] != b["rate"]
    assert a["rate_date"] == "2026-08-28"
    assert b["rate_date"] == "2026-08-27"


def test_a_different_pair_is_a_different_cache_entry():
    client, stub = build_client()
    client.get("/tools/convert", params=ASK)
    client.get("/tools/convert", params={**ASK, "to": "USD"})
    assert len(stub.rate_calls) == 2


def test_todays_rate_expires_but_a_settled_day_does_not():
    now = [1000.0]
    client, stub = build_client(cache_clock=lambda: now[0])

    # A settled day in the past, and today, each asked once.
    client.get("/tools/convert", params=ASK)
    client.get("/tools/convert", params={"amount": 1, "from": "EUR", "to": "TRY"})
    assert len(stub.rate_calls) == 2

    # Past the short TTL: today's rate is re-fetched because the ECB may have
    # published since, the settled day is not because it cannot change.
    now[0] += config.CACHE_TTL_LATEST_SECONDS + 1
    client.get("/tools/convert", params=ASK)
    client.get("/tools/convert", params={"amount": 1, "from": "EUR", "to": "TRY"})
    assert len(stub.rate_calls) == 3


def test_the_currency_list_is_fetched_once_not_per_request():
    client, stub = build_client()
    client.get("/tools/convert", params=ASK)
    client.get("/tools/convert", params={**ASK, "date": "2026-08-27"})

    currency_calls = [r for r in stub.calls if r.url.path.endswith("/currencies")]
    assert len(currency_calls) == 1


def test_errors_are_not_cached_as_answers():
    client, stub = build_client()
    client.get("/tools/convert", params={**ASK, "amount": "0"})
    good = client.get("/tools/convert", params=ASK)
    assert good.status_code == 200
