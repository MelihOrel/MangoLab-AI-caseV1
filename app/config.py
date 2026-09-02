"""Configuration, read from the environment once at import time.

The upstream host is deliberately *not* hardcoded anywhere else in this
package: `FX_UPSTREAM_BASE` is the single knob, and the default below is the
one named in the brief. Point it at a fake upstream and nothing in the code
notices or cares.
"""

from __future__ import annotations

import os
from datetime import date
from decimal import Decimal


def _env_str(name: str, default: str) -> str:
    value = os.environ.get(name)
    return default if value is None or value.strip() == "" else value.strip()


def _env_float(name: str, default: float) -> float:
    try:
        return float(_env_str(name, str(default)))
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env_str(name, str(default)))
    except ValueError:
        return default


# --- upstream -------------------------------------------------------------

#: Base URL of the exchange-rate provider. No path, no trailing slash.
UPSTREAM_BASE = _env_str("FX_UPSTREAM_BASE", "https://api.frankfurter.dev").rstrip("/")

# The brief fixes the *base* (https://api.frankfurter.dev) but Frankfurter's
# endpoints live under /v1. That leaves it ambiguous whether a fake upstream
# will serve /v1/2026-08-28 or /2026-08-28, so the prefix is its own knob:
# set FX_UPSTREAM_PREFIX="" to drop it entirely. Default matches the real API.
_prefix = _env_str("FX_UPSTREAM_PREFIX", "v1").strip("/")
UPSTREAM_PREFIX = f"/{_prefix}" if _prefix else ""

#: Total budget for one upstream call. An agent is waiting on this call with a
#: customer on the other end, so we fail fast rather than hang.
UPSTREAM_TIMEOUT_SECONDS = _env_float("FX_UPSTREAM_TIMEOUT", 4.0)

# --- cache ----------------------------------------------------------------

#: Rates for a day in the past never change once published, so they are held
#: for a long time. Today's / "latest" rates can still be revised when the ECB
#: publishes around 16:00 CET, so those get a short TTL.
CACHE_TTL_HISTORICAL_SECONDS = _env_int("FX_CACHE_TTL_HISTORICAL", 24 * 60 * 60)
CACHE_TTL_LATEST_SECONDS = _env_int("FX_CACHE_TTL_LATEST", 600)
CACHE_TTL_CURRENCIES_SECONDS = _env_int("FX_CACHE_TTL_CURRENCIES", 24 * 60 * 60)
#: How long to remember that the currency list could not be fetched, so a
#: broken /currencies endpoint does not get hammered once per request.
CACHE_TTL_CURRENCIES_FAILURE_SECONDS = _env_int("FX_CACHE_TTL_CURRENCIES_FAILURE", 60)
CACHE_MAX_ENTRIES = _env_int("FX_CACHE_MAX_ENTRIES", 1024)

# --- domain ---------------------------------------------------------------

#: First day of the ECB reference-rate series. Anything earlier cannot have a
#: rate, so we reject it without asking the upstream.
SERIES_START = date(1999, 1, 4)

#: Results are reported to two decimal places. See README for why this is a
#: flat 2 and not per-currency minor units.
RESULT_EXPONENT = Decimal("0.01")

#: Sanity ceiling on `amount`. Above this the caller is not asking a real
#: money question and the result would be noise.
MAX_AMOUNT = Decimal("1e12")

#: The `source` string is fixed by the brief.
SOURCE_LABEL = "ECB via frankfurter.dev"
SOURCE_LABEL_IDENTITY = "identity (same currency, no rate needed)"
