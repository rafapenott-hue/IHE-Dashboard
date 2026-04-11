"""
Iberian Ham Express — Sales Dashboard API Server v3
=====================================================
Now supports per-SKU COGS:
  • Shopify orders  → exact COGS via Variant SKU lookup from cogs_data.json
  • Amazon orders   → exact COGS via ASIN lookup (when order items fetched)
                      or weighted-average ratio fallback (62.6% of order total)

LOCAL DEV:
    pip install -r requirements.txt
    python api_server.py

DEPLOY TO RENDER:
    Push folder to GitHub → connect in Render dashboard → reads render.yaml.
    Set secret env vars (SHOPIFY_TOKEN, AMAZON_* etc.) in Render Environment tab.

ENDPOINTS:
    GET /api/health
    GET /api/shopify/orders?start=ISO
    GET /api/amazon/orders?start=ISO
    GET /api/amazon/order-items?order_id=XXX-XXXXXXX-XXXXXXX
    GET /api/summary?period=yesterday|today|week|month
    GET /api/cogs                          → full COGS lookup table (JSON)
    GET /api/cogs/amazon/<asin>            → single ASIN lookup
    GET /api/cogs/shopify/<sku>            → single SKU lookup
"""

import os
import json
import time
import datetime
import requests
from flask import Flask, jsonify, request
from flask_cors import CORS

app  = Flask(__name__)
CORS(app, origins=["*"])
PORT = int(os.environ.get("PORT", 5000))

# ─────────────────────────────────────────────────────────────
# COGS TABLE  (loaded once at startup from cogs_data.json)
# ─────────────────────────────────────────────────────────────

_COGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cogs_data.json")
_COGS: dict = {"amazon": {}, "shopify": {}, "meta": {}}

def _load_cogs():
    global _COGS
    try:
        with open(_COGS_FILE, encoding="utf-8") as f:
            _COGS = json.load(f)
        print(f"  ✅ COGS loaded — "
              f"{len(_COGS.get('amazon', {}))} Amazon ASINs, "
              f"{len(_COGS.get('shopify', {}))} Shopify SKUs")
    except FileNotFoundError:
        print(f"  ⚠️  cogs_data.json not found at {_COGS_FILE} — using fallback COGS")

_load_cogs()

# Amazon weighted COGS ratio (fallback when ASIN not in table or no line items)
# = sum(Costo) / sum(Amazon price) across all products  → 62.6%
AMAZON_COGS_RATIO = float(os.environ.get("AMAZON_COGS_RATIO", "0.6255"))


def cogs_for_line_items(platform: str, line_items: list, fallback_per_unit: float) -> float:
    """
    Calculate total COGS for a list of line items.
    Each item: {"sku": str, "asin": str, "quantity": int}
    Falls back to fallback_per_unit × quantity when SKU/ASIN not in table.
    """
    table  = _COGS.get(platform, {})
    total  = 0.0
    for item in line_items:
        qty  = max(1, int(item.get("quantity", 1)))
        key  = item.get("asin") if platform == "amazon" else item.get("sku", "")
        if key and key in table:
            total += table[key]["cogs"] * qty
        else:
            total += fallback_per_unit * qty
    return round(total, 2)


# ─────────────────────────────────────────────────────────────
# CONFIG HELPERS
# ─────────────────────────────────────────────────────────────

def cfg(env_key: str, header_key: str = None, default: str = "") -> str:
    val = os.environ.get(env_key, "").strip()
    if not val and header_key:
        val = request.headers.get(header_key, "").strip()
    return val or default

def fee_cfg() -> dict:
    return {
        "amazon_fee":    float(os.environ.get("AMAZON_FEE_PCT",    "15")),
        "shopify_fee":   float(os.environ.get("SHOPIFY_FEE_PCT",    "2")),
        "stripe_pct":    float(os.environ.get("STRIPE_FEE_PCT",   "2.9")),
        "stripe_fixed":  float(os.environ.get("STRIPE_FIXED_FEE", "0.30")),
        "cogs_per_unit": float(os.environ.get("COGS_PER_UNIT",    "18")),  # fallback only
    }

# ─────────────────────────────────────────────────────────────
# SHOPIFY
# ─────────────────────────────────────────────────────────────

def shopify_fetch(store: str, token: str, created_at_min: str) -> list:
    store  = store.replace("https://", "").replace("http://", "").rstrip("/")
    url    = f"https://{store}/admin/api/2024-01/orders.json"
    hdrs   = {"X-Shopify-Access-Token": token}
    params = {
        "status":          "any",
        "created_at_min":  created_at_min,
        "limit":           250,
        # include line_items so we can do per-SKU COGS
        "fields": "id,order_number,created_at,total_price,financial_status,"
                  "billing_address,line_items",
    }
    orders = []
    while True:
        r = requests.get(url, headers=hdrs, params=params, timeout=20)
        r.raise_for_status()
        batch = r.json().get("orders", [])
        orders.extend(batch)
        link     = r.headers.get("Link", "")
        next_url = None
        for part in link.split(","):
            if 'rel="next"' in part:
                next_url = part.split(";")[0].strip().strip("<>")
        if not next_url:
            break
        url    = next_url
        params = {}
    return orders


def normalize_shopify_order(o: dict, fees: dict) -> dict:
    """Convert a raw Shopify order dict into our standard format with exact COGS."""
    gross = float(o.get("total_price", 0))

    # Extract line items for per-SKU COGS
    raw_items = o.get("line_items", [])
    line_items = [
        {"sku": li.get("sku", ""), "quantity": li.get("quantity", 1)}
        for li in raw_items
    ]
    total_units = sum(li["quantity"] for li in line_items)
    cogs = cogs_for_line_items("shopify", line_items, fees["cogs_per_unit"])

    platform_fee = gross * (fees["shopify_fee"] / 100)
    stripe_fee   = gross * (fees["stripe_pct"] / 100) + fees["stripe_fixed"]
    total_fees   = platform_fee + stripe_fee + cogs

    bill = o.get("billing_address") or {}
    name = f"{bill.get('first_name','')} {bill.get('last_name','')}".strip() or "Customer"

    return {
        "id":           f"SH-{o.get('order_number', o.get('id', ''))}",
        "platform":     "shopify",
        "created_at":   o.get("created_at", ""),
        "gross":        round(gross, 2),
        "units":        total_units,
        "customer":     name,
        "status":       o.get("financial_status", ""),
        "platform_fee": round(platform_fee, 2),
        "stripe_fee":   round(stripe_fee, 2),
        "cogs":         round(cogs, 2),
        "total_fees":   round(total_fees, 2),
        "net":          round(gross - total_fees, 2),
        "line_items":   [
            {
                "sku":        li.get("sku", ""),
                "title":      li.get("name", li.get("title", "")),
                "quantity":   li.get("quantity", 1),
                "unit_price": float(li.get("price", 0)),
                "cogs":       (_COGS["shopify"].get(li.get("sku",""), {}).get("cogs", fees["cogs_per_unit"]))
                               * li.get("quantity", 1),
            }
            for li in raw_items
        ],
    }


@app.route("/api/shopify/orders")
def get_shopify_orders():
    store = cfg("SHOPIFY_STORE", "X-Shopify-Store")
    token = cfg("SHOPIFY_TOKEN", "X-Shopify-Token")
    start = request.args.get("start",
            (datetime.datetime.utcnow() - datetime.timedelta(days=30)).isoformat() + "Z")
    if not store or not token:
        return jsonify({"error": "Missing Shopify credentials"}), 400
    try:
        raw    = shopify_fetch(store, token, start)
        fees   = fee_cfg()
        orders = [normalize_shopify_order(o, fees) for o in raw]
        return jsonify({"orders": orders, "count": len(orders)})
    except requests.HTTPError as e:
        return jsonify({"error": str(e), "status": e.response.status_code}), 502
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ─────────────────────────────────────────────────────────────
# AMAZON SP-API
# ─────────────────────────────────────────────────────────────

AMAZON_LWA_URL   = "https://api.amazon.com/auth/o2/token"
AMAZON_ENDPOINTS = {
    "us-east-1": "https://sellingpartnerapi-na.amazon.com",
    "eu-west-1": "https://sellingpartnerapi-eu.amazon.com",
    "us-west-2": "https://sellingpartnerapi-fe.amazon.com",
}
_lwa_cache: dict = {}


def get_lwa_token(client_id: str, client_secret: str, refresh_token: str) -> str:
    now    = time.time()
    cached = _lwa_cache.get(client_id)
    if cached and cached["expires"] > now + 60:
        return cached["token"]
    r = requests.post(AMAZON_LWA_URL, data={
        "grant_type": "refresh_token", "refresh_token": refresh_token,
        "client_id": client_id, "client_secret": client_secret,
    }, timeout=15)
    r.raise_for_status()
    data  = r.json()
    token = data["access_token"]
    _lwa_cache[client_id] = {"token": token, "expires": now + data.get("expires_in", 3600)}
    return token


def _amazon_headers(access_token: str) -> dict:
    return {
        "x-amz-access-token": access_token,
        "x-amz-date": datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ"),
        "User-Agent": "IberianHamExpressDashboard/3.0 (Language=Python)",
    }


def amazon_fetch_orders(client_id, client_secret, refresh_token,
                        marketplace, region, created_after) -> list:
    endpoint = AMAZON_ENDPOINTS.get(region, AMAZON_ENDPOINTS["us-east-1"])
    token    = get_lwa_token(client_id, client_secret, refresh_token)
    orders, next_token = [], None
    while True:
        params = {"NextToken": next_token} if next_token else {
            "MarketplaceIds": marketplace, "CreatedAfter": created_after,
            "OrderStatuses": "Shipped,Unshipped,PartiallyShipped,Pending",
            "MaxResultsPerPage": 100,
        }
        r = requests.get(f"{endpoint}/orders/v0/orders",
                         headers=_amazon_headers(token), params=params, timeout=20)
        if r.status_code == 429:
            time.sleep(2)
            continue
        r.raise_for_status()
        payload    = r.json().get("payload", {})
        orders.extend(payload.get("Orders", []))
        next_token = payload.get("NextToken")
        if not next_token:
            break
    return orders


def amazon_fetch_order_items(order_id: str, client_id: str, client_secret: str,
                             refresh_token: str, region: str) -> list:
    """Fetch line items for a single Amazon order (ASIN-level detail)."""
    endpoint = AMAZON_ENDPOINTS.get(region, AMAZON_ENDPOINTS["us-east-1"])
    token    = get_lwa_token(client_id, client_secret, refresh_token)
    items, next_token = [], None
    while True:
        params = {"NextToken": next_token} if next_token else {}
        r = requests.get(f"{endpoint}/orders/v0/orders/{order_id}/orderItems",
                         headers=_amazon_headers(token), params=params, timeout=20)
        if r.status_code == 429:
            time.sleep(2)
            continue
        r.raise_for_status()
        payload    = r.json().get("payload", {})
        items.extend(payload.get("OrderItems", []))
        next_token = payload.get("NextToken")
        if not next_token:
            break
    return items


def normalize_amazon_order(o: dict, fees: dict, order_items: list = None) -> dict:
    """Convert raw Amazon order to standard format. Uses ASIN COGS if order_items provided."""
    gross = float((o.get("OrderTotal") or {}).get("Amount", 0))

    if order_items:
        line_items = [
            {"asin": i.get("ASIN", ""), "quantity": int(i.get("QuantityOrdered", 1))}
            for i in order_items
        ]
        total_units = sum(li["quantity"] for li in line_items)
        cogs = cogs_for_line_items("amazon", line_items, fees["cogs_per_unit"])
        items_out = [
            {
                "asin":       i.get("ASIN", ""),
                "title":      i.get("Title", "")[:80],
                "quantity":   int(i.get("QuantityOrdered", 1)),
                "unit_price": float((i.get("ItemPrice") or {}).get("Amount", 0)),
                "cogs":       (_COGS["amazon"].get(i.get("ASIN",""), {}).get("cogs", fees["cogs_per_unit"]))
                               * int(i.get("QuantityOrdered", 1)),
            }
            for i in order_items
        ]
    else:
        # Fallback: weighted-average ratio from COGS table (62.6%)
        total_units = int(o.get("NumberOfItemsShipped") or o.get("NumberOfItemsUnshipped") or 1)
        cogs        = round(gross * AMAZON_COGS_RATIO, 2)
        items_out   = []

    amazon_fee  = gross * (fees["amazon_fee"] / 100)
    total_fees  = amazon_fee + cogs

    return {
        "id":           o.get("AmazonOrderId", ""),
        "platform":     "amazon",
        "created_at":   o.get("PurchaseDate", ""),
        "gross":        round(gross, 2),
        "units":        total_units,
        "customer":     "Amazon Customer",
        "status":       o.get("OrderStatus", ""),
        "platform_fee": round(amazon_fee, 2),
        "stripe_fee":   0.0,
        "cogs":         cogs,
        "total_fees":   round(total_fees, 2),
        "net":          round(gross - total_fees, 2),
        "cogs_method":  "per_asin" if order_items else "weighted_ratio",
        "line_items":   items_out,
    }


@app.route("/api/amazon/orders")
def get_amazon_orders():
    client_id     = cfg("AMAZON_CLIENT_ID",     "X-Amazon-Client-Id")
    client_secret = cfg("AMAZON_CLIENT_SECRET",  "X-Amazon-Client-Secret")
    refresh_token = cfg("AMAZON_REFRESH_TOKEN",  "X-Amazon-Refresh-Token")
    marketplace   = cfg("AMAZON_MARKETPLACE_ID", "X-Amazon-Marketplace", "ATVPDKIKX0DER")
    region        = cfg("AMAZON_REGION",         "X-Amazon-Region",      "us-east-1")
    start         = request.args.get("start",
                    (datetime.datetime.utcnow() - datetime.timedelta(days=30)).isoformat() + "Z")
    if not client_id or not client_secret or not refresh_token:
        return jsonify({"error": "Missing Amazon credentials"}), 400
    try:
        fees   = fee_cfg()
        raw    = amazon_fetch_orders(client_id, client_secret, refresh_token,
                                     marketplace, region, start)
        orders = [normalize_amazon_order(o, fees) for o in raw]
        return jsonify({"orders": orders, "count": len(orders),
                        "cogs_method": "weighted_ratio",
                        "note": "Call /api/amazon/order-items for exact per-ASIN COGS"})
    except requests.HTTPError as e:
        return jsonify({"error": str(e), "status": e.response.status_code}), 502
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/amazon/order-items")
def get_amazon_order_items():
    """
    Fetch exact line items (with ASINs) for one Amazon order.
    Returns per-ASIN COGS from cogs_data.json.
    Query: ?order_id=XXX-XXXXXXX-XXXXXXX
    """
    order_id      = request.args.get("order_id", "")
    client_id     = cfg("AMAZON_CLIENT_ID",     "X-Amazon-Client-Id")
    client_secret = cfg("AMAZON_CLIENT_SECRET",  "X-Amazon-Client-Secret")
    refresh_token = cfg("AMAZON_REFRESH_TOKEN",  "X-Amazon-Refresh-Token")
    region        = cfg("AMAZON_REGION",         "X-Amazon-Region", "us-east-1")
    if not order_id:
        return jsonify({"error": "order_id query param required"}), 400
    try:
        fees  = fee_cfg()
        items = amazon_fetch_order_items(order_id, client_id, client_secret, refresh_token, region)
        enriched = []
        for i in items:
            asin     = i.get("ASIN", "")
            qty      = int(i.get("QuantityOrdered", 1))
            cogs_rec = _COGS["amazon"].get(asin, {})
            enriched.append({
                "asin":         asin,
                "seller_sku":   i.get("SellerSKU", ""),
                "title":        i.get("Title", "")[:100],
                "quantity":     qty,
                "unit_price":   float((i.get("ItemPrice") or {}).get("Amount", 0)),
                "cogs_per_unit": cogs_rec.get("cogs", fees["cogs_per_unit"]),
                "cogs_total":   round(cogs_rec.get("cogs", fees["cogs_per_unit"]) * qty, 2),
                "in_cogs_table": bool(cogs_rec),
            })
        return jsonify({"order_id": order_id, "items": enriched, "count": len(enriched)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ─────────────────────────────────────────────────────────────
# SUMMARY  (used by daily Telegram report)
# ─────────────────────────────────────────────────────────────

@app.route("/api/summary")
def get_summary():
    period = request.args.get("period", "yesterday")
    et     = datetime.timezone(datetime.timedelta(hours=-4))
    now_et = datetime.datetime.now(et)

    if period == "yesterday":
        day   = now_et - datetime.timedelta(days=1)
        start = datetime.datetime(day.year, day.month, day.day, 0, 0, 0, tzinfo=et)
        end   = datetime.datetime(day.year, day.month, day.day, 23, 59, 59, tzinfo=et)
    elif period == "today":
        start = now_et.replace(hour=0, minute=0, second=0)
        end   = now_et
    elif period == "week":
        start = (now_et - datetime.timedelta(days=now_et.weekday())).replace(hour=0, minute=0, second=0)
        end   = now_et
    else:  # month
        start = now_et.replace(day=1, hour=0, minute=0, second=0)
        end   = now_et

    start_iso = start.astimezone(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
    fees      = fee_cfg()
    errors    = []
    orders    = []

    store = cfg("SHOPIFY_STORE", "X-Shopify-Store")
    token = cfg("SHOPIFY_TOKEN", "X-Shopify-Token")
    if store and token:
        try:
            for o in shopify_fetch(store, token, start_iso):
                dt = datetime.datetime.fromisoformat(
                    o["created_at"].replace("Z", "+00:00")).astimezone(et)
                if start <= dt <= end:
                    orders.append(normalize_shopify_order(o, fees))
        except Exception as e:
            errors.append(f"Shopify: {e}")

    client_id     = cfg("AMAZON_CLIENT_ID",    "X-Amazon-Client-Id")
    client_secret = cfg("AMAZON_CLIENT_SECRET","X-Amazon-Client-Secret")
    refresh_token = cfg("AMAZON_REFRESH_TOKEN","X-Amazon-Refresh-Token")
    marketplace   = cfg("AMAZON_MARKETPLACE_ID","X-Amazon-Marketplace","ATVPDKIKX0DER")
    region        = cfg("AMAZON_REGION","X-Amazon-Region","us-east-1")
    if client_id and client_secret and refresh_token:
        try:
            for o in amazon_fetch_orders(client_id, client_secret, refresh_token,
                                          marketplace, region, start_iso):
                raw_dt = o.get("PurchaseDate", "")
                if not raw_dt:
                    continue
                dt = datetime.datetime.fromisoformat(
                    raw_dt.replace("Z", "+00:00")).astimezone(et)
                if start <= dt <= end:
                    orders.append(normalize_amazon_order(o, fees))
        except Exception as e:
            errors.append(f"Amazon: {e}")

    totals = {
        "total_orders":   len(orders),
        "shopify_orders": sum(1 for o in orders if o["platform"] == "shopify"),
        "amazon_orders":  sum(1 for o in orders if o["platform"] == "amazon"),
        "gross_revenue":  round(sum(o["gross"]        for o in orders), 2),
        "amazon_fees":    round(sum(o["platform_fee"] for o in orders
                                    if o["platform"] == "amazon"), 2),
        "shopify_fees":   round(sum(o["platform_fee"] for o in orders
                                    if o["platform"] == "shopify"), 2),
        "stripe_fees":    round(sum(o["stripe_fee"]   for o in orders), 2),
        "cogs":           round(sum(o["cogs"]         for o in orders), 2),
        "total_fees":     round(sum(o["total_fees"]   for o in orders), 2),
        "net_revenue":    round(sum(o["net"]          for o in orders), 2),
        "total_units":    sum(o["units"] for o in orders),
        "period":         period,
        "period_start":   start.isoformat(),
        "period_end":     end.isoformat(),
        "errors":         errors,
    }
    g = totals["gross_revenue"]
    totals["net_margin"]  = round(totals["net_revenue"] / g * 100, 1) if g else 0
    totals["avg_order"]   = round(g / totals["total_orders"], 2) if totals["total_orders"] else 0
    totals["vendor_owed"] = totals["cogs"]
    totals["cogs_source"] = "per_sku" if _COGS["shopify"] else "flat_rate"
    return jsonify(totals)


# ─────────────────────────────────────────────────────────────
# COGS ENDPOINTS
# ─────────────────────────────────────────────────────────────

@app.route("/api/cogs")
def get_cogs_table():
    return jsonify({
        "meta":    _COGS.get("meta", {}),
        "amazon":  _COGS.get("amazon", {}),
        "shopify": _COGS.get("shopify", {}),
    })

@app.route("/api/cogs/amazon/<asin>")
def get_amazon_cogs(asin):
    rec = _COGS["amazon"].get(asin)
    if not rec:
        return jsonify({"error": f"ASIN {asin} not in COGS table",
                        "fallback_ratio": AMAZON_COGS_RATIO}), 404
    return jsonify({"asin": asin, **rec})

@app.route("/api/cogs/shopify/<sku>")
def get_shopify_cogs(sku):
    rec = _COGS["shopify"].get(sku)
    if not rec:
        return jsonify({"error": f"SKU {sku} not in COGS table",
                        "fallback_per_unit": fee_cfg()["cogs_per_unit"]}), 404
    return jsonify({"sku": sku, **rec})


# ─────────────────────────────────────────────────────────────
# HEALTH
# ─────────────────────────────────────────────────────────────

@app.route("/api/health")
def health():
    return jsonify({
        "status":              "ok",
        "service":             "Iberian Ham Express Dashboard API",
        "version":             "3.0",
        "shopify_configured":  bool(os.environ.get("SHOPIFY_STORE")),
        "amazon_configured":   bool(os.environ.get("AMAZON_CLIENT_ID")),
        "telegram_configured": bool(os.environ.get("TELEGRAM_BOT_TOKEN")),
        "cogs_amazon_skus":    len(_COGS.get("amazon", {})),
        "cogs_shopify_skus":   len(_COGS.get("shopify", {})),
        "amazon_cogs_ratio":   AMAZON_COGS_RATIO,
    })


# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("  Iberian Ham Express — Dashboard API Server v3")
    print("=" * 60)
    mode = "PRODUCTION" if os.environ.get("SHOPIFY_STORE") else "LOCAL DEV"
    print(f"\n  Mode    : {mode}")
    print(f"  URL     : http://localhost:{PORT}")
    print(f"  Health  : http://localhost:{PORT}/api/health")
    print(f"  COGS    : http://localhost:{PORT}/api/cogs")
    print(f"  Summary : http://localhost:{PORT}/api/summary?period=yesterday\n")
    app.run(host="0.0.0.0", port=PORT, debug=(mode == "LOCAL DEV"))
