import sys, os, datetime
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import patch
from api_server import _compute_summary_range, app
from weekly_periods import ET


def test_compute_summary_range_no_credentials_returns_zeros():
    start = datetime.datetime(2026, 4, 6, 0, 0, 0, tzinfo=ET)
    end = datetime.datetime(2026, 4, 12, 23, 59, 59, tzinfo=ET)
    env = {k: "" for k in [
        "SHOPIFY_STORE", "SHOPIFY_TOKEN",
        "AMAZON_CLIENT_ID", "AMAZON_CLIENT_SECRET", "AMAZON_REFRESH_TOKEN",
    ]}
    with patch.dict(os.environ, env, clear=False):
        with app.test_request_context():
            result = _compute_summary_range(start, end)
    assert result["total_orders"] == 0
    assert result["gross_revenue"] == 0
    assert result["period_start"] == start.isoformat()
    assert result["period_end"] == end.isoformat()
    assert "orders" in result  # new field exposing raw orders list
    assert result["orders"] == []
