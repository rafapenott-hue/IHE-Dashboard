# Shipping Cost Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deduct a flat $8.00 shipping cost from every order (Shopify and Amazon) so net profit and margin calculations are accurate.

**Architecture:** One constant added to `fee_cfg()`, then threaded through `normalize_shopify_order` and `normalize_amazon_order` into `total_fees` and `net`. The `/api/summary` endpoint gains a `shipping` total. Frontend already reads `o.shipping` — no HTML/JS changes needed. Config exposed via `SHIPPING_PER_ORDER` env var in `render.yaml`.

**Tech Stack:** Python 3.9, Flask; pytest; render.yaml

---

## File Map

| File | Change |
|---|---|
| `api_server.py` | `fee_cfg()` + `normalize_shopify_order` + `normalize_amazon_order` + `/api/summary` |
| `tests/test_financials.py` | New file — two tests covering Shopify and Amazon shipping |
| `render.yaml` | Add `SHIPPING_PER_ORDER=8` |

---

## Task 1: Write failing tests

**Files:**
- Create: `tests/test_financials.py`

- [ ] **Step 1: Create `tests/test_financials.py`**

```python
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from api_server import normalize_shopify_order, normalize_amazon_order


def test_shopify_order_includes_shipping():
    fees = {
        "shopify_fee":      2,
        "stripe_pct":       2.9,
        "stripe_fixed":     0.30,
        "cogs_per_unit":    18.0,
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
        "amazon_fee":       15,
        "cogs_per_unit":    18.0,
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
```

- [ ] **Step 2: Run tests — expect failures**

```bash
cd /Users/rafaelpenott/IHE-Dashboard/src
python3 -m pytest tests/test_financials.py -v
```

Expected: 2 FAILED — `KeyError: 'shipping_per_order'` (fee_cfg doesn't have the key yet) or `KeyError: 'shipping'` (result dict doesn't have it yet).

---

## Task 2: Implement shipping in backend

**Files:**
- Modify: `api_server.py:356-363` (`fee_cfg`)
- Modify: `api_server.py:399-448` (`normalize_shopify_order`)
- Modify: `api_server.py:554-600` (`normalize_amazon_order`)
- Modify: `api_server.py:725-748` (`/api/summary` totals)

- [ ] **Step 1: Add `shipping_per_order` to `fee_cfg()`**

Find `fee_cfg()` (lines 356–363). The current last line is:
```python
        "cogs_per_unit": float(os.environ.get("COGS_PER_UNIT",    "18")),  # fallback only
```

Add one line after it (before the closing `}`):
```python
        "shipping_per_order": float(os.environ.get("SHIPPING_PER_ORDER", "8.00")),
```

Result:
```python
def fee_cfg() -> dict:
    return {
        "amazon_fee":         float(os.environ.get("AMAZON_FEE_PCT",    "15")),
        "shopify_fee":        float(os.environ.get("SHOPIFY_FEE_PCT",    "2")),
        "stripe_pct":         float(os.environ.get("STRIPE_FEE_PCT",   "2.9")),
        "stripe_fixed":       float(os.environ.get("STRIPE_FIXED_FEE", "0.30")),
        "cogs_per_unit":      float(os.environ.get("COGS_PER_UNIT",    "18")),  # fallback only
        "shipping_per_order": float(os.environ.get("SHIPPING_PER_ORDER", "8.00")),
    }
```

- [ ] **Step 2: Update `normalize_shopify_order`**

In `normalize_shopify_order` (lines 399–448), find these two lines:
```python
    platform_fee = gross * (fees["shopify_fee"] / 100)
    stripe_fee   = gross * (fees["stripe_pct"] / 100) + fees["stripe_fixed"]
    total_fees   = platform_fee + stripe_fee + cogs
```

Replace with:
```python
    platform_fee = gross * (fees["shopify_fee"] / 100)
    stripe_fee   = gross * (fees["stripe_pct"] / 100) + fees["stripe_fixed"]
    shipping     = fees["shipping_per_order"]
    total_fees   = platform_fee + stripe_fee + cogs + shipping
```

Then in the `return { ... }` dict, find:
```python
        "cogs":         round(cogs, 2),
        "total_fees":   round(total_fees, 2),
```

Add `"shipping"` between them:
```python
        "cogs":         round(cogs, 2),
        "shipping":     round(shipping, 2),
        "total_fees":   round(total_fees, 2),
```

- [ ] **Step 3: Update `normalize_amazon_order`**

In `normalize_amazon_order` (lines 554–600), find:
```python
    amazon_fee  = gross * (fees["amazon_fee"] / 100)
    total_fees  = amazon_fee + cogs
```

Replace with:
```python
    amazon_fee  = gross * (fees["amazon_fee"] / 100)
    shipping    = fees["shipping_per_order"]
    total_fees  = amazon_fee + cogs + shipping
```

In the `return { ... }` dict, find:
```python
        "cogs":         cogs,
        "total_fees":   round(total_fees, 2),
```

Add `"shipping"` between them:
```python
        "cogs":         cogs,
        "shipping":     round(shipping, 2),
        "total_fees":   round(total_fees, 2),
```

- [ ] **Step 4: Update `/api/summary` totals**

In the `totals` dict inside `/api/summary` (around line 725–743), find:
```python
        "cogs":           round(sum(o["cogs"]         for o in orders), 2),
        "total_fees":     round(sum(o["total_fees"]   for o in orders), 2),
```

Add `"shipping"` between them:
```python
        "cogs":           round(sum(o["cogs"]           for o in orders), 2),
        "shipping":       round(sum(o.get("shipping", 0) for o in orders), 2),
        "total_fees":     round(sum(o["total_fees"]     for o in orders), 2),
```

- [ ] **Step 5: Run tests — expect passing**

```bash
cd /Users/rafaelpenott/IHE-Dashboard/src
python3 -m pytest tests/test_financials.py tests/test_tracking.py -v
```

Expected: 24 PASS, 0 FAIL (2 new + 22 existing).

- [ ] **Step 6: Commit**

```bash
cd /Users/rafaelpenott/IHE-Dashboard/src
git add api_server.py tests/test_financials.py
git commit -m "feat: add $8 flat shipping cost per order to net profit calculation"
```

---

## Task 3: Add env var to render.yaml and push

**Files:**
- Modify: `render.yaml`

- [ ] **Step 1: Add `SHIPPING_PER_ORDER` to `render.yaml`**

Find the block with `COGS_PER_UNIT` in `render.yaml`. It looks like:
```yaml
      - key: COGS_PER_UNIT
        value: "18"        # update to your actual cost per unit
```

Add directly after it:
```yaml
      - key: SHIPPING_PER_ORDER
        value: "8"
```

- [ ] **Step 2: Commit**

```bash
cd /Users/rafaelpenott/IHE-Dashboard/src
git add render.yaml
git commit -m "chore: add SHIPPING_PER_ORDER env var to render.yaml"
```

- [ ] **Step 3: Push to GitHub**

```bash
cd /Users/rafaelpenott/IHE-Dashboard/src
git remote set-url origin https://<PAT>@github.com/rafapenott-hue/IHE-Dashboard.git
git push origin main
git remote set-url origin https://github.com/rafapenott-hue/IHE-Dashboard.git
```

- [ ] **Step 4: Verify on Render after deploy**

Once Render auto-deploys (~2 min), run:
```bash
curl -s "https://ihe-dashboard.onrender.com/api/shopify/orders?start=2026-04-01T00:00:00Z" \
  | python3 -c "import json,sys; orders=json.load(sys.stdin)['orders']; o=orders[0]; print('shipping:', o.get('shipping'), 'total_fees:', o['total_fees'], 'net:', o['net'])"
```

Expected: `shipping: 8.0` in the response, and `total_fees` is 8.00 higher than before, `net` is 8.00 lower.
