"""Input validation, the date policy, and the arithmetic.

Two rules drive everything here, in this order:

  1. Never invent a rate. If the number cannot be confirmed, the answer is an
     error, not a plausible figure.
  2. Never present a rate as belonging to a day it does not belong to. When
     the rate used comes from an earlier day than the one asked about, the
     response says so in a field a machine can branch on *and* a sentence a
     model can read out loud.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

from . import clock, config
from .errors import ToolError, codes
from .upstream import Upstream

_CURRENCY_RE = re.compile(r"^[A-Za-z]{3}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ONE = Decimal(1)


# --- parsing --------------------------------------------------------------


def parse_amount(raw: str | None) -> Decimal:
    if raw is None or raw.strip() == "":
        raise ToolError(
            codes.MISSING_PARAMETER,
            "The 'amount' parameter is required; there is nothing to convert without it.",
            400,
        )
    text = raw.strip()
    try:
        amount = Decimal(text)
    except (InvalidOperation, ValueError):
        raise ToolError(
            codes.INVALID_AMOUNT,
            f"'amount' must be a decimal number, but {text!r} is not one.",
            400,
        ) from None

    if not amount.is_finite():
        raise ToolError(
            codes.INVALID_AMOUNT,
            f"'amount' must be a finite number; {text!r} is not an amount of money.",
            400,
        )
    if amount == 0:
        raise ToolError(
            codes.INVALID_AMOUNT,
            "'amount' is zero. A zero conversion is almost always a caller that lost "
            "the number rather than a real question, so no result is returned; send "
            "the amount you meant.",
            400,
        )
    if amount < 0:
        raise ToolError(
            codes.INVALID_AMOUNT,
            f"'amount' must be greater than zero, but {text} is negative. Convert the "
            f"positive amount and describe the direction yourself.",
            400,
        )
    if amount > config.MAX_AMOUNT:
        raise ToolError(
            codes.INVALID_AMOUNT,
            f"'amount' is above the {config.MAX_AMOUNT:,f} ceiling this tool will "
            f"convert, so the result would not be meaningful.",
            400,
        )
    return amount


def parse_currency(raw: str | None, field: str) -> str:
    if raw is None or raw.strip() == "":
        raise ToolError(
            codes.MISSING_PARAMETER,
            f"The '{field}' parameter is required; it is the currency to convert "
            f"{'from' if field == 'from' else 'to'}.",
            400,
        )
    code = raw.strip().upper()
    if not _CURRENCY_RE.match(code):
        raise ToolError(
            codes.INVALID_CURRENCY,
            f"'{field}' must be a three-letter ISO 4217 currency code such as EUR, "
            f"but {raw.strip()!r} is not one.",
            400,
        )
    return code


def parse_date(raw: str | None) -> date | None:
    """None means "no date given", which is read as today."""
    if raw is None or raw.strip() == "":
        return None
    text = raw.strip()
    if not _DATE_RE.match(text):
        raise ToolError(
            codes.INVALID_DATE,
            f"'date' must be written as YYYY-MM-DD, but {text!r} is not.",
            400,
        )
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise ToolError(
            codes.INVALID_DATE,
            f"'date' must be a real calendar date, but {text!r} is not one.",
            400,
        ) from None


def check_date_range(asked: date, today: date) -> None:
    if asked > today:
        raise ToolError(
            codes.DATE_IN_FUTURE,
            f"{asked.isoformat()} is in the future (today is {today.isoformat()} UTC). "
            f"No exchange rate exists for a day that has not happened, and this tool "
            f"will not present an older rate as if it were one.",
            400,
        )
    if asked < config.SERIES_START:
        raise ToolError(
            codes.DATE_BEFORE_SERIES_START,
            f"{asked.isoformat()} is before the ECB reference-rate series begins on "
            f"{config.SERIES_START.isoformat()}, so no rate was ever published for it.",
            400,
        )


# --- the answer -----------------------------------------------------------


def build_note(asked_date: date, rate_date: date) -> str:
    """The sentence a model can read to the customer, when the days differ."""
    gap = (asked_date - rate_date).days
    day_word = "day" if gap == 1 else "days"
    weekday = asked_date.strftime("%A")
    reason = (
        f"{asked_date.isoformat()} was a {weekday} and the ECB publishes no rates at "
        f"the weekend"
        if asked_date.weekday() >= 5
        else f"the ECB published no rate for {asked_date.isoformat()}"
    )
    return (
        f"This rate is from {rate_date.isoformat()}, not {asked_date.isoformat()}: "
        f"{reason}, so the most recent published rate before it was used "
        f"({gap} {day_word} earlier)."
    )


async def convert(
    *,
    raw_amount: str | None,
    raw_from: str | None,
    raw_to: str | None,
    raw_date: str | None,
    upstream: Upstream,
) -> dict[str, Any]:
    amount = parse_amount(raw_amount)
    source_currency = parse_currency(raw_from, "from")
    target_currency = parse_currency(raw_to, "to")
    asked = parse_date(raw_date)

    today = clock.utc_today()
    if asked is not None:
        check_date_range(asked, today)
    asked_date = asked if asked is not None else today

    # Best effort: when the provider will tell us which codes exist, a typo is
    # a clean 400 and the rate endpoint is never troubled.
    known = await upstream.known_currencies()
    if known is not None:
        for field, code in (("from", source_currency), ("to", target_currency)):
            if code not in known:
                raise ToolError(
                    codes.UNKNOWN_CURRENCY,
                    f"'{field}' is {code}, which the exchange-rate provider does not "
                    f"publish. No conversion was attempted.",
                    400,
                )

    if source_currency == target_currency:
        # True on every day the currency exists, so no rate is fetched and
        # none is attributed to the ECB.
        rate = _ONE
        rate_date = asked_date
        source = config.SOURCE_LABEL_IDENTITY
    else:
        observation = await upstream.fetch_rate(source_currency, target_currency, asked)
        rate, rate_date = observation.rate, observation.rate_date
        if rate_date > asked_date:
            raise ToolError(
                codes.UPSTREAM_INVALID_RESPONSE,
                f"The provider returned a rate dated {rate_date.isoformat()}, which is "
                f"after the {asked_date.isoformat()} that was asked about. A later "
                f"day's rate cannot answer an earlier day's question, so it was "
                f"discarded.",
                502,
            )
        source = config.SOURCE_LABEL

    result = (amount * rate).quantize(config.RESULT_EXPONENT, rounding=ROUND_HALF_UP)
    exact = rate_date == asked_date

    return {
        "amount": _number(amount),
        "from": source_currency,
        "to": target_currency,
        "rate": _number(rate),
        "result": _number(result),
        "rate_date": rate_date.isoformat(),
        "asked_date": asked_date.isoformat(),
        "source": source,
        # Everything below this line exists so that a model relaying the answer
        # cannot miss a rate that belongs to a different day than the question.
        "rate_date_is_asked_date": exact,
        "note": None if exact else build_note(asked_date, rate_date),
    }


def _number(value: Decimal) -> float | int:
    """JSON numbers, without Decimal's string serialisation."""
    if value == value.to_integral_value():
        try:
            return int(value)
        except (OverflowError, ValueError):  # pragma: no cover - guarded by MAX_AMOUNT
            return float(value)
    return float(value)
