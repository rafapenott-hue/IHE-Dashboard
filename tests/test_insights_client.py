import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import patch, MagicMock
from insights_client import generate_insights


def _sample_report():
    return {
        "last_week": {
            "sales": {"gross_revenue": 3214.50, "total_orders": 47,
                      "net_margin": 36.7, "avg_order": 68.39,
                      "top_product": "Jamón Ibérico 3oz Sliced",
                      "top_states": [("CA", 12), ("FL", 9), ("NY", 5)]},
            "klaviyo": {"campaigns_sent": 2, "open_rate": 28.4,
                        "click_rate": 3.1, "attributed_revenue": 412.0,
                        "new_subscribers": 23, "unsubscribes": 4},
            "ads": {"google": {"spend": 180, "revenue": 756, "roas": 4.2},
                    "meta":   {"spend": 240, "revenue": 672, "roas": 2.8},
                    "blended_roas": 3.4},
        },
        "prior_week": {"sales": {"gross_revenue": 2870.00}},
        "mtd": {"sales": {"gross_revenue": 6180.0}, "ads": {"blended_roas": 3.6}},
    }


def test_generate_insights_happy_path():
    fake_response = MagicMock()
    fake_response.content = [MagicMock(text='["Bullet one.", "Bullet two.", "Bullet three."]')]

    with patch("insights_client.Anthropic") as cls:
        inst = cls.return_value
        inst.messages.create.return_value = fake_response
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "fake"}):
            bullets = generate_insights(_sample_report())

    assert bullets == ["Bullet one.", "Bullet two.", "Bullet three."]


def test_generate_insights_no_api_key():
    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}):
        assert generate_insights(_sample_report()) == []


def test_generate_insights_api_error_returns_empty():
    with patch("insights_client.Anthropic") as cls:
        cls.return_value.messages.create.side_effect = RuntimeError("boom")
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "fake"}):
            assert generate_insights(_sample_report()) == []


def test_generate_insights_malformed_json_returns_empty():
    fake_response = MagicMock()
    fake_response.content = [MagicMock(text="this is not json")]
    with patch("insights_client.Anthropic") as cls:
        cls.return_value.messages.create.return_value = fake_response
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "fake"}):
            assert generate_insights(_sample_report()) == []
