# IHE Dashboard — Shipping Cost Fix

**Date:** 2026-04-14
**Status:** Approved

## Goal

Deduct a flat $8.00 shipping cost from every order (Shopify and Amazon) so that net profit and margin calculations are accurate.

## Current Problem

`normalize_shopify_order` and `normalize_amazon_order` do not include any shipping cost in `total_fees` or `net`. The result dict returns `shipping: 0` implicitly (the field is referenced in the frontend but always zero). Every order's net profit is overstated by $8.

## Design

### Backend — `api_server.py`

**`fee_cfg()`** — add one entry:

```python
"shipping_per_order": float(os.environ.get("SHIPPING_PER_ORDER", "8.00")),
```

**`normalize_shopify_order(o, fees)`**:

```python
shipping     = fees["shipping_per_order"]
total_fees   = platform_fee + stripe_fee + cogs + shipping
```

Add to result dict:
```python
"shipping": round(shipping, 2),
```

`net` = `gross - total_fees` remains unchanged (now correctly includes shipping).

**`normalize_amazon_order(o, fees, order_items)`**:

```python
shipping    = fees["shipping_per_order"]
total_fees  = amazon_fee + cogs + shipping
```

Add to result dict:
```python
"shipping": round(shipping, 2),
```

**`/api/summary`** — add to `totals` dict:

```python
"shipping": round(sum(o.get("shipping", 0) for o in orders), 2),
```

### Configuration — `render.yaml`

Add non-secret env var:

```yaml
- key: SHIPPING_PER_ORDER
  value: "8"
```

### Frontend — `dashboard.html`

No changes needed. The frontend already reads `o.shipping` in `aggregate()` (line ~1036) and shows a Shipping row in the orders table and waterfall chart. Once the backend sends `shipping: 8.0`, those cells populate automatically.

### Tests — `tests/test_tracking.py` → new file `tests/test_financials.py`

Two new tests:

```python
def test_shopify_order_includes_shipping():
    fees = {"shopify_fee": 2, "stripe_pct": 2.9, "stripe_fixed": 0.30,
            "cogs_per_unit": 18.0, "shipping_per_order": 8.0}
    raw = {"total_price": "50.00", "order_number": "1001", "id": "1001",
           "created_at": "2026-04-14T10:00:00Z", "financial_status": "paid",
           "billing_address": {}, "line_items": [], "fulfillments": []}
    result = normalize_shopify_order(raw, fees)
    assert result["shipping"] == 8.0
    assert result["total_fees"] == round(result["platform_fee"] + result["stripe_fee"]
                                         + result["cogs"] + 8.0, 2)
    assert result["net"] == round(50.0 - result["total_fees"], 2)

def test_amazon_order_includes_shipping():
    fees = {"amazon_fee": 15, "cogs_per_unit": 18.0, "shipping_per_order": 8.0}
    raw = {"AmazonOrderId": "111-1234567-1234567", "PurchaseDate": "2026-04-14T10:00:00Z",
           "OrderStatus": "Shipped", "OrderTotal": {"Amount": "50.00"},
           "NumberOfItemsShipped": 1}
    result = normalize_amazon_order(raw, fees)
    assert result["shipping"] == 8.0
    assert result["total_fees"] == round(result["platform_fee"] + result["cogs"] + 8.0, 2)
    assert result["net"] == round(50.0 - result["total_fees"], 2)
```

## Error Handling

- `SHIPPING_PER_ORDER` env var missing → defaults to `8.00`
- `o.get("shipping", 0)` in summary → safe for any orders missing the field

## Out of Scope

- Per-order actual shipping cost from Shopify `shipping_lines`
- Per-carrier shipping rate variations
- Amazon shipping cost from SP-API
