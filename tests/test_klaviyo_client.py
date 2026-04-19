import sys, os, datetime
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import patch, MagicMock
from klaviyo_client import fetch_weekly_metrics


def _dt(d): return datetime.datetime.fromisoformat(d)


def test_fetch_weekly_metrics_happy_path():
    resp_campaigns = MagicMock(ok=True, status_code=200)
    resp_campaigns.json.return_value = {"data": [{"id": "c1"}, {"id": "c2"}]}

    resp_reports = MagicMock(ok=True, status_code=200)
    resp_reports.json.return_value = {"data": [{
        "attributes": {"statistics": {
            "open_rate": 0.284, "click_rate": 0.031, "revenue": 412.00
        }}
    }]}

    resp_subs = MagicMock(ok=True, status_code=200)
    resp_subs.json.return_value = {"data": [{}]*23}

    resp_unsubs = MagicMock(ok=True, status_code=200)
    resp_unsubs.json.return_value = {"data": [{}]*4}

    with patch("klaviyo_client.requests.get",
               side_effect=[resp_campaigns, resp_reports, resp_subs, resp_unsubs]):
        result = fetch_weekly_metrics(
            "fake-key",
            _dt("2026-04-06T00:00:00-04:00"),
            _dt("2026-04-12T23:59:59-04:00"),
        )

    assert result["campaigns_sent"] == 2
    assert result["open_rate"] == 28.4
    assert result["click_rate"] == 3.1
    assert result["attributed_revenue"] == 412.00
    assert result["new_subscribers"] == 23
    assert result["unsubscribes"] == 4
    assert result["errors"] == []


def test_fetch_weekly_metrics_api_error_returns_zeros():
    resp_fail = MagicMock(ok=False, status_code=500, text="server error")
    with patch("klaviyo_client.requests.get", return_value=resp_fail):
        result = fetch_weekly_metrics(
            "fake-key",
            _dt("2026-04-06T00:00:00-04:00"),
            _dt("2026-04-12T23:59:59-04:00"),
        )
    assert result["campaigns_sent"] == 0
    assert result["open_rate"] == 0
    assert len(result["errors"]) > 0


def test_fetch_weekly_metrics_no_api_key():
    result = fetch_weekly_metrics(
        "",
        _dt("2026-04-06T00:00:00-04:00"),
        _dt("2026-04-12T23:59:59-04:00"),
    )
    assert result["campaigns_sent"] == 0
    assert result["errors"] == ["Klaviyo: no API key configured"]
