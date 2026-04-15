import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from api_server import normalize_shopify_order, normalize_amazon_order


def test_shopify_order_includes_shipping():
    fees = {
        "shopify_fee":        2,
        "stripe_pct":         2.9,
        "stripe_fixed":       0.30,
        "cogs_per_unit":      18.0,
        "shipping_per_order": 8.0,
    }
    raw = {
        "total_price":      "50.00",
        "order_number":     "1001",
        "id":               "1001",
        "created_at":       "2026-04-14T10:00:00Z",
        "financial_status": "paid",
        "billing_address":  {},
        "line_items":       [],
        "fulfillments":     [],
    }
    result = normalize_shopify_order(raw, fees)
    assert result["shipping"] == 8.0
    assert result["total_fees"] == round(
        result["platform_fee"] + result["stripe_fee"] + result["cogs"] + 8.0, 2)
    assert result["net"] == round(50.0 - result["total_fees"], 2)


def test_amazon_order_includes_shipping():
    fees = {
        "amazon_fee":         15,
        "cogs_per_unit":      18.0,
        "shipping_per_order": 8.0,
    }
    raw = {
        "AmazonOrderId":        "111-1234567-1234567",
        "PurchaseDate":         "2026-04-14T10:00:00Z",
        "OrderStatus":          "Shipped",
        "OrderTotal":           {"Amount": "50.00"},
        "NumberOfItemsShipped": 1,
    }
    result = normalize_amazon_order(raw, fees)
    assert result["shipping"] == 8.0
    assert result["total_fees"] == round(
        result["platform_fee"] + result["cogs"] + 8.0, 2)
    assert result["net"] == round(50.0 - result["total_fees"], 2)
