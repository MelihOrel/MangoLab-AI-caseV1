"""Every way the provider can let us down, and the error each one becomes.

None of these opens a socket: the failures are raised by the mock transport.
"""

from __future__ import annotations

import httpx
import pytest

from conftest import build_client, convert

ASK = {"amount": 250, "from": "EUR", "to": "TRY", "date": "2026-08-28"}
CURRENCIES = {"EUR": "Euro", "TRY": "Turkish Lira", "USD": "United States Dollar"}


def rates_only(response_factory):
    """Serve the currency list normally, and `response_factory` for rates."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/currencies"):
            return httpx.Response(200, json=CURRENCIES)
        return response_factory(request)

    return handler


def test_timeout_becomes_504():
    def blow_up(request):
        raise httpx.ReadTimeout("too slow", request=request)

    client, _ = build_client(rates_only(blow_up))
    response = client.get("/tools/convert", params=ASK)
    assert response.status_code == 504
    assert response.json()["error"] == "upstream_timeout"


def test_connection_failure_becomes_503():
    def blow_up(request):
        raise httpx.ConnectError("closed port", request=request)

    client, _ = build_client(rates_only(blow_up))
    response = client.get("/tools/convert", params=ASK)
    assert response.status_code == 503
    assert response.json()["error"] == "upstream_unavailable"


@pytest.mark.parametrize("status", [500, 502, 503, 429, 418])
def test_unexpected_status_becomes_502(status):
    client, _ = build_client(rates_only(lambda r: httpx.Response(status, text="nope")))
    response = client.get("/tools/convert", params=ASK)
    assert response.status_code == 502, status
    assert response.json()["error"] == "upstream_error", status


def test_html_instead_of_json_becomes_502():
    body = "<html><body>502 Bad Gateway</body></html>"
    client, _ = build_client(rates_only(lambda r: httpx.Response(200, text=body)))
    response = client.get("/tools/convert", params=ASK)
    assert response.status_code == 502
    assert response.json()["error"] == "upstream_invalid_response"


@pytest.mark.parametrize(
    "payload",
    [
        {"amount": 1.0, "base": "EUR", "date": "2026-08-28"},          # no rates
        {"amount": 1.0, "base": "EUR", "rates": {"TRY": 47.1}},        # no date
        {"amount": 1.0, "base": "EUR", "date": "nope", "rates": {"TRY": 47.1}},
        {"amount": 1.0, "base": "EUR", "date": "2026-08-28", "rates": {"TRY": "47.1"}},
        {"amount": 1.0, "base": "EUR", "date": "2026-08-28", "rates": {"TRY": 0}},
        {"amount": 1.0, "base": "EUR", "date": "2026-08-28", "rates": {"TRY": -47.1}},
        {"amount": 1.0, "base": "EUR", "date": "2026-08-28", "rates": {"TRY": None}},
        {"amount": 1.0, "base": "EUR", "date": "1998-01-01", "rates": {"TRY": 47.1}},
    ],
)
def test_nonsense_payloads_never_become_a_number(payload):
    client, _ = build_client(rates_only(lambda r: httpx.Response(200, json=payload)))
    response = client.get("/tools/convert", params=ASK)
    assert response.status_code in (404, 502), payload
    assert response.json()["error"] in {
        "upstream_invalid_response",
        "no_rate_available",
    }, payload
    assert "rate" not in response.json()


def test_a_provider_that_ignores_our_base_is_caught():
    """Asked for USD, answered in EUR: a plausible number, and wrong."""
    payload = {"amount": 1.0, "base": "EUR", "date": "2026-08-28", "rates": {"TRY": 47.1234}}
    client, _ = build_client(rates_only(lambda r: httpx.Response(200, json=payload)))
    response = client.get(
        "/tools/convert", params={**ASK, "from": "USD", "to": "TRY"}
    )
    assert response.status_code == 502
    assert response.json()["error"] == "upstream_invalid_response"


def test_json_array_instead_of_object_becomes_502():
    client, _ = build_client(rates_only(lambda r: httpx.Response(200, json=[1, 2, 3])))
    response = client.get("/tools/convert", params=ASK)
    assert response.status_code == 502
    assert response.json()["error"] == "upstream_invalid_response"


def test_every_error_body_has_both_fields():
    def blow_up(request):
        raise httpx.ConnectError("closed port", request=request)

    client, _ = build_client(rates_only(blow_up))
    body = client.get("/tools/convert", params=ASK).json()
    assert set(body) == {"error", "message"}
    assert body["message"].endswith(".")
