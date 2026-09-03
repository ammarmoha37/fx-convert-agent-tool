"""HTTP currency convert tool for an AI agent."""

import os
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation

import httpx
from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse

# The application object uvicorn loads from this file.
app = FastAPI(title="fx-convert")

SOURCE = "ECB via frankfurter.dev"
MAX_AMOUNT_DECIMALS = 6
ECB_SERIES_START = date(1999, 1, 4)
# Default of the env var only. Requests always read FX_UPSTREAM_BASE so
# reviewers can point the app at a fake host.
DEFAULT_UPSTREAM_BASE = "https://api.frankfurter.dev"
UPSTREAM_TIMEOUT_SECONDS = 3.0

# (from, to, asked_date) -> (rate, rate_date). Failures are not stored.
_cache: dict[tuple[str, str, str], tuple[float, str]] = {}


class ConvertError(Exception):
    """A check failed. convert() turns this into {error, message}."""

    def __init__(self, status_code: int, error: str, message: str) -> None:
        self.status_code = status_code
        self.error = error
        self.message = message


def error_body(exc: ConvertError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.error, "message": exc.message},
    )


def parse_amount(raw: str | None) -> Decimal:
    if raw is None or raw.strip() == "":
        raise ConvertError(
            400,
            "invalid_amount",
            "amount is required and must be a number greater than zero.",
        )
    try:
        value = Decimal(raw.strip())
    except InvalidOperation as exc:
        raise ConvertError(
            400,
            "invalid_amount",
            "amount must be a number greater than zero.",
        ) from exc
    if not value.is_finite() or value <= 0:
        raise ConvertError(
            400,
            "invalid_amount",
            "amount must be a number greater than zero.",
        )
    exponent = value.as_tuple().exponent
    if isinstance(exponent, int) and exponent < -MAX_AMOUNT_DECIMALS:
        raise ConvertError(
            400,
            "invalid_amount",
            f"amount can have at most {MAX_AMOUNT_DECIMALS} decimal places.",
        )
    return value


def parse_currency(raw: str | None, field: str) -> str:
    if raw is None or raw.strip() == "":
        raise ConvertError(
            400,
            "invalid_currency",
            f"{field} is required and must be a 3-letter currency code.",
        )
    code = raw.strip().upper()
    if len(code) != 3 or not code.isalpha():
        raise ConvertError(
            400,
            "invalid_currency",
            f"{field} must be a 3-letter currency code.",
        )
    return code


def parse_asked_date(raw: str | None) -> date:
    if raw is None or raw.strip() == "":
        raise ConvertError(
            400,
            "invalid_date",
            "date is required and must be YYYY-MM-DD.",
        )
    try:
        asked = date.fromisoformat(raw.strip())
    except ValueError as exc:
        raise ConvertError(
            400,
            "invalid_date",
            "date must be YYYY-MM-DD.",
        ) from exc
    today = datetime.now(timezone.utc).date()
    if asked > today:
        raise ConvertError(400, "date_in_future", "date cannot be in the future.")
    if asked < ECB_SERIES_START:
        raise ConvertError(
            400,
            "date_out_of_range",
            "date is before the ECB euro series starts (1999-01-04).",
        )
    return asked


def upstream_base() -> str:
    # Strip a trailing slash so we never build ...dev//v1/...
    return os.environ.get("FX_UPSTREAM_BASE", DEFAULT_UPSTREAM_BASE).rstrip("/")


def upstream_message(response: httpx.Response) -> str:
    """Best-effort text from an error body. Empty if it is not JSON."""
    try:
        payload = response.json()
    except ValueError:
        return ""
    if isinstance(payload, dict):
        return str(payload.get("message") or "")
    return ""


def raise_for_upstream_status(response: httpx.Response) -> None:
    """Turn a non-2xx upstream reply into ConvertError. Never invent a rate."""
    if response.status_code < 400:
        return

    message = upstream_message(response)
    lowered = message.lower()

    # Frankfurter often answers 404 "not found" for an unknown code.
    # Dates we cannot serve are already rejected in parse_asked_date, and
    # weekends/holidays are 200 with an earlier "date", not 404.
    if "currency" in lowered or response.status_code == 404:
        raise ConvertError(
            400,
            "invalid_currency",
            "the currency code is not published by the ECB.",
        )
    if response.status_code >= 500:
        raise ConvertError(
            502,
            "upstream_error",
            "the rate source returned an error; no rate was used.",
        )
    raise ConvertError(
        502,
        "upstream_error",
        "the rate source rejected the request; no rate was used.",
    )


def fetch_rate(from_code: str, to_code: str, asked: date) -> tuple[float, str]:
    """Return (rate, rate_date) from the upstream JSON.

    Frankfurter v1: GET {base}/v1/YYYY-MM-DD?base=EUR&symbols=TRY
    On weekends/holidays the body still has a rate, but "date" is the last
    published day. We copy that field as rate_date. We never use `asked`
    as the rate's day.
    """
    url = f"{upstream_base()}/v1/{asked.isoformat()}"
    try:
        with httpx.Client(timeout=UPSTREAM_TIMEOUT_SECONDS) as client:
            response = client.get(
                url,
                params={"base": from_code, "symbols": to_code},
            )
    except httpx.TimeoutException as exc:
        # Slow source: fail closed. A late number is not worth guessing.
        raise ConvertError(
            504,
            "upstream_timeout",
            "the rate source did not answer in time; no rate was used.",
        ) from exc
    except httpx.RequestError as exc:
        # DNS failure, connection refused, and similar.
        raise ConvertError(
            502,
            "upstream_error",
            "the rate source could not be reached; no rate was used.",
        ) from exc

    raise_for_upstream_status(response)

    # Parse JSON only after a 2xx. HTML or empty bodies must not become a rate.
    try:
        payload = response.json()
    except ValueError as exc:
        raise ConvertError(
            502,
            "upstream_error",
            "the rate source did not return JSON; no rate was used.",
        ) from exc

    if not isinstance(payload, dict):
        raise ConvertError(
            502,
            "upstream_error",
            "the rate source returned an unexpected body; no rate was used.",
        )

    rates = payload.get("rates")
    rate_date = payload.get("date")
    if not isinstance(rates, dict) or to_code not in rates:
        raise ConvertError(
            400,
            "invalid_currency",
            f"{to_code} is not a published ECB quote for this request.",
        )
    if not isinstance(rate_date, str) or not rate_date:
        raise ConvertError(
            502,
            "upstream_error",
            "the rate source did not say which date the rate belongs to.",
        )

    try:
        rate = float(rates[to_code])
    except (TypeError, ValueError) as exc:
        raise ConvertError(
            502,
            "upstream_error",
            "the rate source returned a rate that is not a number.",
        ) from exc
    if rate <= 0:
        raise ConvertError(
            502,
            "upstream_error",
            "the rate source returned a rate that cannot be used.",
        )
    return rate, rate_date


def get_rate(from_code: str, to_code: str, asked: date) -> tuple[float, str]:
    """Same pair + asked day reuses the stored rate. Amount is not in the key."""
    key = (from_code, to_code, asked.isoformat())
    cached = _cache.get(key)
    if cached is not None:
        return cached
    rate, rate_date = fetch_rate(from_code, to_code, asked)
    _cache[key] = (rate, rate_date)
    return rate, rate_date


@app.get("/tools/convert")
def convert(
    amount: str | None = None,
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = None,
    date: str | None = None,
) -> JSONResponse:
    try:
        parsed_amount = parse_amount(amount)
        from_code = parse_currency(from_, "from")
        to_code = parse_currency(to, "to")
        if from_code == to_code:
            raise ConvertError(
                400,
                "same_currency",
                "from and to must be different currency codes.",
            )
        asked = parse_asked_date(date)
        rate, rate_date = get_rate(from_code, to_code, asked)
    except ConvertError as exc:
        return error_body(exc)

    amount_number = float(parsed_amount)
    # Multiply only. The rate is used as published; we do not round it first.
    result = round(amount_number * rate, 2)
    return JSONResponse(
        {
            "amount": amount_number,
            "from": from_code,
            "to": to_code,
            "rate": rate,
            "result": result,
            "rate_date": rate_date,
            "asked_date": asked.isoformat(),
            "source": SOURCE,
        }
    )
