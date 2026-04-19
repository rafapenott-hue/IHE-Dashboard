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
    GET /api/track?number=XXX             → carrier tracking lookup (FedEx/UPS/USPS)
"""

import os
import hmac
import json
import time
import datetime
from typing import Optional
import requests
from flask import Flask, jsonify, request
from flask_cors import CORS

app  = Flask(__name__)
CORS(app, origins=["*"])
PORT = int(os.environ.get("PORT", 5000))

# ─────────────────────────────────────────────────────────────
# CARRIER TRACKING  (FedEx, UPS, USPS)
# ─────────────────────────────────────────────────────────────

_TRACK_CACHE     = {}       # {tracking_number: {data, ts}}
_TRACK_CACHE_TTL = 1800     # 30 min
_CARRIER_TOKENS  = {}       # {carrier: {token, expires_at}}


def _detect_carrier(tracking_number: str, carrier_hint: str = "") -> str:
    """Detect carrier from tracking number pattern or hint string."""
    hint = (carrier_hint or "").lower()
    if "fedex" in hint:                                    return "fedex"
    if "ups" in hint:                                      return "ups"
    if "usps" in hint or "united states postal" in hint:   return "usps"
    tn = (tracking_number or "").strip()
    if not tn:                                             return "unknown"
    if tn.upper().startswith("1Z") and len(tn) == 18:      return "ups"
    if tn.startswith("9") and tn.isdigit() and len(tn) >= 20: return "usps"
    if tn.isdigit() and len(tn) in (12, 15, 20, 22):      return "fedex"
    return "unknown"


def _norm_status(carrier: str, raw_status: str, code: str = "") -> str:
    """Map carrier-specific status to unified enum."""
    s = (raw_status or "").lower()
    c = (code or "").upper()
    if carrier == "fedex":
        if c in ("PU", "OC"):              return "picked_up"
        if c in ("IT", "IX", "AF"):        return "in_transit"
        if c == "OD":                      return "out_for_delivery"
        if c == "DL":                      return "delivered"
        if c in ("DE", "CA", "SE", "DY"): return "exception"
        if "deliver" in s:                 return "delivered"
        if "transit" in s:                 return "in_transit"
        return "label_created"
    if carrier == "ups":
        if "delivered" in s:                                         return "delivered"
        if "out for delivery" in s:                                  return "out_for_delivery"
        if any(w in s for w in ("in transit", "departed", "arrived")): return "in_transit"
        if "picked up" in s or "origin scan" in s:                   return "picked_up"
        if "exception" in s or "delay" in s:                         return "exception"
        return "label_created"
    if carrier == "usps":
        if "delivered" in s:                                                    return "delivered"
        if "out for delivery" in s:                                             return "out_for_delivery"
        if any(w in s for w in ("in transit", "arrived", "departed", "processed")): return "in_transit"
        if "accepted" in s or "picked up" in s:                                 return "picked_up"
        if "alert" in s or "exception" in s or "delay" in s:                   return "exception"
        return "label_created"
    return "unknown"


def _fedex_token() -> Optional[str]:
    cached = _CARRIER_TOKENS.get("fedex")
    if cached and cached["expires_at"] > time.time():
        return cached["token"]
    cid, csec = os.environ.get("FEDEX_CLIENT_ID", ""), os.environ.get("FEDEX_CLIENT_SECRET", "")
    if not cid or not csec:
        return None
    try:
        r = requests.post("https://apis.fedex.com/oauth/token",
            data={"grant_type": "client_credentials", "client_id": cid, "client_secret": csec},
            headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=10)
        r.raise_for_status()
        d = r.json()
        _CARRIER_TOKENS["fedex"] = {"token": d["access_token"],
            "expires_at": time.time() + int(d.get("expires_in", 3600)) - 60}
        return d["access_token"]
    except Exception as e:
        print(f"[FEDEX] OAuth error: {e}")
        return None


def _ups_token() -> Optional[str]:
    cached = _CARRIER_TOKENS.get("ups")
    if cached and cached["expires_at"] > time.time():
        return cached["token"]
    cid, csec = os.environ.get("UPS_CLIENT_ID", ""), os.environ.get("UPS_CLIENT_SECRET", "")
    if not cid or not csec:
        return None
    try:
        r = requests.post("https://onlinetools.ups.com/security/v1/oauth/token",
            data={"grant_type": "client_credentials"}, auth=(cid, csec),
            headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=10)
        print(f"[UPS] OAuth status: {r.status_code} body: {r.text[:500]}")
        r.raise_for_status()
        d = r.json()
        _CARRIER_TOKENS["ups"] = {"token": d["access_token"],
            "expires_at": time.time() + int(d.get("expires_in", 14400)) - 60}
        return d["access_token"]
    except Exception as e:
        print(f"[UPS] OAuth error: {e}")
        return None


def _usps_token() -> Optional[str]:
    cached = _CARRIER_TOKENS.get("usps")
    if cached and cached["expires_at"] > time.time():
        return cached["token"]
    cid, csec = os.environ.get("USPS_CLIENT_ID", ""), os.environ.get("USPS_CLIENT_SECRET", "")
    if not cid or not csec:
        return None
    try:
        r = requests.post("https://api.usps.com/oauth2/v3/token",
            json={"grant_type": "client_credentials", "client_id": cid, "client_secret": csec},
            headers={"Content-Type": "application/json"}, timeout=10)
        r.raise_for_status()
        d = r.json()
        _CARRIER_TOKENS["usps"] = {"token": d["access_token"],
            "expires_at": time.time() + int(d.get("expires_in", 3600)) - 60}
        return d["access_token"]
    except Exception as e:
        print(f"[USPS] OAuth error: {e}")
        return None


def _track_fedex(tn: str) -> dict:
    """Query FedEx Track API v1. Uses _TRACK_CACHE."""
    cached = _TRACK_CACHE.get(tn)
    if cached and (time.time() - cached["ts"]) < _TRACK_CACHE_TTL:
        return cached["data"]
    token = _fedex_token()
    if not token:
        return {"carrier": "fedex", "tracking_number": tn, "status": "no_credentials", "events": []}
    try:
        r = requests.post("https://apis.fedex.com/track/v1/trackingnumbers",
            json={"trackingInfo": [{"trackingNumberInfo": {"trackingNumber": tn}}],
                  "includeDetailedScans": True},
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                     "X-locale": "en_US"}, timeout=15)
        r.raise_for_status()
        d           = r.json()
        track       = d.get("output", {}).get("completeTrackResults", [{}])[0].get("trackResults", [{}])[0]
        latest      = track.get("latestStatusDetail", {})
        loc         = latest.get("scanLocation", {})
        service_raw = track.get("serviceDetail", {}).get("description", "") or None
        events = [
            {
                "timestamp":   e.get("date", ""),
                "status":      _norm_status("fedex", e.get("eventDescription", ""), e.get("eventType", "")),
                "description": e.get("eventDescription", ""),
                "location":    ", ".join(p for p in [e.get('scanLocation',{}).get('city',''), e.get('scanLocation',{}).get('stateOrProvinceCode','')] if p),
            }
            for e in track.get("scanEvents", [])
        ]
        eta_w        = track.get("estimatedDeliveryTimeWindow", {}).get("window", {})
        eta_raw      = eta_w.get("ends", "") if eta_w else ""   # "2026-04-14T00:00:00"
        est_delivery = eta_raw[:10] if eta_raw else None        # slice to "2026-04-14"
        result = {
            "carrier":            "fedex",
            "tracking_number":    tn,
            "status":             _norm_status("fedex", latest.get("description", ""), latest.get("code", "")),
            "status_description": latest.get("description", ""),
            "service":            service_raw,
            "latest_location":    ", ".join(p for p in [loc.get('city',''), loc.get('stateOrProvinceCode','')] if p),
            "estimated_delivery": est_delivery,
            "events":             events[:20],
        }
        _TRACK_CACHE[tn] = {"data": result, "ts": time.time()}
        return result
    except Exception as e:
        print(f"[FEDEX] Track error: {e}")
        return {"carrier": "fedex", "tracking_number": tn, "status": "error", "events": [], "error": str(e)}


def _track_ups(tn: str) -> dict:
    """Query UPS Track API v1. Uses _TRACK_CACHE."""
    cached = _TRACK_CACHE.get(tn)
    if cached and (time.time() - cached["ts"]) < _TRACK_CACHE_TTL:
        return cached["data"]
    cid = os.environ.get("UPS_CLIENT_ID", "")
    csec = os.environ.get("UPS_CLIENT_SECRET", "")
    if not cid or not csec:
        return {"carrier": "ups", "tracking_number": tn, "status": "no_credentials", "events": []}
    token = _ups_token()
    if not token:
        return {"carrier": "ups", "tracking_number": tn, "status": "auth_error", "events": [], "error": "UPS OAuth token fetch failed — check UPS_CLIENT_ID/SECRET in Render"}
    try:
        r = requests.get(f"https://onlinetools.ups.com/api/track/v1/details/{tn}",
            headers={"Authorization": f"Bearer {token}",
                     "transId": f"ihe-{int(time.time())}", "transactionSrc": "IHE-Dashboard"},
            timeout=15)
        r.raise_for_status()
        shipment     = r.json().get("trackResponse", {}).get("shipment", [{}])[0]
        pkg          = shipment.get("package", [{}])[0]
        service_raw  = shipment.get("service", {}).get("description", "") or None
        acts         = pkg.get("activity", [])
        latest       = acts[0] if acts else {}
        latest_desc  = latest.get("status", {}).get("description", "")
        events = [
            {
                "timestamp":   f"{a.get('date','')} {a.get('time','')}".strip(),
                "status":      _norm_status("ups", a.get("status", {}).get("description", "")),
                "description": a.get("status", {}).get("description", ""),
                "location":    ", ".join(p for p in [a.get('location',{}).get('address',{}).get('city',''), a.get('location',{}).get('address',{}).get('stateOrProvinceCode','')] if p),
            }
            for a in acts
        ]
        eta_entry    = pkg.get("deliveryDate", [{}])[0] if pkg.get("deliveryDate") else {}
        eta_raw      = eta_entry.get("date", "")   # "20260421"
        est_delivery = f"{eta_raw[:4]}-{eta_raw[4:6]}-{eta_raw[6:8]}" if len(eta_raw) == 8 else None
        result = {
            "carrier":            "ups",
            "tracking_number":    tn,
            "status":             _norm_status("ups", latest_desc),
            "status_description": latest_desc,
            "service":            service_raw,
            "latest_location":    events[0]["location"] if events else "",
            "estimated_delivery": est_delivery,
            "events":             events[:20],
        }
        _TRACK_CACHE[tn] = {"data": result, "ts": time.time()}
        return result
    except Exception as e:
        print(f"[UPS] Track error: {e}")
        return {"carrier": "ups", "tracking_number": tn, "status": "error", "events": [], "error": str(e)}


def _track_usps(tn: str) -> dict:
    """Query USPS Track API v3. Uses _TRACK_CACHE."""
    cached = _TRACK_CACHE.get(tn)
    if cached and (time.time() - cached["ts"]) < _TRACK_CACHE_TTL:
        return cached["data"]
    token = _usps_token()
    if not token:
        return {"carrier": "usps", "tracking_number": tn, "status": "no_credentials", "events": []}
    try:
        r = requests.get(f"https://api.usps.com/tracking/v3/tracking/{tn}",
            headers={"Authorization": f"Bearer {token}"},
            params={"expand": "DETAIL"}, timeout=15)
        r.raise_for_status()
        d      = r.json()
        events = [
            {
                "timestamp":   ev.get("eventTimestamp", ""),
                "status":      _norm_status("usps", ev.get("eventType", "")),
                "description": ev.get("eventType", ""),
                "location":    ", ".join(p for p in [ev.get('eventCity',''), ev.get('eventState','')] if p),
            }
            for ev in d.get("trackingEvents", [])
        ]
        latest_desc  = events[0]["description"] if events else d.get("statusCategory", "")
        service_raw  = d.get("mailClass", "") or None
        est_delivery = d.get("expectedDeliveryDate", "") or None   # already "YYYY-MM-DD"
        result = {
            "carrier":            "usps",
            "tracking_number":    tn,
            "status":             _norm_status("usps", latest_desc),
            "status_description": latest_desc,
            "service":            service_raw,
            "latest_location":    events[0]["location"] if events else "",
            "estimated_delivery": est_delivery,
            "events":             events[:20],
        }
        _TRACK_CACHE[tn] = {"data": result, "ts": time.time()}
        return result
    except Exception as e:
        print(f"[USPS] Track error: {e}")
        return {"carrier": "usps", "tracking_number": tn, "status": "error", "events": [], "error": str(e)}

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

def build_digest_message(totals: dict) -> str:
    date_str = datetime.datetime.fromisoformat(totals["period_start"]).strftime("%a %b %-d")
    if totals["total_orders"] == 0:
        return f"📦 IHE — {date_str}\n\nNo orders yesterday."
    sh = totals["shopify_orders"]
    am = totals["amazon_orders"]
    return (
        f"📦 IHE — {date_str}\n\n"
        f"Orders: {totals['total_orders']}  ({sh} Shopify · {am} Amazon)\n"
        f"Gross:  ${totals['gross_revenue']:,.2f}\n"
        f"Net:    ${totals['net_revenue']:,.2f}  ({totals['net_margin']}%)\n"
        f"AOV:    ${totals['avg_order']:,.2f}"
    )

def fee_cfg() -> dict:
    return {
        "amazon_fee":    float(os.environ.get("AMAZON_FEE_PCT",    "15")),
        "shopify_fee":   float(os.environ.get("SHOPIFY_FEE_PCT",    "2")),
        "stripe_pct":    float(os.environ.get("STRIPE_FEE_PCT",   "2.9")),
        "stripe_fixed":  float(os.environ.get("STRIPE_FIXED_FEE", "0.30")),
        "cogs_per_unit": float(os.environ.get("COGS_PER_UNIT",    "18")),  # fallback only
        "shipping_per_order": float(os.environ.get("SHIPPING_PER_ORDER", "8.00")),
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
                  "billing_address,line_items,fulfillments",
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
    shipping     = fees["shipping_per_order"]
    total_fees   = platform_fee + stripe_fee + cogs + shipping

    bill         = o.get("billing_address") or {}
    name         = f"{bill.get('first_name','')} {bill.get('last_name','')}".strip() or "Customer"
    fulfillments = o.get("fulfillments", [])
    tracking_num = fulfillments[0].get("tracking_number") if fulfillments else None
    carrier_name = fulfillments[0].get("tracking_company") if fulfillments else None

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
        "shipping":     round(shipping, 2),
        "total_fees":   round(total_fees, 2),
        "net":             round(gross - total_fees, 2),
        "tracking_number": tracking_num,
        "carrier":         carrier_name,
        "line_items":      [
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
    shipping    = fees["shipping_per_order"]
    total_fees  = amazon_fee + cogs + shipping

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
        "shipping":     round(shipping, 2),
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

def _compute_summary_range(start: datetime.datetime, end: datetime.datetime) -> dict:
    """Compute sales totals for an explicit (start, end) tz-aware range."""
    start_iso = start.astimezone(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
    fees      = fee_cfg()
    errors    = []
    orders    = []

    et = start.tzinfo

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

    client_id     = cfg("AMAZON_CLIENT_ID",     "X-Amazon-Client-Id")
    client_secret = cfg("AMAZON_CLIENT_SECRET", "X-Amazon-Client-Secret")
    refresh_token = cfg("AMAZON_REFRESH_TOKEN", "X-Amazon-Refresh-Token")
    marketplace   = cfg("AMAZON_MARKETPLACE_ID","X-Amazon-Marketplace", "ATVPDKIKX0DER")
    region        = cfg("AMAZON_REGION",        "X-Amazon-Region",      "us-east-1")
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
        "total_orders":     len(orders),
        "shopify_orders":   sum(1 for o in orders if o["platform"] == "shopify"),
        "amazon_orders":    sum(1 for o in orders if o["platform"] == "amazon"),
        "gross_revenue":    round(sum(o["gross"]          for o in orders), 2),
        "amazon_fees":      round(sum(o["platform_fee"]   for o in orders
                                      if o["platform"] == "amazon"), 2),
        "shopify_fees":     round(sum(o["platform_fee"]   for o in orders
                                      if o["platform"] == "shopify"), 2),
        "stripe_fees":      round(sum(o["stripe_fee"]     for o in orders), 2),
        "cogs":             round(sum(o["cogs"]           for o in orders), 2),
        "shipping":         round(sum(o.get("shipping",0) for o in orders), 2),
        "shipping_charged": round(sum(o.get("shipping_charged", 0) for o in orders), 2),
        "shipping_net":     round(sum(o.get("shipping_net", 0)     for o in orders), 2),
        "total_fees":       round(sum(o["total_fees"]     for o in orders), 2),
        "net_revenue":      round(sum(o["net"]            for o in orders), 2),
        "total_units":      sum(o["units"] for o in orders),
        "period_start":     start.isoformat(),
        "period_end":       end.isoformat(),
        "errors":           errors,
        "orders":           orders,
    }
    g = totals["gross_revenue"]
    totals["net_margin"]  = round(totals["net_revenue"] / g * 100, 1) if g else 0
    totals["avg_order"]   = round(g / totals["total_orders"], 2) if totals["total_orders"] else 0
    totals["vendor_owed"] = totals["cogs"]
    totals["cogs_source"] = "per_sku" if _COGS["shopify"] else "flat_rate"
    return totals


def _compute_summary(period: str = "yesterday") -> dict:
    """Backwards-compatible wrapper used by /api/summary and daily digest."""
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

    result = _compute_summary_range(start, end)
    result["period"] = period
    return result


@app.route("/api/summary")
def get_summary():
    period = request.args.get("period", "yesterday")
    return jsonify(_compute_summary(period))


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
# CARRIER TRACKING ENDPOINT
# ─────────────────────────────────────────────────────────────

@app.route("/api/track")
def get_tracking():
    number = request.args.get("number", "").strip()
    if not number:
        return jsonify({"error": "number query param required"}), 400
    carrier_hint = request.args.get("carrier", "")
    carrier      = _detect_carrier(number, carrier_hint)
    if carrier == "fedex":
        return jsonify(_track_fedex(number))
    if carrier == "ups":
        return jsonify(_track_ups(number))
    if carrier == "usps":
        return jsonify(_track_usps(number))
    return jsonify({
        "carrier": "unknown", "tracking_number": number,
        "status": "unknown_carrier", "events": [],
        "error": f"Cannot detect carrier for {number}",
    })

# ─────────────────────────────────────────────────────────────
# DASHBOARD  (serves dashboard.html at the root URL)
# ─────────────────────────────────────────────────────────────

@app.route("/")
def dashboard():
    html_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard.html")
    if os.path.exists(html_path):
        with open(html_path, encoding="utf-8") as f:
            content = f.read()
        return content, 200, {"Content-Type": "text/html; charset=utf-8"}
    return "<h2>dashboard.html not found — add it to the repo root.</h2>", 404


# ─────────────────────────────────────────────────────────────
# CONFIG  (used by dashboard auto-detect in init())
# ─────────────────────────────────────────────────────────────

@app.route("/api/config")
def get_config():
    return jsonify({
        "shopify_configured":   bool(os.environ.get("SHOPIFY_STORE")),
        "shopify_store":        os.environ.get("SHOPIFY_STORE", ""),
        "amazon_configured":    bool(os.environ.get("AMAZON_CLIENT_ID")),
        "amazon_marketplace":   os.environ.get("AMAZON_MARKETPLACE_ID", "ATVPDKIKX0DER"),
        "amazon_region":        os.environ.get("AMAZON_REGION", "us-east-1"),
        "fees": {
            "amazon_fee":   float(os.environ.get("AMAZON_FEE_PCT", 15)),
            "shopify_fee":  float(os.environ.get("SHOPIFY_FEE_PCT", 2)),
            "stripe_pct":   float(os.environ.get("STRIPE_FEE_PCT", 2.9)),
            "stripe_fixed": float(os.environ.get("STRIPE_FIXED_FEE", 0.30)),
            "cogs_per_unit": float(os.environ.get("COGS_PER_UNIT", 18)),
        },
    })


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
        "fedex_configured":    bool(os.environ.get("FEDEX_CLIENT_ID") and os.environ.get("FEDEX_CLIENT_SECRET")),
        "ups_configured":      bool(os.environ.get("UPS_CLIENT_ID") and os.environ.get("UPS_CLIENT_SECRET")),
        "usps_configured":     bool(os.environ.get("USPS_CLIENT_ID") and os.environ.get("USPS_CLIENT_SECRET")),
    })


def send_telegram(token: str, chat_id: str, text: str) -> None:
    url  = f"https://api.telegram.org/bot{token}/sendMessage"
    resp = requests.post(url, json={"chat_id": chat_id, "text": text}, timeout=10)
    if not resp.ok:
        raise RuntimeError(f"Telegram error {resp.status_code}: {resp.text}")


@app.route("/api/digest", methods=["POST"])
def post_digest():
    secret = os.environ.get("DIGEST_SECRET", "")
    if not secret:
        return jsonify({"error": "digest not configured"}), 503
    if not hmac.compare_digest(request.args.get("secret", ""), secret):
        return jsonify({"error": "unauthorized"}), 401

    tg_token  = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    tg_chat   = os.environ.get("TELEGRAM_CHAT_ID",   "")
    if not tg_token or not tg_chat:
        return jsonify({"error": "Telegram not configured"}), 503

    try:
        totals  = _compute_summary("yesterday")
        message = build_digest_message(totals)
        send_telegram(tg_token, tg_chat, message)
        return jsonify({"ok": True, "message": message})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


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
