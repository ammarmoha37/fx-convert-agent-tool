"""Shared test setup. FX_UPSTREAM_BASE is a fake host, never the real API."""

import os

import httpx
import pytest
from fastapi.testclient import TestClient

os.environ["FX_UPSTREAM_BASE"] = "http://fx-upstream.test"

import app as app_module  # noqa: E402


@pytest.fixture
def api() -> TestClient:
    app_module._cache.clear()
    app_module._transport = None
    with TestClient(app_module.app) as client:
        yield client
    app_module._cache.clear()
    app_module._transport = None


def install_upstream(handler):
    """Serve canned Frankfurter responses. No sockets are opened."""
    app_module._transport = httpx.MockTransport(handler)


def frankfurter_ok(rate: float = 47.1234, rate_date: str = "2026-08-28"):
    return {
        "amount": 1.0,
        "base": "EUR",
        "date": rate_date,
        "rates": {"TRY": rate},
    }
