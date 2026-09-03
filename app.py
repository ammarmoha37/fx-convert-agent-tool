"""HTTP currency convert tool for an AI agent."""

from fastapi import FastAPI
from fastapi.responses import JSONResponse

# The application object uvicorn loads from this file.
app = FastAPI(title="fx-convert")


@app.get("/tools/convert")
def convert() -> JSONResponse:
    # Response for GET /tools/convert.
    return JSONResponse(
        status_code=501,
        content={
            "error": "not_implemented",
            "message": "convert is not implemented yet.",
        },
    )
