"""Offline tests for GET /tools/convert. The upstream is always faked."""

from datetime import date, datetime, timezone

import httpx

import app as app_module
from tests.conftest import frankfurter_ok, install_upstream


def convert(api, amount=None, from_code=None, to=None, date=None):
    params = {}
    if amount is not None:
        params["amount"] = amount
    if from_code is not None:
        params["from"] = from_code
    if to is not None:
        params["to"] = to
    if date is not None:
        params["date"] = date
    return api.get("/tools/convert", params=params)


def test_success_uses_upstream_date(api):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        assert request.url.host == "fx-upstream.test"
        assert request.url.path == "/v1/2026-08-28"
        return httpx.Response(200, json=frankfurter_ok())

    install_upstream(handler)
    response = convert(api, amount="250", from_code="EUR", to="TRY", date="2026-08-28")

    assert response.status_code == 200
    body = response.json()
    assert body["amount"] == 250.0
    assert body["from"] == "EUR"
    assert body["to"] == "TRY"
    assert body["rate"] == 47.1234
    assert body["result"] == 11780.85
    assert body["rate_date"] == "2026-08-28"
    assert body["asked_date"] == "2026-08-28"
    assert body["source"] == "ECB via frankfurter.dev"
    assert calls["n"] == 1


def test_weekend_shows_published_rate_date(api):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/2026-08-29"
        return httpx.Response(200, json=frankfurter_ok(rate=47.1, rate_date="2026-08-28"))

    install_upstream(handler)
    response = convert(api, amount="10", from_code="eur", to="try", date="2026-08-29")

    assert response.status_code == 200
    body = response.json()
    assert body["asked_date"] == "2026-08-29"
    assert body["rate_date"] == "2026-08-28"
    assert body["rate"] == 47.1
    assert body["result"] == 471.0


def test_repeat_question_does_not_call_upstream_again(api):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json=frankfurter_ok())

    install_upstream(handler)
    first = convert(api, amount="250", from_code="EUR", to="TRY", date="2026-08-28")
    second = convert(api, amount="250", from_code="EUR", to="TRY", date="2026-08-28")

    assert first.status_code == 200
    assert second.json() == first.json()
    assert calls["n"] == 1


def test_same_pair_and_date_reuses_rate_for_other_amount(api):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json=frankfurter_ok())

    install_upstream(handler)
    convert(api, amount="100", from_code="EUR", to="TRY", date="2026-08-28")
    second = convert(api, amount="200", from_code="EUR", to="TRY", date="2026-08-28")

    assert second.json()["result"] == 9424.68
    assert calls["n"] == 1


def test_different_asked_date_calls_upstream_again(api):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        asked = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(200, json=frankfurter_ok(rate_date=asked))

    install_upstream(handler)
    convert(api, amount="1", from_code="EUR", to="TRY", date="2026-08-27")
    convert(api, amount="1", from_code="EUR", to="TRY", date="2026-08-28")
    assert calls["n"] == 2


def test_missing_amount(api):
    response = convert(api, from_code="EUR", to="TRY", date="2026-08-28")
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_amount"


def test_zero_amount(api):
    response = convert(api, amount="0", from_code="EUR", to="TRY", date="2026-08-28")
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_amount"


def test_negative_amount(api):
    response = convert(api, amount="-5", from_code="EUR", to="TRY", date="2026-08-28")
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_amount"


def test_ten_decimal_places_rejected(api):
    response = convert(
        api, amount="1.1234567890", from_code="EUR", to="TRY", date="2026-08-28"
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_amount"


def test_bad_currency_format(api):
    response = convert(api, amount="1", from_code="EURO", to="TRY", date="2026-08-28")
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_currency"


def test_same_currency_does_not_call_upstream(api):
    response = convert(api, amount="10", from_code="EUR", to="EUR", date="2026-08-28")
    assert response.status_code == 400
    assert response.json()["error"] == "same_currency"
    assert app_module._transport is None


def test_invalid_date(api):
    response = convert(api, amount="1", from_code="EUR", to="TRY", date="28-08-2026")
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_date"


def test_future_date(api):
    tomorrow = date.fromordinal(datetime.now(timezone.utc).date().toordinal() + 1)
    response = convert(
        api, amount="1", from_code="EUR", to="TRY", date=tomorrow.isoformat()
    )
    assert response.status_code == 400
    assert response.json()["error"] == "date_in_future"


def test_date_before_ecb_series(api):
    response = convert(api, amount="1", from_code="EUR", to="TRY", date="1999-01-03")
    assert response.status_code == 400
    assert response.json()["error"] == "date_out_of_range"


def test_unknown_currency_404(api):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "not found"})

    install_upstream(handler)
    response = convert(api, amount="1", from_code="XXX", to="TRY", date="2026-08-28")
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_currency"


def test_upstream_500(api):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"message": "nope"})

    install_upstream(handler)
    response = convert(api, amount="1", from_code="EUR", to="TRY", date="2026-08-28")
    assert response.status_code == 502
    assert response.json()["error"] == "upstream_error"


def test_upstream_non_json(api):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>nope</html>")

    install_upstream(handler)
    response = convert(api, amount="1", from_code="EUR", to="TRY", date="2026-08-28")
    assert response.status_code == 502
    assert response.json()["error"] == "upstream_error"


def test_upstream_timeout(api):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("slow")

    install_upstream(handler)
    response = convert(api, amount="1", from_code="EUR", to="TRY", date="2026-08-28")
    assert response.status_code == 504
    assert response.json()["error"] == "upstream_timeout"


def test_failures_are_not_cached(api):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(500, json={"message": "nope"})

    install_upstream(handler)
    convert(api, amount="1", from_code="EUR", to="TRY", date="2026-08-28")
    convert(api, amount="1", from_code="EUR", to="TRY", date="2026-08-28")
    assert calls["n"] == 2
