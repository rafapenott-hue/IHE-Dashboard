import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from api_server import build_digest_message


def test_build_digest_message_normal():
    totals = {
        "period_start":   "2026-04-13T00:00:00-04:00",
        "total_orders":   12,
        "shopify_orders": 8,
        "amazon_orders":  4,
        "gross_revenue":  847.50,
        "net_revenue":    312.40,
        "net_margin":     36.9,
        "avg_order":      70.63,
    }
    msg = build_digest_message(totals)
    assert "Mon Apr 13" in msg
    assert "Orders: 12" in msg
    assert "8 Shopify · 4 Amazon" in msg
    assert "$847.50" in msg
    assert "36.9%" in msg
    assert "$70.63" in msg


def test_build_digest_message_zero_orders():
    totals = {
        "period_start":   "2026-04-13T00:00:00-04:00",
        "total_orders":   0,
        "shopify_orders": 0,
        "amazon_orders":  0,
        "gross_revenue":  0,
        "net_revenue":    0,
        "net_margin":     0,
        "avg_order":      0,
    }
    msg = build_digest_message(totals)
    assert "No orders yesterday" in msg
    assert "Mon Apr 13" in msg


from unittest.mock import patch


def test_digest_endpoint_no_secret():
    from api_server import app
    with patch.dict(os.environ, {"DIGEST_SECRET": "abc123"}):
        client = app.test_client()
        resp = client.post("/api/digest")
        assert resp.status_code == 401


def test_digest_endpoint_wrong_secret():
    from api_server import app
    with patch.dict(os.environ, {"DIGEST_SECRET": "abc123"}):
        client = app.test_client()
        resp = client.post("/api/digest?secret=wrong")
        assert resp.status_code == 401


def test_digest_endpoint_not_configured():
    from api_server import app
    env = {"DIGEST_SECRET": "", "TELEGRAM_BOT_TOKEN": "", "TELEGRAM_CHAT_ID": ""}
    with patch.dict(os.environ, env):
        client = app.test_client()
        resp = client.post("/api/digest")
        assert resp.status_code == 503
