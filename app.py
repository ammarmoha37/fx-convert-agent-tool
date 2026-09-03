"""HTTP currency convert tool for an AI agent."""

from fastapi import FastAPI, Query

# The application object uvicorn loads from this file.
app = FastAPI(title="fx-convert")

SOURCE = "ECB via frankfurter.dev"


@app.get("/tools/convert")
def convert(
    amount: float = Query(),
    from_: str = Query(alias="from"),
    to: str = Query(),
    date: str = Query(),
) -> dict:
    return {
        "amount": amount,
        "from": from_,
        "to": to,
        "rate": None,
        "result": None,
        "rate_date": None,
        "asked_date": date,
        "source": SOURCE,
    }
