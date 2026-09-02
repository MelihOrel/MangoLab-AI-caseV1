# Review of tool.py

Ranked by what reaches a paying customer. My ordering rule: a *plausible*
wrong number outranks an *obviously* wrong one, because nobody catches the
plausible one.

## 1. The date on the rate is not the date the rate is from

Two independent mechanisms, same result — the service states a day it never
checked.

**a. The upstream's date is thrown away.** `fetch_rate` returns
`str(on or date.today())` (lines 30 and 44): the date *asked for*, not the
date the rate belongs to. The response body contains `payload["date"]`, which
is the real answer, and it is never read.

**b. The weekend fallback makes that a lie rather than a rounding error.**
Lines 36–40: when the requested day has no rate, it refetches `/latest`. So
asking for Saturday 2026-08-30 returns *today's* rate labelled
`rate_date: "2026-08-30"`. Ask for a Saturday in 2019 and you get this week's
rate stamped 2019. There is no `asked_date` field, so the caller cannot detect
the substitution even in principle.

**c. The cache key omits the date and never expires.** Line 28:
`key = f"{base}-{target}"`. The first EUR→TRY answer is pinned for the life of
the process and returned for every date afterwards, each time relabelled with
whatever date that caller asked for. It also outlives the ECB's next
publication, so a long-running process serves last week's rate as today's.

**What it does to a customer.** They are told "on 28 August, 250 EUR was
11,780 TRY" and the number is from some other day. It is in the right
ballpark, so no one queries it, and it ends up in an invoice or a contract.
This is precisely the failure the brief names: a rate presented as belonging
to a date it does not belong to.

**How I would verify it.** Two ways, both quick.
- Live: start the service, `GET /tools/convert?amount=1&from=EUR&to=TRY`, note
  the rate; then `GET ...&on=2020-01-02`. Same rate comes back, with
  `rate_date: "2020-01-02"`. Two different days, one number.
- Deterministic: point it at a stub upstream that answers
  `{"date":"2026-08-28","rates":{"TRY":47.1234}}` for a request for
  2026-08-30, and assert `rate_date == "2026-08-28"`. It will be
  `"2026-08-30"`. (Not currently possible without editing the file — see
  finding 5.)

## 2. `from` and `date` from the documented URL are silently ignored

The brief's tool contract is
`?amount=250&from=EUR&to=TRY&date=2026-08-28`. The handler declares `from_`
and `on` (lines 48–49), so FastAPI never binds `from=` or `date=` — unknown
query parameters are dropped without complaint, and the defaults apply.

Calling the documented URL therefore:

- always prices at **today's** rate, whatever date was requested; and
- always converts **from EUR**, whatever `from` was sent. A request for
  `from=USD&to=TRY` returns the EUR→TRY rate. The response does echo
  `"from": "EUR"`, so a very careful caller could notice — but the number is
  the wrong one either way.

**What it does to a customer.** A USD question answered with a EUR rate: TRY
per EUR is roughly 1.17× TRY per USD, so the answer is ~17% out. Confidently,
with no error.

**How I would verify it.** `curl` the exact URL from the brief with
`from=USD&to=TRY&date=2020-01-02` and compare against the same call with
`from_=USD&on=2020-01-02`. Different answers from the same intent is the bug.

## 3. Every failure is returned as a successful 200 with `rate: 0.0`

The bare `except Exception` (lines 71–81) catches upstream 500s, timeouts,
non-JSON bodies, unknown currency codes, `KeyError` on `payload["rates"]` —
everything — and answers **HTTP 200** with `rate: 0.0, result: 0.0`. The only
trace is a `print()` to stdout.

**What it does to a customer.** The model is told the conversion succeeded and
the answer is zero, so it says "250 EUR is 0.00 TRY". It also removes the
caller's ability to retry: a 503 is retryable, a 200 is not. And there is no
`{"error", "message"}` contract at all, so nothing downstream can branch on
the failure.

Ranked below 1 and 2 because it is loud. A zero is visibly absurd and gets
caught; a plausible number does not.

**How I would verify it.** `GET ...?amount=250&from_=EUR&to=XYZ` — a currency
that does not exist. Expect a 4xx; observe 200 with zeros. Same by blocking
DNS for the upstream, or with a stub that returns a 500.

## 4. The rate is rounded to 2 decimals before it is multiplied

Line 60, `rate = round(rate, 2)`, then line 61 multiplies. EUR→TRY ≈ 47.1234
becomes 47.12, so 250 EUR converts to 11,780.00 instead of 11,780.85. The
error is proportional: ~0.007% here, but ~3,400 TRY on a 1,000,000 EUR quote,
and far worse for any pair whose rate is small — a rate of 0.0064 (JPY→EUR
territory) rounds to 0.01, a 56% error.

It also breaks the response contract: the brief's own example shows
`"rate": 47.1234`.

**How I would verify it.** Stub the upstream at 47.1234, request 250 EUR,
assert `rate == 47.1234` and `result == 11780.85`. Both fail. Or by hand:
`250 * 47.1234 = 11780.85`, and the service says 11780.00.

## 5. The upstream host is hardcoded

`UPSTREAM = "https://api.frankfurter.dev/v1"` (line 18), no environment
variable. This breaks a stated requirement, but the reason it matters
operationally is that it makes findings 1–4 untestable and unfixable without
touching the source: there is no way to point this at a stub, so it cannot
have a test suite that runs offline, and no way to fail over or throttle in
production. Verify by running with `FX_UPSTREAM_BASE=http://127.0.0.1:1` and
watching it talk to the real API anyway.

## The one I would fix before shipping tonight

**Finding 1** — make `rate_date` come from `payload["date"]` and put the date
in the cache key (with a TTL). It is a handful of lines.

I am choosing it over finding 3, which is tempting because the fix is one
line and the symptom is dramatic. But the zeros announce themselves: someone
sees "0.00 TRY" within a day and we roll back. A rate quietly attributed to
the wrong day produces a number that looks right, is used, and is discovered
weeks later by a customer reconciling an invoice — and by then every answer
the service gave is suspect. Given the brief's own rule that a wrong number is
worse than no number, the silent wrong number is the one that ships over my
objection.

If I could ship two lines, finding 3 goes in the same commit.

## Things that look suspicious but are fine

- **No timeout on `httpx.AsyncClient()` (line 23).** This is the first thing
  I reached for and it is not a defect: httpx applies a default
  `Timeout(5.0)` to connect, read, write and pool. The service will not hang
  forever. Five seconds is longer than I would want on an agent's critical
  path, but that is a tuning opinion, not a bug.
- **The cache dict is unbounded (line 21).** It looks like a memory leak, but
  the key is `base-target` over ~30 ECB currencies, so it is capped at roughly
  900 small entries. (It is bounded *because* of the bug in finding 1 — fix
  the key and the bound comes from the TTL instead.)
- **`float` for money.** Alarming on sight, and wrong for ledgers, but at
  these magnitudes float64 carries ~15 significant digits, so the error is
  many orders of magnitude below a cent. The 2-decimal rounding in finding 4
  is thousands of times larger. Not the problem here.
- **`round()` uses banker's rounding**, so a result landing exactly on a half
  cent rounds to even rather than up. Real, and worth a line of code, but it
  is a half-cent question — not something I would hold a release for.
- **The module-level `AsyncClient` is created at import (line 23), before any
  event loop exists.** This pattern often bites, but httpx binds lazily on
  first use, so it works under uvicorn. It is never closed, which leaks
  connections on shutdown — untidy, invisible to a customer.
- **`from __future__ import annotations` with FastAPI.** Used to break
  Pydantic's introspection on older versions; fine on anything current.
