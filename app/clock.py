"""The one place "what day is it" is decided.

Everything compares against today in UTC. The alternative — the caller's
local day — would make `rate_date` ambiguous, because the dates in the
response are ECB publication dates and belong to no caller's timezone. The
cost is that a caller east of UTC can be told `date_in_future` for a few
hours a day; the README says so out loud.

Kept in its own module so tests can freeze it.
"""

from __future__ import annotations

from datetime import date, datetime, timezone


def utc_today() -> date:
    return datetime.now(timezone.utc).date()
