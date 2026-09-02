"""The client for the exchange-rate provider.

The contract of this module is the point of the whole exercise: anything it
returns has already been checked. A caller can rely on

  * `rate` being a finite, strictly positive number, and
  * `rate_date` being the date **the upstream itself said** the rate belongs
    to, parsed from the response body -- never the date we asked for.

Anything that does not survive those checks becomes a ToolError. Nothing is
guessed, defaulted, or filled in.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from decimal import Decimal
from typing import Any, NamedTuple

import httpx

from . import clock, config
from .cache import TTLCache
from .errors import ToolError, UpstreamNotFound, codes

logger = logging.getLogger(__name__)

_CURRENCIES_KEY = ("currencies",)
_CURRENCIES_UNAVAILABLE = "unavailable"


class Observation(NamedTuple):
    """A rate, and the day it actually belongs to."""

    rate: Decimal
    rate_date: date


class Upstream:
    def __init__(self, client: httpx.AsyncClient, cache: TTLCache | None = None) -> None:
        self._client = client
        self._cache = cache if cache is not None else TTLCache(config.CACHE_MAX_ENTRIES)

    # -- public ------------------------------------------------------------

    async def fetch_rate(self, base: str, target: str, on: date | None) -> Observation:
        """Return the observation for `base`->`target`, for `on` or the latest.

        `on` is passed through to the upstream, which may answer with an
        earlier day (weekends, holidays). We report whatever day it names.
        """
        key = ("rate", base, target, on.isoformat() if on else "latest")
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        path = on.isoformat() if on else "latest"
        try:
            payload = await self._get_json(path, params={"base": base, "symbols": target})
        except UpstreamNotFound:
            raise ToolError(
                codes.NO_RATE_AVAILABLE,
                f"The provider has no {base} to {target} rate for "
                f"{on.isoformat() if on else 'the latest publication'}. No rate is "
                f"returned rather than one substituted from another day or pair.",
                404,
            ) from None

        observation = self._read_observation(payload, base, target)

        # A day in the past is settled and will not change; today's rate can
        # still be revised when the ECB publishes.
        historical = on is not None and on < clock.utc_today()
        ttl = (
            config.CACHE_TTL_HISTORICAL_SECONDS
            if historical
            else config.CACHE_TTL_LATEST_SECONDS
        )
        self._cache.set(key, observation, ttl)
        return observation

    async def known_currencies(self) -> frozenset[str] | None:
        """The provider's currency list, or None if it cannot be determined.

        Best effort on purpose. When we have the list we can say
        "unknown_currency" with certainty and never bother the rate endpoint.
        When we cannot get it -- a stripped-down fake upstream, an outage --
        we degrade to letting the rate call answer, instead of failing every
        request because a secondary endpoint is down.
        """
        cached = self._cache.get(_CURRENCIES_KEY)
        if cached is _CURRENCIES_UNAVAILABLE:
            return None
        if cached is not None:
            return cached

        try:
            payload = await self._get_json("currencies", params=None)
        except (ToolError, UpstreamNotFound):
            logger.warning("currency list unavailable; falling back to rate-endpoint validation")
            self._cache.set(
                _CURRENCIES_KEY,
                _CURRENCIES_UNAVAILABLE,
                config.CACHE_TTL_CURRENCIES_FAILURE_SECONDS,
            )
            return None

        codes_found = frozenset(
            key.upper() for key in payload.keys() if isinstance(key, str) and len(key) == 3
        )
        if not codes_found:
            self._cache.set(
                _CURRENCIES_KEY,
                _CURRENCIES_UNAVAILABLE,
                config.CACHE_TTL_CURRENCIES_FAILURE_SECONDS,
            )
            return None

        self._cache.set(_CURRENCIES_KEY, codes_found, config.CACHE_TTL_CURRENCIES_SECONDS)
        return codes_found

    # -- transport ---------------------------------------------------------

    async def _get_json(self, path: str, params: dict[str, str] | None) -> dict[str, Any]:
        url = f"{config.UPSTREAM_BASE}{config.UPSTREAM_PREFIX}/{path}"
        try:
            response = await self._client.get(url, params=params)
        except httpx.TimeoutException as exc:
            logger.warning("upstream timeout for %s: %s", path, exc)
            raise ToolError(
                codes.UPSTREAM_TIMEOUT,
                f"The exchange-rate provider did not answer within "
                f"{config.UPSTREAM_TIMEOUT_SECONDS:g} seconds, so no rate could be "
                f"confirmed. Nothing was estimated in its place.",
                504,
            ) from exc
        except httpx.HTTPError as exc:
            logger.warning("upstream unreachable for %s: %s", path, exc)
            raise ToolError(
                codes.UPSTREAM_UNAVAILABLE,
                "The exchange-rate provider could not be reached, so no rate could "
                "be confirmed. Nothing was estimated in its place.",
                503,
            ) from exc

        if response.status_code == 404:
            raise UpstreamNotFound(url)

        if response.status_code >= 500:
            logger.warning("upstream %s for %s", response.status_code, path)
            raise ToolError(
                codes.UPSTREAM_ERROR,
                f"The exchange-rate provider failed with HTTP "
                f"{response.status_code}, so no rate could be confirmed.",
                502,
            )

        if response.status_code >= 300:
            logger.warning("unexpected upstream %s for %s", response.status_code, path)
            raise ToolError(
                codes.UPSTREAM_ERROR,
                f"The exchange-rate provider answered with an unexpected HTTP "
                f"{response.status_code}, so no rate could be confirmed.",
                502,
            )

        # parse_float=Decimal keeps 47.1234 exactly 47.1234 all the way through
        # the multiplication, instead of the nearest binary float to it.
        try:
            payload = json.loads(response.text, parse_float=Decimal)
        except ValueError as exc:
            logger.warning("upstream returned non-JSON for %s: %s", path, exc)
            raise ToolError(
                codes.UPSTREAM_INVALID_RESPONSE,
                "The exchange-rate provider returned a body that is not JSON, so no "
                "rate could be read from it.",
                502,
            ) from exc

        if not isinstance(payload, dict):
            raise ToolError(
                codes.UPSTREAM_INVALID_RESPONSE,
                "The exchange-rate provider returned JSON that is not an object, so "
                "no rate could be read from it.",
                502,
            )
        return payload

    # -- validation of the payload ----------------------------------------

    def _read_observation(self, payload: dict[str, Any], base: str, target: str) -> Observation:
        rates = payload.get("rates")
        if not isinstance(rates, dict) or target not in rates:
            raise ToolError(
                codes.NO_RATE_AVAILABLE,
                f"The provider's response contains no {target} rate for a {base} "
                f"request. No rate is returned rather than one taken from elsewhere "
                f"in the response.",
                404,
            )

        rate = _as_decimal(rates[target])
        if rate is None or not rate.is_finite() or rate <= 0:
            raise ToolError(
                codes.UPSTREAM_INVALID_RESPONSE,
                f"The provider reported {rates[target]!r} as the {base} to {target} "
                f"rate, which is not a usable exchange rate.",
                502,
            )

        raw_date = payload.get("date")
        rate_date = _as_date(raw_date)
        if rate_date is None:
            raise ToolError(
                codes.UPSTREAM_INVALID_RESPONSE,
                "The provider did not say which day its rate belongs to, so the rate "
                "cannot be reported honestly and is not used.",
                502,
            )

        # A provider that quietly ignores `base` would hand back EUR-based
        # rates for a USD question: a plausible number that is simply wrong.
        # Checked only when the field is present, so a minimal upstream that
        # omits it still works.
        returned_base = payload.get("base")
        if isinstance(returned_base, str) and returned_base.upper() != base:
            raise ToolError(
                codes.UPSTREAM_INVALID_RESPONSE,
                f"The provider answered with rates based on {returned_base.upper()} "
                f"after being asked for {base}, so its numbers do not answer the "
                f"question and were discarded.",
                502,
            )

        if rate_date < config.SERIES_START:
            raise ToolError(
                codes.UPSTREAM_INVALID_RESPONSE,
                f"The provider dated its rate {rate_date.isoformat()}, before the ECB "
                f"series begins on {config.SERIES_START.isoformat()}, so it was not used.",
                502,
            )

        return Observation(rate=rate, rate_date=rate_date)


def _as_decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    return None


def _as_date(value: Any) -> date | None:
    if not isinstance(value, str) or len(value) != 10:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None
