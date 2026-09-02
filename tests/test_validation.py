"""Amounts and currency codes."""

from __future__ import annotations

import pytest

from conftest import FrankfurterStub, build_client, convert

OK = {"from": "EUR", "to": "TRY", "date": "2026-08-28"}


def test_amount_is_required():
    client, _ = build_client()
    response = client.get("/tools/convert", params=OK)
    assert response.status_code == 400
    assert response.json()["error"] == "missing_parameter"


@pytest.mark.parametrize("bad", ["0", "0.00", "-5", "abc", "", "nan", "inf", "-inf", "1e400"])
def test_bad_amounts_are_refused(bad):
    client, stub = build_client()
    response = client.get("/tools/convert", params={**OK, "amount": bad})
    assert response.status_code == 400, bad
    assert response.json()["error"] in {"invalid_amount", "missing_parameter"}, bad
    assert stub.rate_calls == [], bad


def test_ten_decimal_places_are_accepted_and_the_result_is_rounded():
    """250.1234567891 EUR is a real question; the answer is money, so 2dp."""
    client, _ = build_client()
    body = client.get("/tools/convert", params={**OK, "amount": "250.1234567891"}).json()
    assert body["amount"] == 250.1234567891
    # 250.1234567891 * 47.1234 == 11786.6666...; exact decimal maths, half up.
    assert body["result"] == 11786.67


def test_amount_maths_is_decimal_not_binary_float():
    """0.1 + 0.2 style drift must not reach a customer's invoice."""
    client, _ = build_client()
    body = client.get("/tools/convert", params={**OK, "amount": "0.07"}).json()
    assert body["result"] == 3.3  # 0.07 * 47.1234 = 3.298638 -> 3.30


def test_from_and_to_are_required():
    client, _ = build_client()
    assert client.get("/tools/convert", params={"amount": 1, "to": "TRY"}).json()["error"] == (
        "missing_parameter"
    )
    assert client.get("/tools/convert", params={"amount": 1, "from": "EUR"}).json()["error"] == (
        "missing_parameter"
    )


@pytest.mark.parametrize("bad", ["EU", "EURO", "E1R", "€", "12"])
def test_malformed_currency_codes_are_refused(bad):
    client, stub = build_client()
    response = client.get("/tools/convert", params={**OK, "amount": 1, "from": bad})
    assert response.status_code == 400, bad
    assert response.json()["error"] == "invalid_currency", bad
    assert stub.rate_calls == [], bad


def test_a_well_formed_but_nonexistent_currency_is_refused_by_name():
    client, stub = build_client()
    response = client.get("/tools/convert", params={**OK, "amount": 1, "to": "XBT"})
    assert response.status_code == 400
    assert response.json()["error"] == "unknown_currency"
    assert "XBT" in response.json()["message"]
    assert stub.rate_calls == []


def test_unknown_currency_still_caught_when_the_currency_list_is_unavailable():
    """A stripped-down upstream must degrade, not fail every request."""
    stub = FrankfurterStub(currencies=None)
    client, _ = build_client(stub)

    good = client.get("/tools/convert", params={**OK, "amount": 1})
    assert good.status_code == 200, good.json()

    bad = client.get("/tools/convert", params={**OK, "amount": 1, "to": "XBT"})
    assert bad.status_code == 404
    assert bad.json()["error"] == "no_rate_available"


def test_same_currency_returns_one_without_asking_upstream():
    client, stub = build_client()
    body = client.get(
        "/tools/convert", params={"amount": 250, "from": "EUR", "to": "EUR", "date": "2026-08-30"}
    ).json()

    assert body["rate"] == 1
    assert body["result"] == 250
    assert body["rate_date"] == "2026-08-30"
    assert body["asked_date"] == "2026-08-30"
    # 1 EUR is 1 EUR on a Sunday too, so this is not a fallback...
    assert body["rate_date_is_asked_date"] is True
    # ...but it is not an ECB observation either, and does not claim to be.
    assert body["source"] == "identity (same currency, no rate needed)"
    assert stub.rate_calls == []
