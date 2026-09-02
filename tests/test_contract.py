"""The success shape, and the promise that a rate is never mislabelled."""

from __future__ import annotations

from pathlib import Path

from conftest import build_client, convert


def test_success_matches_the_documented_shape():
    client, _ = build_client()
    response = convert(client, amount=250, **{"from": "EUR"}, to="TRY", date="2026-08-28")

    assert response.status_code == 200
    assert response.json() == {
        "amount": 250,
        "from": "EUR",
        "to": "TRY",
        "rate": 47.1234,
        "result": 11780.85,
        "rate_date": "2026-08-28",
        "asked_date": "2026-08-28",
        "source": "ECB via frankfurter.dev",
        "rate_date_is_asked_date": True,
        "note": None,
    }


def test_rate_is_not_rounded_before_multiplying():
    """47.1234 rounded to 47.12 first would give 11780.00, 85 kurus short."""
    client, _ = build_client()
    body = convert(client, amount=250, **{"from": "EUR"}, to="TRY", date="2026-08-28").json()
    assert body["rate"] == 47.1234
    assert body["result"] == 11780.85


def test_lowercase_currency_codes_are_accepted_and_normalised():
    client, _ = build_client()
    body = convert(client, amount=10, **{"from": "eur"}, to="try", date="2026-08-28").json()
    assert body["from"] == "EUR"
    assert body["to"] == "TRY"


def test_omitting_the_date_prices_today():
    client, _ = build_client()
    body = convert(client, amount=1, **{"from": "EUR"}, to="TRY").json()
    assert body["asked_date"] == "2026-09-02"
    assert body["rate_date"] == "2026-09-02"
    assert body["rate_date_is_asked_date"] is True


def test_unknown_path_still_uses_the_error_shape():
    client, _ = build_client()
    body = client.get("/nope").json()
    assert body["error"] == "not_found"
    assert isinstance(body["message"], str)


def test_the_real_host_appears_only_in_config():
    """Nothing may hardcode the upstream host: it comes from the environment."""
    package = Path(__file__).resolve().parents[1] / "app"
    offenders = [
        path.name
        for path in package.glob("*.py")
        if path.name != "config.py" and "frankfurter" in path.read_text(encoding="utf-8")
    ]
    assert offenders == []
