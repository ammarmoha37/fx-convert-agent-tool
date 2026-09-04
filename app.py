"""HTTP currency convert tool for an AI agent."""

import os
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation

import httpx
from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, ValidationInfo, field_validator
from pydantic_core import PydanticCustomError

# The application object uvicorn loads from this file.
app = FastAPI(title="fx-convert")

# Defaults for env vars. Every request reads the env so reviewers can override.
DEFAULT_UPSTREAM_BASE = "https://api.frankfurter.dev"
DEFAULT_SOURCE = "ECB via frankfurter.dev"
DEFAULT_TIMEOUT_SECONDS = 3.0
DEFAULT_MAX_AMOUNT_DECIMALS = 6
DEFAULT_SERIES_START = date(1999, 1, 4)

# (from, to, asked_date) -> (rate, rate_date). Failures are not stored.
_cache: dict[tuple[str, str, str], tuple[float, str]] = {}

# Tests assign httpx.MockTransport here so pytest never opens a socket.
_transport: httpx.BaseTransport | None = None


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


def env_text(name: str, default: str) -> str:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip()


def max_amount_decimals() -> int:
    raw = os.environ.get("FX_MAX_AMOUNT_DECIMALS")
    if raw is None or raw.strip() == "":
        return DEFAULT_MAX_AMOUNT_DECIMALS
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_MAX_AMOUNT_DECIMALS
    if value < 0:
        return DEFAULT_MAX_AMOUNT_DECIMALS
    return value


def series_start_date() -> date:
    raw = os.environ.get("FX_SERIES_START")
    if raw is None or raw.strip() == "":
        return DEFAULT_SERIES_START
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return DEFAULT_SERIES_START


def timeout_seconds() -> float:
    raw = os.environ.get("FX_TIMEOUT_SECONDS")
    if raw is None or raw.strip() == "":
        return DEFAULT_TIMEOUT_SECONDS
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_TIMEOUT_SECONDS
    if value <= 0:
        return DEFAULT_TIMEOUT_SECONDS
    return value


def source_label() -> str:
    return env_text("FX_SOURCE", DEFAULT_SOURCE)


def upstream_base() -> str:
    # Strip a trailing slash so we never build ...dev//v1/...
    return env_text("FX_UPSTREAM_BASE", DEFAULT_UPSTREAM_BASE).rstrip("/")


class ConvertQuery(BaseModel):
    """Query string for GET /tools/convert. Extra rules live in the validators."""

    model_config = ConfigDict(populate_by_name=True)

    amount: Decimal
    from_: str = Field(alias="from")
    to: str
    date: date

    @field_validator("amount", mode="before")
    @classmethod
    def validate_amount(cls, raw: object) -> Decimal:
        if raw is None or (isinstance(raw, str) and raw.strip() == ""):
            raise PydanticCustomError(
                "invalid_amount",
                "amount is required and must be a number greater than zero.",
            )
        text = raw.strip() if isinstance(raw, str) else str(raw)
        try:
            value = Decimal(text)
        except InvalidOperation as exc:
            raise PydanticCustomError(
                "invalid_amount",
                "amount must be a number greater than zero.",
            ) from exc
        if not value.is_finite() or value <= 0:
            raise PydanticCustomError(
                "invalid_amount",
                "amount must be a number greater than zero.",
            )
        exponent = value.as_tuple().exponent
        max_decimals = max_amount_decimals()
        if isinstance(exponent, int) and exponent < -max_decimals:
            raise PydanticCustomError(
                "invalid_amount",
                f"amount can have at most {max_decimals} decimal places.",
            )
        return value

    @field_validator("from_", "to", mode="before")
    @classmethod
    def validate_currency(cls, raw: object, info: ValidationInfo) -> str:
        field = "from" if info.field_name == "from_" else "to"
        if raw is None or (isinstance(raw, str) and raw.strip() == ""):
            raise PydanticCustomError(
                "invalid_currency",
                f"{field} is required and must be a 3-letter currency code.",
            )
        code = str(raw).strip().upper()
        if len(code) != 3 or not code.isalpha():
            raise PydanticCustomError(
                "invalid_currency",
                f"{field} must be a 3-letter currency code.",
            )
        return code

    @field_validator("date", mode="before")
    @classmethod
    def validate_date(cls, raw: object) -> date:
        if raw is None or (isinstance(raw, str) and raw.strip() == ""):
            raise PydanticCustomError(
                "invalid_date",
                "date is required and must be YYYY-MM-DD.",
            )
        text = raw.strip() if isinstance(raw, str) else str(raw)
        try:
            asked = date.fromisoformat(text)
        except ValueError as exc:
            raise PydanticCustomError(
                "invalid_date",
                "date must be YYYY-MM-DD.",
            ) from exc
        today = datetime.now(timezone.utc).date()
        if asked > today:
            raise PydanticCustomError("date_in_future", "date cannot be in the future.")
        series_start = series_start_date()
        if asked < series_start:
            raise PydanticCustomError(
                "date_out_of_range",
                f"date is before the ECB euro series starts ({series_start.isoformat()}).",
            )
        return asked

    @field_validator("to")
    @classmethod
    def currencies_differ(cls, to_code: str, info: ValidationInfo) -> str:
        from_code = info.data.get("from_")
        if from_code is not None and from_code == to_code:
            raise PydanticCustomError(
                "same_currency",
                "from and to must be different currency codes.",
            )
        return to_code


def convert_error_from_validation(exc: ValidationError) -> ConvertError:
    err = exc.errors()[0]
    code = str(err.get("type") or "")
    message = str(err.get("msg") or "the request was not valid.")
    known = {
        "invalid_amount",
        "invalid_currency",
        "invalid_date",
        "date_in_future",
        "date_out_of_range",
        "same_currency",
    }
    if code in known:
        return ConvertError(400, code, message)
    loc = err.get("loc") or ()
    field = loc[0] if loc else ""
    if field == "amount":
        return ConvertError(400, "invalid_amount", message)
    if field in ("from", "from_", "to"):
        return ConvertError(400, "invalid_currency", message)
    if field == "date":
        return ConvertError(400, "invalid_date", message)
    return ConvertError(400, "invalid_amount", message)


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
    # Dates we cannot serve are already rejected in ConvertQuery, and
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
        with httpx.Client(
            timeout=timeout_seconds(),
            transport=_transport,
        ) as client:
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
        query = ConvertQuery.model_validate(
            {"amount": amount, "from": from_, "to": to, "date": date}
        )
        rate, rate_date = get_rate(query.from_, query.to, query.date)
    except ValidationError as exc:
        return error_body(convert_error_from_validation(exc))
    except ConvertError as exc:
        return error_body(exc)

    amount_number = float(query.amount)
    # Multiply only. The rate is used as published; we do not round it first.
    result = round(amount_number * rate, 2)
    return JSONResponse(
        {
            "amount": amount_number,
            "from": query.from_,
            "to": query.to,
            "rate": rate,
            "result": result,
            "rate_date": rate_date,
            "asked_date": query.date.isoformat(),
            "source": source_label(),
        }
    )
