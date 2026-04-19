import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import patch, MagicMock
from insights_client import generate_insights


def _sample_report():
    return {
        "last_week": {
            "sales": {"gross_revenue": 3214.50, "total_orders": 47},
            "klaviyo": {"campaigns_sent": 2, "attributed_revenue": 412.0},
            "ads": {"blended_roas": 3.4},
        },
        "mtd": {"sales": {"gross_revenue": 6180.0}},
    }


def test_generate_insights_happy_path():
    fake_response = MagicMock()
    fake_response.content = [MagicMock(text='["Bullet one.", "Bullet two.", "Bullet three."]')]
    with patch("insights_client.Anthropic") as cls:
        cls.return_value.messages.create.return_value = fake_response
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "fake"}):
            result = generate_insights(_sample_report())
    assert result["bullets"] == ["Bullet one.", "Bullet two.", "Bullet three."]
    assert result["errors"] == []


def test_generate_insights_strips_markdown_fences():
    fake_response = MagicMock()
    fake_response.content = [MagicMock(text='```json\n["A", "B", "C"]\n```')]
    with patch("insights_client.Anthropic") as cls:
        cls.return_value.messages.create.return_value = fake_response
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "fake"}):
            result = generate_insights(_sample_report())
    assert result["bullets"] == ["A", "B", "C"]


def test_generate_insights_no_api_key():
    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}):
        result = generate_insights(_sample_report())
    assert result["bullets"] == []
    assert "ANTHROPIC_API_KEY" in result["errors"][0]


def test_generate_insights_api_error_captures_message():
    with patch("insights_client.Anthropic") as cls:
        cls.return_value.messages.create.side_effect = RuntimeError("boom")
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "fake"}):
            result = generate_insights(_sample_report())
    assert result["bullets"] == []
    assert "RuntimeError" in result["errors"][0]
    assert "boom" in result["errors"][0]


def test_generate_insights_malformed_json_captures_preview():
    fake_response = MagicMock()
    fake_response.content = [MagicMock(text="this is not json at all")]
    with patch("insights_client.Anthropic") as cls:
        cls.return_value.messages.create.return_value = fake_response
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "fake"}):
            result = generate_insights(_sample_report())
    assert result["bullets"] == []
    assert "parse error" in result["errors"][0]
    assert "this is not json" in result["errors"][0]
