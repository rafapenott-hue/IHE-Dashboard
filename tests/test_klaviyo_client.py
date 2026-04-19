import sys, os, datetime
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import patch, MagicMock
from klaviyo_client import fetch_weekly_metrics


def _dt(d): return datetime.datetime.fromisoformat(d)


def _mk(status=200, payload=None, text=""):
    r = MagicMock(ok=(status < 400), status_code=status, text=text)
    r.json.return_value = payload or {}
    return r


def test_fetch_weekly_metrics_no_api_key():
    result = fetch_weekly_metrics(
        "",
        _dt("2026-04-06T00:00:00-04:00"),
        _dt("2026-04-12T23:59:59-04:00"),
    )
    assert result["campaigns_sent"] == 0
    assert result["errors"] == ["no API key configured"]


def test_fetch_weekly_metrics_happy_path():
    # Order of calls in the client:
    # 1. GET /campaigns (sent campaigns in window)
    # 2. GET /metrics (find Placed Order id)
    # 3. POST /campaign-values-reports
    # 4. GET /metrics (find Subscribed to List id) — cached in real Klaviyo, but separate call here
    # 5. POST /metric-aggregates (subs)
    # 6. GET /metrics (find Unsubscribed from Email Marketing id)
    # 7. POST /metric-aggregates (unsubs)
    campaigns = _mk(payload={"data": [
        {"id": "c1", "attributes": {"status": "Sent"}},
        {"id": "c2", "attributes": {"status": "Sent"}},
        {"id": "c3", "attributes": {"status": "Draft"}},
    ]})
    metrics_list = _mk(payload={"data": [
        {"id": "M_ORDER",  "attributes": {"name": "Placed Order"}},
        {"id": "M_SUBS",   "attributes": {"name": "Subscribed to List"}},
        {"id": "M_UNSUBS", "attributes": {"name": "Unsubscribed from Email Marketing"}},
    ]})
    report = _mk(payload={"data": {"attributes": {"results": [
        {"statistics": {"opens": 285, "clicks": 37, "conversions": 1,
                        "conversion_value": 29.75, "delivered": 1505}},
    ]}}})
    subs_agg = _mk(payload={"data": {"attributes": {"data": [
        {"measurements": {"count": [23]}},
    ]}}})
    unsubs_agg = _mk(payload={"data": {"attributes": {"data": [
        {"measurements": {"count": [4]}},
    ]}}})

    with patch("klaviyo_client.requests.get",
               side_effect=[campaigns, metrics_list, metrics_list, metrics_list]), \
         patch("klaviyo_client.requests.post",
               side_effect=[report, subs_agg, unsubs_agg]):
        result = fetch_weekly_metrics(
            "fake-key",
            _dt("2026-04-06T00:00:00-04:00"),
            _dt("2026-04-12T23:59:59-04:00"),
        )

    assert result["campaigns_sent"] == 2
    assert result["open_rate"] == round(285 / 1505 * 100, 1)
    assert result["click_rate"] == round(37 / 1505 * 100, 1)
    assert result["attributed_revenue"] == 29.75
    assert result["new_subscribers"] == 23
    assert result["unsubscribes"] == 4
    assert result["errors"] == []


def test_fetch_weekly_metrics_campaigns_error_captures_message():
    fail = _mk(status=400, text='{"errors":[{"detail":"send_time is not filterable"}]}')
    with patch("klaviyo_client.requests.get", return_value=fail), \
         patch("klaviyo_client.requests.post", return_value=fail):
        result = fetch_weekly_metrics(
            "fake-key",
            _dt("2026-04-06T00:00:00-04:00"),
            _dt("2026-04-12T23:59:59-04:00"),
        )
    assert result["campaigns_sent"] == 0
    assert any("campaigns" in e and "400" in e for e in result["errors"])
