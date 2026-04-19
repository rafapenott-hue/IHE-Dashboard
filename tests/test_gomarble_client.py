import sys, os, datetime
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import patch, AsyncMock
from gomarble_client import fetch_weekly_ads


def _dt(s): return datetime.datetime.fromisoformat(s)


def test_fetch_weekly_ads_no_api_key():
    result = fetch_weekly_ads(
        "",
        _dt("2026-04-06T00:00:00-04:00"),
        _dt("2026-04-12T23:59:59-04:00"),
    )
    assert result["google"]["spend"] == 0
    assert result["meta"]["spend"] == 0
    assert result["blended_roas"] == 0
    assert "no API key" in result["errors"][0]


def test_fetch_weekly_ads_happy_path():
    google_payload = {
        "rows": [{
            "metrics": {"costMicros": 180_000_000, "conversions": 12,
                        "conversionsValue": 756.0}
        }]
    }
    meta_payload = {
        "data": [{
            "spend": "240.00",
            "actions": [{"action_type": "purchase", "value": "8"}],
            "action_values": [{"action_type": "purchase", "value": "672.00"}],
        }]
    }

    async def fake_call(tool_name, args):
        if tool_name == "google_ads_list_accounts":
            return {"accounts": [{"id": "123-456-7890"}]}
        if tool_name == "facebook_list_ad_accounts":
            return {"data": [{"id": "act_987654321"}]}
        if tool_name == "google_ads_run_gaql":
            assert args.get("customer_id") == "123-456-7890"
            return google_payload
        if tool_name == "facebook_get_adaccount_insights":
            assert args.get("ad_account_id") == "act_987654321"
            return meta_payload
        raise AssertionError(f"unexpected tool {tool_name}")

    with patch("gomarble_client._call_mcp_tool", new=AsyncMock(side_effect=fake_call)):
        result = fetch_weekly_ads(
            "fake-key",
            _dt("2026-04-06T00:00:00-04:00"),
            _dt("2026-04-12T23:59:59-04:00"),
        )

    assert result["google"]["spend"] == 180.0
    assert result["google"]["revenue"] == 756.0
    assert result["google"]["roas"] == 4.2
    assert result["meta"]["spend"] == 240.0
    assert result["meta"]["revenue"] == 672.0
    assert round(result["meta"]["roas"], 1) == 2.8
    assert round(result["blended_roas"], 1) == 3.4


def test_fetch_weekly_ads_timeout_returns_zeros():
    async def fake_call(tool_name, args):
        raise TimeoutError("mcp timeout")

    with patch("gomarble_client._call_mcp_tool", new=AsyncMock(side_effect=fake_call)):
        result = fetch_weekly_ads(
            "fake-key",
            _dt("2026-04-06T00:00:00-04:00"),
            _dt("2026-04-12T23:59:59-04:00"),
        )
    assert result["google"]["spend"] == 0
    assert result["meta"]["spend"] == 0
    assert len(result["errors"]) >= 1
