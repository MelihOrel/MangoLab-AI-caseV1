# fx-convert

One endpoint an agent can call as a tool. It converts an amount between two
currencies at an ECB reference rate, and it will return an error rather than a
number it cannot stand behind.

```
GET /tools/convert?amount=250&from=EUR&to=TRY&date=2026-08-28
```

## Run it

```bash
./run.sh                 # listens on $PORT, default 8080
./test.sh                # 63 tests, no network
```

Both scripts create `.venv` on first use and install `requirements.txt`.
Python 3.10+.

| variable | default | |
|---|---|---|
| `FX_UPSTREAM_BASE` | `https://api.frankfurter.dev` | Upstream base URL. The host appears nowhere else in the code. |
| `FX_UPSTREAM_PREFIX` | `v1` | Path prefix under that base. See "one ambiguity" below. |
| `PORT` | `8080` | |
| `FX_UPSTREAM_TIMEOUT` | `4.0` | Seconds for one upstream call. |

## The response

```json
{
  "amount": 250, "from": "EUR", "to": "TRY",
  "rate": 47.1234, "result": 11780.85,
  "rate_date": "2026-08-28", "asked_date": "2026-08-30",
  "source": "ECB via frankfurter.dev",
  "rate_date_is_asked_date": false,
  "note": "This rate is from 2026-08-28, not 2026-08-30: 2026-08-30 was a Sunday and the ECB publishes no rates at the weekend, so the most recent published rate before it was used (2 days earlier)."
}
```

The last two fields are additions to the shape in the brief. `rate_date`
differing from `asked_date` already carries the information, but a model
skimming a tool result can miss it, so there is one boolean to branch on and
one sentence it can read to the customer verbatim. On an exact match the
boolean is `true` and `note` is `null`.

`rate` is reported exactly as published — never rounded before being
multiplied. `result` is `amount x rate` in decimal arithmetic (not binary
floats), rounded half-up to 2 places.

Errors are always:

```json
{ "error": "no_rate_available", "message": "The provider has no EUR to XBT rate for 2026-08-28. ..." }
```

## Error codes

| code | HTTP | when |
|---|---|---|
| `missing_parameter` | 400 | `amount`, `from` or `to` was not supplied |
| `invalid_amount` | 400 | not a number, `nan`/`inf`, zero, negative, or above 10^12 |
| `invalid_currency` | 400 | not three letters |
| `unknown_currency` | 400 | three letters, but the provider does not publish it |
| `invalid_date` | 400 | not a real `YYYY-MM-DD` date |
| `date_in_future` | 400 | after today (UTC) |
| `date_before_series_start` | 400 | before 1999-01-04, when the ECB series begins |
| `no_rate_available` | 404 | the provider has no rate for that pair on or before that day |
| `upstream_timeout` | 504 | provider did not answer in time |
| `upstream_unavailable` | 503 | provider could not be reached |
| `upstream_error` | 502 | provider answered 5xx or another unexpected status |
| `upstream_invalid_response` | 502 | body was not JSON, or failed a sanity check (below) |
| `bad_request` / `not_found` / `method_not_allowed` | 4xx | malformed request, or an endpoint that does not exist |
| `internal_error` | 500 | a bug in this service |

## What it does in each case

**The ECB published no rate for the date asked (weekend, holiday).** It
answers with the most recent rate published *on or before* that date and makes
the substitution explicit: `rate_date` is the day the rate belongs to,
`asked_date` is the day that was asked about, `rate_date_is_asked_date` is
`false`, and `note` spells it out. The upstream is the source of truth for
which day the rate belongs to — its `date` field is read and echoed, never the
date we asked for. There is no cap on how far back the fallback reaches; the
gap is stated in the note instead, in days.

There is no holiday calendar in this codebase. A weekend, Good Friday and a
national closure all take the same path, because the upstream is the only
thing that knows which days it published on. The single weekday-dependent
piece is the wording of `note`: it names the day when the date asked about was
a Saturday or Sunday, and otherwise says only that no rate was published —
naming a holiday would require the calendar this service deliberately does not
keep. Christmas 2025 is the case worth trying: asking for Sunday 2025-12-28
answers with the rate from 2025-12-24, four days back across the closure.

**The date is in the future.** `date_in_future`, refused before any upstream
call. No rate exists for a day that has not happened, and answering with an
older one would be presenting a rate as belonging to a date it does not.

**The date is before the series starts.** `date_before_series_start`, also
without an upstream call.

**The currency code does not exist.** Three letters is checked locally
(`invalid_currency`). Existence is checked against the provider's own currency
list, cached for a day, so a typo is `unknown_currency` with no rate lookup at
all. If that list cannot be fetched the service does not fail — it falls
through to the rate call, and a missing rate becomes `no_rate_available`.

**`from` and `to` are the same.** 200 with `rate: 1`, no upstream call. That
is true on any day, including a Sunday, so no fallback applies. `source` says
`identity (same currency, no rate needed)` rather than claiming the ECB
published it.

**The upstream is slow, 500s, or is not JSON.** `upstream_timeout` (504),
`upstream_unavailable` (503), `upstream_error` (502),
`upstream_invalid_response` (502). No retries: an agent is holding a customer
on the line, so a fast honest failure beats a slow one, and the agent can
decide whether to retry.

**`amount` is missing, zero, negative, or has ten decimal places.** Missing is
`missing_parameter`. Zero is refused — a zero conversion is almost always a
model that lost the number, and returning `0.00` would confirm that mistake to
a customer. Negative is refused. Ten decimal places is *accepted*: it is a real
question, parsed as a decimal, and the result is rounded to 2 places at the end.

## What is checked before a number is returned

A wrong number is worse than no number, so the upstream's answer is not taken
on trust. Any of these produces an error instead of a figure:

- the rate is not a finite number, or is zero or negative;
- the response does not say which day the rate belongs to;
- the response says it is based on a different currency than the one asked for
  (a provider that ignores `base` would otherwise return a plausible, wrong
  number);
- the rate is dated *after* the day asked about;
- the rate is dated before the ECB series begins.

## Caching

Keyed on (from, to, day) — including the day, so a cached rate can never be
served for a different date. Rates for a settled day never change and are held
for 24 hours; today's rate is held for 10 minutes, because the ECB publishes
around 16:00 CET and the answer can change. In-process, bounded at 1024
entries. The currency list is fetched once a day.

## Tests

`./test.sh` runs 63 tests. Every upstream call goes through
`httpx.MockTransport`, so no socket is ever opened — the client code, URL
building, JSON parsing and sanity checks all run, but the bytes come from
`tests/conftest.py`. `test.sh` sets `FX_UPSTREAM_BASE` to a closed port so
that a regression which reaches the network fails loudly.

One test greps `app/` to assert the real hostname appears only in
`config.py`.

## One ambiguity, and how I resolved it

The brief fixes the upstream *base* as `https://api.frankfurter.dev`, but the
API's endpoints live under `/v1` (`/v1/2026-08-28`). So it is not clear whether
a fake upstream will serve `/v1/2026-08-28` or `/2026-08-28`. I default to
`/v1`, matching the real API, and made the prefix its own environment variable:
`FX_UPSTREAM_PREFIX=""` drops it. Happy to change the default.

## Deliberately not here

No auth, database, UI, Dockerfile, CI, or extra endpoints — not even
`/health`, since the brief said extra endpoints are not scored.

Windows: `run.sh` and `test.sh` are bash; use Git Bash or WSL, or run
`python -m uvicorn app.main:app --port 8080` and `python -m pytest` directly.
