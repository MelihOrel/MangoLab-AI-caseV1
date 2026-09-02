"""The date policy: what happens when the ECB published nothing that day."""

from __future__ import annotations

import httpx

from conftest import FrankfurterStub, build_client, convert


def test_weekend_falls_back_and_says_so():
    """2026-08-30 is a Sunday; the newest rate on or before it is Friday's."""
    client, _ = build_client()
    body = convert(client, amount=250, **{"from": "EUR"}, to="TRY", date="2026-08-30").json()

    assert body["asked_date"] == "2026-08-30"
    assert body["rate_date"] == "2026-08-28"
    assert body["rate_date_is_asked_date"] is False
    assert body["rate"] == 47.1234
    assert body["result"] == 11780.85
    # The sentence has to carry both days, because the model reads it aloud.
    assert "2026-08-28" in body["note"]
    assert "2026-08-30" in body["note"]
    assert "Sunday" in body["note"]


def test_holiday_fallback_note_does_not_claim_a_weekend():
    """A gap on a weekday reads as "no rate was published", not "weekend"."""
    stub = FrankfurterStub(series={"2026-08-27": 47.0})
    client, _ = build_client(stub)
    body = convert(client, amount=1, **{"from": "EUR"}, to="TRY", date="2026-08-28").json()

    assert body["rate_date"] == "2026-08-27"
    assert "weekend" not in body["note"]
    assert "published no rate for 2026-08-28" in body["note"]


def test_a_multi_day_closure_is_crossed_and_the_gap_is_reported():
    """Christmas 2025: the ECB is shut on the 25th, the 26th and the weekend.

    Nothing here knows that. The fallback is not "one day back" and there is
    no holiday calendar — the upstream names the day and we report the gap.
    """
    stub = FrankfurterStub(series={"2025-12-23": 50.4, "2025-12-24": 50.5072})
    client, _ = build_client(stub)
    body = convert(client, amount=1, **{"from": "EUR"}, to="TRY", date="2025-12-28").json()

    assert body["asked_date"] == "2025-12-28"
    assert body["rate_date"] == "2025-12-24"
    assert body["rate"] == 50.5072
    assert "4 days earlier" in body["note"]


def test_a_future_date_is_refused_without_asking_upstream():
    client, stub = build_client()
    response = convert(client, amount=1, **{"from": "EUR"}, to="TRY", date="2026-09-03")

    assert response.status_code == 400
    assert response.json()["error"] == "date_in_future"
    assert stub.rate_calls == []


def test_a_date_before_the_series_is_refused_without_asking_upstream():
    client, stub = build_client()
    response = convert(client, amount=1, **{"from": "EUR"}, to="TRY", date="1999-01-03")

    assert response.status_code == 400
    assert response.json()["error"] == "date_before_series_start"
    assert stub.rate_calls == []


def test_upstream_has_nothing_on_or_before_the_date():
    stub = FrankfurterStub(series={"2026-08-28": 47.1234})
    client, _ = build_client(stub)
    response = convert(client, amount=1, **{"from": "EUR"}, to="TRY", date="2020-01-02")

    assert response.status_code == 404
    assert response.json()["error"] == "no_rate_available"


def test_a_rate_dated_after_the_question_is_discarded():
    """The guard that stops a broken upstream turning into a wrong number."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/currencies"):
            return httpx.Response(200, json={"EUR": "Euro", "TRY": "Turkish Lira"})
        return httpx.Response(
            200,
            json={"amount": 1.0, "base": "EUR", "date": "2026-09-01", "rates": {"TRY": 47.9}},
        )

    client, _ = build_client(handler)
    response = convert(client, amount=1, **{"from": "EUR"}, to="TRY", date="2026-08-28")

    assert response.status_code == 502
    assert response.json()["error"] == "upstream_invalid_response"


def test_malformed_dates_are_rejected():
    client, _ = build_client()
    for bad in ("28-08-2026", "2026/08/28", "2026-8-28", "yesterday", "2026-13-01", "2026-02-30"):
        response = convert(client, amount=1, **{"from": "EUR"}, to="TRY", date=bad)
        assert response.status_code == 400, bad
        assert response.json()["error"] == "invalid_date", bad
