import sys, os, datetime
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import patch
from weekly_digest import format_weekly_message, top_product, top_states


def _sample_report():
    return {
        "now_et_iso": "2026-04-14T07:00:00-04:00",
        "last_week": {
            "period_label": "Apr 7–13",
            "sales": {
                "total_orders": 47, "shopify_orders": 32, "amazon_orders": 15,
                "gross_revenue": 3214.50, "net_revenue": 1180.20,
                "net_margin": 36.7, "avg_order": 68.39,
            },
            "top_product": ("Jamón Ibérico 3oz Sliced", 19),
            "top_states":  [("CA", 12), ("FL", 9), ("NY", 5)],
            "wow_gross_pct": 12,
            "klaviyo": {"campaigns_sent": 2, "open_rate": 28.4,
                        "click_rate": 3.1, "attributed_revenue": 412.00,
                        "new_subscribers": 23, "unsubscribes": 4, "errors": []},
            "ads": {"google": {"spend": 180, "revenue": 756, "roas": 4.2},
                    "meta":   {"spend": 240, "revenue": 672, "roas": 2.8},
                    "blended_roas": 3.4, "errors": []},
        },
        "mtd": {
            "period_label": "Apr 1–14",
            "sales": {"total_orders": 92, "gross_revenue": 6180.0,
                      "net_revenue": 2240.0, "net_margin": 36.2},
            "ads":  {"google": {"spend": 420, "revenue": 1520},
                     "meta":   {"spend": 400, "revenue": 1460},
                     "blended_roas": 3.6, "errors": []},
        },
        "insights": [
            "Gross up 12% WoW driven by the Jamón 3oz promo.",
            "Meta ROAS dropped 3.9x → 2.8x; check creative fatigue.",
            "Email attributed revenue 13% of total — below 20% target.",
        ],
    }


def test_top_product_basic():
    orders = [
        {"line_items": [{"name": "Jamón 3oz", "quantity": 2}]},
        {"line_items": [{"name": "Jamón 3oz", "quantity": 1}]},
        {"line_items": [{"name": "Olive Oil", "quantity": 1}]},
    ]
    assert top_product(orders) == ("Jamón 3oz", 2)


def test_top_states_basic():
    orders = [
        {"billing_address": {"province_code": "CA"}},
        {"billing_address": {"province_code": "CA"}},
        {"billing_address": {"province_code": "FL"}},
    ]
    result = top_states(orders, n=2)
    assert result[0] == ("CA", 2)
    assert result[1] == ("FL", 1)


def test_format_weekly_message_full():
    msg = format_weekly_message(_sample_report())
    assert "IHE Weekly" in msg
    assert "Apr 7–13" in msg
    assert "Orders:   47" in msg
    assert "$3,214.50" in msg
    assert "+12% WoW" in msg
    assert "Jamón Ibérico 3oz Sliced" in msg
    assert "CA 12" in msg
    assert "28.4% open" in msg
    assert "Google: $180" in msg
    assert "Meta:   $240" in msg
    assert "3.4x" in msg
    assert "Apr 1–14" in msg
    assert "━ Analysis ━" in msg
    assert "creative fatigue" in msg


def test_format_weekly_message_no_insights_shows_dash():
    report = _sample_report()
    report["insights"] = []
    msg = format_weekly_message(report)
    assert "━ Analysis ━" in msg
    assert "—" in msg


def test_format_weekly_message_zero_ads_shows_dash():
    report = _sample_report()
    report["last_week"]["ads"] = {
        "google": {"spend": 0, "revenue": 0, "roas": 0},
        "meta":   {"spend": 0, "revenue": 0, "roas": 0},
        "blended_roas": 0, "errors": [],
    }
    msg = format_weekly_message(report)
    assert "📣 Ads" in msg
    ads_section = msg.split("📣 Ads")[1].split("━")[0]
    assert "—" in ads_section
