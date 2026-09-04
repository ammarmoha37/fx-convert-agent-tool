# Review of tool.py

Findings ranked by harm to a paying customer.

## 1. Failures return HTTP 200 with `rate: 0.0`

The `except Exception` block still returns a success-shaped body: `rate` and `result` are `0.0`. An unknown ECB code, a network error, or bad JSON all look like a real conversion to zero.

The agent will tell the customer “that is 0 TRY” or feed 0 into a payment. A wrong number is worse than no number.

**Verify:** run `uvicorn tool:app --port 8081`, then  
`GET /tools/convert?amount=1&from_=XXX&to=TRY&on=2026-08-28`.  
Expect 200 and `"rate": 0.0`. Compare with our app: 400 `invalid_currency`.

## 2. Weekend/holiday uses `/latest` (today), not the last rate for the asked day

If the asked day has no `rates` (or the pair is missing), it calls `/latest` — **today’s** fixing — then sets `rate_date` to `on` (the day the caller asked for). Frankfurter already rolls a weekend back to the previous published day and tells you that in `date`. This code ignores that and can stamp **today’s** rate on a **historical** Saturday or holiday.

The customer thinks they got “the rate for that date.” They got a different day’s number, labeled as the day they asked. The JSON still looks consistent (`from` matches, `rate_date` equals the asked day), so they are unlikely to notice.

**Verify:** `GET /tools/convert?amount=250&from_=EUR&to=TRY&on=2026-08-29` (Saturday). Check `rate` and `rate_date`. Then call `/latest` on Frankfurter and a dated Friday request. If `rate_date` is `2026-08-29` or the number matches **today** rather than Friday 28th, the fallback is lying.

## 3. The agent’s `from` (and `date`) are ignored

The handler binds `from_` and `on`, with no aliases. The brief’s URL uses `from` and `date`. If the agent sends `from=USD&date=2020-01-15`, those query keys are dropped. Defaults stay EUR and “latest”.

The customer asked for one pair and day; they get another. A careful reader can catch it because the body still shows `"from": "EUR"` when they sent USD.

**Verify:**  
`GET /tools/convert?amount=250&from=USD&to=GBP&date=2020-01-15`  
then the same amount with `from_=USD&on=2020-01-15`. The first call does not use USD or 2020.

## The one I would fix before shipping tonight

**Finding 1.** Stop returning 200 with a zero rate. On any failure, return a non-2xx `{error, message}` and no number. That is a small change and it stops the worst lie (an invented successful conversion). The weekend `/latest` label is next, but tonight I would fail closed first.

## Things that look suspicious but are fine

- **No `asked_date` next to `rate_date`** — the caller should be able to see what they asked for and which day’s rate they actually got. Missing `asked_date` looks incomplete. It can still work if `rate_date` is honest (finding 2 is the lie, not the missing field).
- **Multiply and round `result` to 2 decimals** — looks like we are throwing away precision. For a quoted amount of money, two decimal places is normal. (Rounding the **rate** to 2 places *before* multiplying is a separate, small accuracy issue; I would not block tonight’s ship on it.)
- **`GET /health`** — extra, not harmful.
