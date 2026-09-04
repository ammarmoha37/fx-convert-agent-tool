# Notes

## Decisions

When ECB publishes nothing for the asked day (weekend or holiday), I still answer, but only with the last published rate. Frankfurter already does that rollback and puts the real day in `date`. I copy that into `rate_date` and leave `asked_date` as the caller’s day. I do not call `/latest` and stamp today’s rate on a historical request — that would be a lie.

I refuse a future date and any date before the ECB euro series (1999-01-04). I refuse `from == to` instead of returning `1.0`; that call is usually an agent mistake. Amounts must be greater than zero and at most six decimal places. I use Frankfurter v1 so `source` is honestly ECB, not a blended v2 feed. The cache key is `(from, to, asked_date)` and stores the published `rate_date` with the rate. Failures are not cached.

## With another day

I would split `app.py` into smaller files (validation, upstream, cache, route) so it is easier to read and change. I would cap the in-memory cache and drop the oldest entries when the limit is reached, so the process cannot grow without bound. I would load the ECB currency list once and check `from` / `to` against it, instead of treating a Frankfurter 404 as “unknown currency.”

## AI tools

I used Cursor. I have worked with TypeScript a lot day to day. I am aiming to learn Python and FastAPI, so I built this in that stack, step by step. I ran the server myself and curled the cases in the brief. I did not accept a finished dump. AI helped a lot with `run.sh`, `test.sh`, the docs (README and these notes), and explaining the code snippets as we went.

## One thing the AI got wrong

It mapped every upstream 404 to `date_out_of_range`. I curled `from=XXX` and got `{"error":"date_out_of_range","message":"not found"}` while `EUR` to `TRY` on the same day worked. Frankfurter returns 404 `"not found"` for an unknown code, not a sentence that contains “currency.” Dates we cannot serve are already rejected before the network call, and weekends come back 200 with an earlier `date`. I changed 404 to `invalid_currency` so a typo is not reported as a bad date.
