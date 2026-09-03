"""HTTP currency convert tool for an AI agent."""

from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse

# The application object uvicorn loads from this file.
app = FastAPI(title="fx-convert")

SOURCE = "ECB via frankfurter.dev"
MAX_AMOUNT_DECIMALS = 6
ECB_SERIES_START = date(1999, 1, 4)


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
    except ConvertError as exc:
        return error_body(exc)

    return JSONResponse(
        {
            "amount": float(parsed_amount),
            "from": from_code,
            "to": to_code,
            "rate": None,
            "result": None,
            "rate_date": None,
            "asked_date": asked.isoformat(),
            "source": SOURCE,
        }
    )
