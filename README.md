# fx-convert

HTTP tool for an AI agent that needs a **historical ECB conversion**, not a guess.

Call this when a customer asks something like “how much is 250 euros in lira on 28 August 2026?” You send `amount`, `from`, `to`, and `date`. You get back a rate you can quote, or an error you must not turn into a number.

```
GET /tools/convert?amount=250&from=EUR&to=TRY&date=2026-08-28
```

How to talk to the customer from the response:

- **`200`** — use `result` as the converted amount and `rate` as the ECB rate. If `rate_date` differs from `asked_date` (weekend or holiday), say the figure is from `rate_date`, not the day they asked for. Do not imply ECB published a rate on a day they did not.
- **Non-2xx** — read `error` and `message`. Tell the customer you could not convert. Never invent a rate, and never treat a missing rate as zero.

Rates come from Frankfurter v1 (ECB). A wrong number is worse than no number.

## Run

```bash
python3 -m venv .venv
source .venv/bin/activate          # macOS and Linux
# Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
./run.sh                           # PORT defaults to 8080
```

`FX_UPSTREAM_BASE` defaults to `https://api.frankfurter.dev`. The host is never hardcoded; set this env var to point at another upstream.

## Test

```bash
./test.sh
```

Tests fake the upstream (`httpx.MockTransport`) and pass with no network, including when `FX_UPSTREAM_BASE` is a closed port.

## Success

`200` with `amount`, `from`, `to`, `rate`, `result`, `rate_date`, `asked_date`, `source`.

- `asked_date` is what the caller sent.
- `rate_date` is the date field from the upstream JSON — the day the rate actually belongs to.
- The rate is not rounded before multiplying. `result` is rounded to 2 decimal places.

## Errors

Non-2xx with `{ "error": "<code>", "message": "<sentence>" }`.

| Code | Status | When |
|---|---|---|
| `invalid_amount` | 400 | Missing, not a number, zero, negative, or more than 6 decimal places |
| `invalid_currency` | 400 | Missing/malformed code, or not published by the ECB |
| `same_currency` | 400 | `from` and `to` are the same |
| `invalid_date` | 400 | Missing or not `YYYY-MM-DD` |
| `date_in_future` | 400 | After today (UTC) |
| `date_out_of_range` | 400 | Before the ECB euro series (`1999-01-04`) |
| `upstream_timeout` | 504 | Upstream did not answer within 3 seconds |
| `upstream_error` | 502 | Unreachable, HTTP 5xx, or a body that is not usable JSON |

## Behaviour

| Case | What we do |
|---|---|
| Weekend / holiday | Return the last published ECB rate. `rate_date` is that earlier day; `asked_date` stays what was asked. We do not call `/latest` and stamp today’s rate on the asked day. |
| Future date | Refuse (`date_in_future`). |
| Date before the series | Refuse (`date_out_of_range`). |
| Unknown currency | Refuse (`invalid_currency`). Frankfurter’s 404 `"not found"` is treated as this, because dates we cannot serve are already rejected locally. |
| `from == to` | Refuse (`same_currency`). We do not return `1.0`. |
| Slow / 500 / non-JSON | Refuse (`upstream_timeout` or `upstream_error`). Never `rate: 0`. |
| Bad amount | Refuse (`invalid_amount`). |
| Repeat question | Same pair + asked date is served from an in-memory cache. Amount is not part of the key. Failures are not cached. |

`tool.py` is not this service. It is the Part B review sample.
