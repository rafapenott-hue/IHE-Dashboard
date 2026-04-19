import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import patch, MagicMock


def test_weekly_endpoint_no_secret():
    from api_server import app
    with patch.dict(os.environ, {"DIGEST_SECRET": "abc123"}):
        client = app.test_client()
        resp = client.post("/api/digest/weekly")
        assert resp.status_code == 401


def test_weekly_endpoint_wrong_secret():
    from api_server import app
    with patch.dict(os.environ, {"DIGEST_SECRET": "abc123"}):
        client = app.test_client()
        resp = client.post("/api/digest/weekly?secret=wrong")
        assert resp.status_code == 401


def test_weekly_endpoint_not_configured():
    from api_server import app
    with patch.dict(os.environ, {"DIGEST_SECRET": ""}):
        client = app.test_client()
        resp = client.post("/api/digest/weekly")
        assert resp.status_code == 503


def test_weekly_endpoint_happy_path_calls_telegram():
    from api_server import app

    fake_report = {
        "now_et_iso": "2026-04-14T07:00:00-04:00",
        "last_week": {
            "period_label": "Apr 7–13",
            "sales": {"total_orders": 5, "shopify_orders": 3, "amazon_orders": 2,
                      "gross_revenue": 500.0, "net_revenue": 180.0,
                      "net_margin": 36.0, "avg_order": 100.0, "orders": []},
            "top_product": ("", 0), "top_states": [], "wow_gross_pct": 0,
            "klaviyo": {"campaigns_sent": 0, "open_rate": 0, "click_rate": 0,
                        "attributed_revenue": 0, "new_subscribers": 0,
                        "unsubscribes": 0, "errors": []},
            "ads": {"google": {"spend": 0, "revenue": 0, "roas": 0},
                    "meta":   {"spend": 0, "revenue": 0, "roas": 0},
                    "blended_roas": 0, "errors": []},
        },
        "mtd": {
            "period_label": "Apr 1–14",
            "sales": {"total_orders": 10, "gross_revenue": 1000.0,
                      "net_revenue": 360.0, "net_margin": 36.0, "orders": []},
            "ads":  {"google": {"spend": 0, "revenue": 0}, "meta": {"spend": 0, "revenue": 0},
                     "blended_roas": 0, "errors": []},
        },
        "insights": ["Test bullet."],
        "errors": [],
    }

    env = {"DIGEST_SECRET": "abc123",
           "TELEGRAM_BOT_TOKEN": "tok", "TELEGRAM_CHAT_ID": "chat"}
    with patch.dict(os.environ, env), \
         patch("api_server.build_weekly_report", return_value=fake_report), \
         patch("api_server.send_telegram") as send:
        client = app.test_client()
        resp = client.post("/api/digest/weekly?secret=abc123")

    assert resp.status_code == 200
    send.assert_called_once()
    args, _ = send.call_args
    assert "IHE Weekly" in args[2]
