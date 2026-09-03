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
            payload = response.json()
    except httpx.HTTPError as exc:
        raise ConvertError(
            502,
            "upstream_error",
            "the rate source could not be used; no rate was returned.",
        ) from exc

    rates = payload.get("rates") if isinstance(payload, dict) else None
    rate_date = payload.get("date") if isinstance(payload, dict) else None
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
        rate, rate_date = fetch_rate(from_code, to_code, asked)
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
