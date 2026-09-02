"""The error contract.

Every non-2xx response this service produces is a `ToolError` rendered as

    {"error": "<machine code>", "message": "<a sentence a person could read>"}

The message is written for the language model that will read it and then have
to explain itself to a paying customer, so it says what went wrong *and* makes
clear that no number was invented to paper over it.
"""

from __future__ import annotations


class Codes:
    """Machine-readable error codes. Also the list documented in the README."""

    MISSING_PARAMETER = "missing_parameter"
    INVALID_AMOUNT = "invalid_amount"
    INVALID_CURRENCY = "invalid_currency"
    UNKNOWN_CURRENCY = "unknown_currency"
    INVALID_DATE = "invalid_date"
    DATE_IN_FUTURE = "date_in_future"
    DATE_BEFORE_SERIES_START = "date_before_series_start"
    NO_RATE_AVAILABLE = "no_rate_available"
    UPSTREAM_TIMEOUT = "upstream_timeout"
    UPSTREAM_UNAVAILABLE = "upstream_unavailable"
    UPSTREAM_ERROR = "upstream_error"
    UPSTREAM_INVALID_RESPONSE = "upstream_invalid_response"
    BAD_REQUEST = "bad_request"
    NOT_FOUND = "not_found"
    METHOD_NOT_ALLOWED = "method_not_allowed"
    INTERNAL_ERROR = "internal_error"


codes = Codes()


class ToolError(Exception):
    """An error with a machine code, a human sentence, and an HTTP status."""

    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status

    def as_body(self) -> dict[str, str]:
        return {"error": self.code, "message": self.message}

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"ToolError({self.code!r}, status={self.status})"


class UpstreamNotFound(Exception):
    """Internal: the upstream answered 404. Callers decide what that means."""
