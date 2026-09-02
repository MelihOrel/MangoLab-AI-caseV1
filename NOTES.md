# Notes

## Decisions

**When the ECB published no rate for the date asked, I answer — and say so.**
The alternative was refusing, which is safe but useless: every weekend
question fails, and "no rate exists for a Sunday" is not what the customer
wants to hear. So the service uses the most recent rate published *on or
before* the day asked about, and makes the substitution impossible to miss:
`rate_date` is the day the rate belongs to, `asked_date` is the day that was
asked about, `rate_date_is_asked_date` is a boolean the caller can branch on,
and `note` is a sentence the model can read to the customer verbatim
("This rate is from 2026-08-28, not 2026-08-30: 2026-08-30 was a Sunday…").
The two dates alone would technically carry the information, but a model
skimming a tool result can miss a field it was not looking for; a boolean and
a sentence cannot be skimmed past.

**The upstream decides what day a rate is from, not me.** I read
`payload["date"]` and echo it. I never assume the rate I got back is for the
day I asked for — and I reject the answer outright if the returned date is
*after* the day asked about, since a later day's rate cannot answer an
earlier day's question.

**No holiday calendar, on purpose.** The fallback never asks whether a day was
a holiday. It has no calendar and does not want one: it sends the date
upstream and reads the `date` that comes back, so Good Friday, Christmas and a
national closure all take the same path a Sunday does. A hardcoded list would
need maintaining every year — Easter moves — and a day the list got wrong
would leave the code believing a rate should exist when it does not. The
calendar lives upstream; my job is to read what it says.

The one place a weekday matters is the *wording* of `note`. From the date
alone I can be certain 2026-08-30 was a Sunday, so the note says so. I cannot
know that 2026-04-03 was Good Friday without exactly the calendar I refused to
keep, so for a weekday gap the note says only that no rate was published and
does not invent a reason. Checked against the real API: Good Friday
2026-04-03 → 2026-04-02, Christmas 2025-12-25 → 2025-12-24, and Sunday
2025-12-28 → 2025-12-24 — four days back, across the whole Christmas closure.

**No cap on how far back the fallback reaches.** I considered a window (say
7 days) and decided the gap is better *reported* than *enforced*: the note
states it in days, so the caller can apply its own tolerance. A hard cap would
silently convert a stale-but-honest answer into a failure, and the honesty is
already there.

**Future dates are refused rather than answered with an older rate.** The
whole point of the fallback is "the closest rate at or before your date"; for
a future date there is no such thing, and answering with today's rate would be
exactly the mislabelling the fallback is designed to avoid.

**Zero amount is an error.** `0` is almost always a model that lost the number
rather than a real question, and returning `0.00 TRY` confirms the mistake to
the customer. Ten decimal places, by contrast, I accept: it is a real
question. Everything is computed in `Decimal` (I parse the upstream JSON with
`parse_float=Decimal`, so 47.1234 stays 47.1234 and not the nearest binary
float), and only the final result is rounded, half-up, to 2 places.

**`from == to` returns 1 without calling the upstream,** with `source` marked
`identity` rather than `ECB via frankfurter.dev` — it is true on every day, so
it is not a fallback, but it is also not something the ECB published and it
should not claim to be.

**No retries on upstream failure.** An agent is holding a customer on the
line. A fast, clear `upstream_timeout` that the agent can act on beats a slow
one, and the agent is better placed than I am to decide whether retrying is
worth the wait.

**Today is today in UTC.** The dates in the response are ECB publication
dates and belong to no caller's timezone; mixing in a local day would make
`rate_date` ambiguous. The cost is that a caller far enough east can be told
`date_in_future` for a few hours a day. It is in the README rather than hidden.

**One ambiguity I could not resolve from the brief.** `FX_UPSTREAM_BASE`
defaults to `https://api.frankfurter.dev`, but the endpoints live under `/v1`.
I could not tell whether a fake upstream will serve `/v1/2026-08-28` or
`/2026-08-28`, so I default to `/v1` (matching the real API) and made the
prefix its own variable, `FX_UPSTREAM_PREFIX`, which can be set to empty. Happy
to change the default — just say which.

## With another day

- **Per-currency minor units.** Everything rounds to 2 places; JPY and KRW
  have none, so 11,780.85 JPY is not a real amount of money.
- **Single-flight.** Ten identical requests arriving during a cache miss make
  ten upstream calls today. One in-flight future per key would fix it.
- **A bounded retry plus a circuit breaker**, once there is a metric showing
  how often the upstream actually flaps. I did not want to guess the policy.
- **A metric on the fallback rate.** If `rate_date_is_asked_date` is false far
  more often than the ECB calendar predicts, something upstream is wrong and
  nobody would currently notice.
- **Property-based tests on the amount arithmetic** (Hypothesis), instead of
  the handful of decimal cases I picked by hand.
- **The cache is per-process.** Fine for one instance; behind more than one it
  should be shared, and that is a different design conversation.

## AI tools

I worked with Claude (Claude Code / the Claude desktop app) throughout, mostly
as a fast pair rather than an autocomplete: I made the design calls — the
fallback policy, refusing zero amounts, no retries, what goes in the response —
and used it to draft the modules and the test suite against them, then read
and adjusted every file. I also used it to probe the real Frankfurter API
before writing any code, which is where the decision below came from.

## One thing the AI got wrong

**It assumed the upstream would 404 on a weekend date. It does not.**

The first pass at the design had an error branch for "the ECB published
nothing that day", on the assumption that asking Frankfurter for a Sunday
would fail and I would then walk backwards day by day looking for a rate.
Before writing it, I called the API for real:

```
GET /v1/2026-08-30?base=EUR&symbols=TRY
{"amount":1.0,"base":"EUR","date":"2026-08-28","rates":{"TRY":56.1718}}
```

It answers 200, silently gives you Friday's rate, and tells you so in `date`.
The assumed 404 branch would have been dead code — and, worse, the shape of
the bug it invited is exactly the one in `tool.py`: if you expect a failure on
weekends and do not get one, you never think to check which day the rate you
were handed is actually from.

So the design inverted. `date` is not a nice-to-have field to echo, it is the
authoritative answer to the only question that matters, and everything else
follows from reading it: `rate_date` comes from the payload, the cache is
keyed by the date the *caller* asked for while the *returned* date is stored
alongside, and there is an explicit guard rejecting any rate dated after the
day asked about — which is only conceivable because I no longer trust the
upstream to have understood the question.

(I checked the future-date and pre-1999 cases the same way: both really do
404. I still validate them locally rather than relying on that, since the
reviewing upstream is a fake one and may not behave the same.)
