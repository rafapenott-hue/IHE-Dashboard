# Carrier Tracking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the Tracking tab to real FedEx, UPS, and USPS carrier APIs — auto-fetching live shipment status when the tab opens.

**Architecture:** Port the carrier tracking functions from `app.py` (present but in a corrupted binary) into `api_server.py`, expose them via a new `/api/track` endpoint, update the Shopify order fetch to include tracking numbers from fulfillments, and rewrite the Tracking tab JS to call the endpoint in parallel on tab open with a 30-min client-side cache.

**Tech Stack:** Python/Flask (backend), vanilla JS (frontend), FedEx Track API v1, UPS Track API v1, USPS Track API v3 (all OAuth2)

---

## File Map

| File | Change |
|---|---|
| `api_server.py` | Add carrier token/tracking functions + `/api/track` route + update Shopify fetch |
| `render.yaml` | Add 6 new env vars for carrier credentials |
| `dashboard.html` | Update tracking tab HTML (cards + table) + rewrite tracking JS |
| `tests/test_tracking.py` | New — unit tests for carrier functions and `/api/track` endpoint |
| `requirements.txt` | Add `pytest==8.3.5` |

---

## Task 1: Tests + carrier helper functions in api_server.py

`app.py` has all the carrier code written but the file is binary-corrupted and unused. This task ports it cleanly.

**Files:**
- Create: `tests/test_tracking.py`
- Modify: `api_server.py` (add after line 38, before the `# COGS TABLE` section)
- Modify: `requirements.txt`

- [ ] **Step 1: Add pytest to requirements.txt**

Open `requirements.txt` and add:
```
pytest==8.3.5
```

Final file:
```
flask==3.0.3
flask-cors==4.0.1
requests==2.32.3
gunicorn==22.0.0
pytest==8.3.5
```

- [ ] **Step 2: Create the test file with a failing test**

Create `tests/__init__.py` (empty) and `tests/test_tracking.py`:

```python
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import patch, MagicMock
import api_server
from api_server import _norm_status, _detect_carrier, _track_fedex, _track_ups, _track_usps


# ── _detect_carrier ────────────────────────────────────────────

def test_detect_fedex_by_digits():
    assert _detect_carrier("794644823401") == "fedex"        # 12-digit

def test_detect_ups_by_prefix():
    assert _detect_carrier("1Z999AA10123456784") == "ups"    # starts 1Z, 18 chars

def test_detect_usps_by_prefix():
    assert _detect_carrier("9400111899223397846233") == "usps"  # starts 9, ≥20 chars

def test_detect_by_hint():
    assert _detect_carrier("12345", "FedEx Ground") == "fedex"
    assert _detect_carrier("12345", "UPS") == "ups"
    assert _detect_carrier("12345", "USPS") == "usps"


# ── _norm_status ───────────────────────────────────────────────

def test_norm_status_fedex():
    assert _norm_status("fedex", "Delivered", "DL")         == "delivered"
    assert _norm_status("fedex", "On FedEx vehicle", "OD")  == "out_for_delivery"
    assert _norm_status("fedex", "In transit", "IT")        == "in_transit"
    assert _norm_status("fedex", "Delay", "DY")             == "exception"

def test_norm_status_ups():
    assert _norm_status("ups", "Delivered")           == "delivered"
    assert _norm_status("ups", "Out For Delivery")    == "out_for_delivery"
    assert _norm_status("ups", "In Transit")          == "in_transit"
    assert _norm_status("ups", "Exception - Delay")   == "exception"

def test_norm_status_usps():
    assert _norm_status("usps", "Delivered")          == "delivered"
    assert _norm_status("usps", "Out for Delivery")   == "out_for_delivery"
    assert _norm_status("usps", "In Transit")         == "in_transit"
    assert _norm_status("usps", "Delivery Alert")     == "exception"


# ── _track_fedex ───────────────────────────────────────────────

def _fedex_response(code, description, city="Miami", state="FL", eta="2026-04-14T00:00:00"):
    return {
        "output": {"completeTrackResults": [{"trackResults": [{
            "latestStatusDetail": {
                "code": code, "description": description,
                "scanLocation": {"city": city, "stateOrProvinceCode": state}
            },
            "estimatedDeliveryTimeWindow": {"window": {"ends": eta}},
            "scanEvents": [],
        }]}]}
    }

def test_track_fedex_delivered():
    mock_resp = MagicMock()
    mock_resp.json.return_value = _fedex_response("DL", "Delivered")
    mock_resp.raise_for_status = MagicMock()
    with patch("api_server._fedex_token", return_value="tok"), \
         patch("api_server.requests.post", return_value=mock_resp), \
         patch.dict(api_server._TRACK_CACHE, {}, clear=True):
        result = _track_fedex("794644823401")
    assert result["status"] == "delivered"
    assert result["carrier"] == "fedex"
    assert result["location"] == "Miami, FL"
    assert result["eta"] == "2026-04-14T00:00:00"

def test_track_fedex_no_token():
    with patch("api_server._fedex_token", return_value=None):
        result = _track_fedex("794644823401")
    assert result["status"] == "no_credentials"

def test_track_fedex_api_error():
    with patch("api_server._fedex_token", return_value="tok"), \
         patch("api_server.requests.post", side_effect=Exception("timeout")), \
         patch.dict(api_server._TRACK_CACHE, {}, clear=True):
        result = _track_fedex("794644823401")
    assert result["status"] == "error"
    assert "timeout" in result["error"]


# ── _track_ups ─────────────────────────────────────────────────

def _ups_response(description, city="Atlanta", state="GA", eta_date="20260415"):
    return {
        "trackResponse": {"shipment": [{"package": [{
            "activity": [{
                "location": {"address": {"city": city, "stateOrProvinceCode": state}},
                "status": {"description": description},
                "date": "20260413", "time": "214700"
            }],
            "deliveryDate": [{"date": eta_date}],
        }]}]}
    }

def test_track_ups_in_transit():
    mock_resp = MagicMock()
    mock_resp.json.return_value = _ups_response("In Transit")
    mock_resp.raise_for_status = MagicMock()
    with patch("api_server._ups_token", return_value="tok"), \
         patch("api_server.requests.get", return_value=mock_resp), \
         patch.dict(api_server._TRACK_CACHE, {}, clear=True):
        result = _track_ups("1Z999AA10123456784")
    assert result["status"] == "in_transit"
    assert result["carrier"] == "ups"
    assert result["eta"] == "20260415"

def test_track_ups_no_token():
    with patch("api_server._ups_token", return_value=None):
        result = _track_ups("1Z999AA10123456784")
    assert result["status"] == "no_credentials"


# ── _track_usps ────────────────────────────────────────────────

def _usps_response(event_type, city="Miami", state="FL", eta="2026-04-16"):
    return {
        "trackingEvents": [{
            "eventTimestamp": "2026-04-14T08:00:00",
            "eventType": event_type,
            "eventCity": city, "eventState": state, "eventZIPCode": "33101"
        }],
        "expectedDeliveryDate": eta
    }

def test_track_usps_delivered():
    mock_resp = MagicMock()
    mock_resp.json.return_value = _usps_response("DELIVERED")
    mock_resp.raise_for_status = MagicMock()
    with patch("api_server._usps_token", return_value="tok"), \
         patch("api_server.requests.get", return_value=mock_resp), \
         patch.dict(api_server._TRACK_CACHE, {}, clear=True):
        result = _track_usps("9400111899223397846233")
    assert result["status"] == "delivered"
    assert result["carrier"] == "usps"
    assert result["eta"] == "2026-04-16"

def test_track_usps_no_token():
    with patch("api_server._usps_token", return_value=None):
        result = _track_usps("9400111899223397846233")
    assert result["status"] == "no_credentials"


# ── /api/track endpoint ────────────────────────────────────────

def test_api_track_routes_to_fedex():
    with patch("api_server._track_fedex") as mock_fn:
        mock_fn.return_value = {"carrier": "fedex", "status": "delivered", "tracking_number": "794644823401"}
        client = api_server.app.test_client()
        resp = client.get("/api/track?number=794644823401")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["carrier"] == "fedex"
    mock_fn.assert_called_once_with("794644823401")

def test_api_track_missing_number():
    client = api_server.app.test_client()
    resp = client.get("/api/track")
    assert resp.status_code == 400
```

- [ ] **Step 3: Run tests — verify they fail**

```bash
cd /Users/rafaelpenott/IHE-Dashboard/src
pip install pytest==8.3.5 -q
pytest tests/test_tracking.py -v 2>&1 | head -40
```

Expected: `ImportError` or `AttributeError` — functions not yet defined in api_server.py.

- [ ] **Step 4: Add carrier functions to api_server.py**

Open `api_server.py`. Find this line (around line 38):
```python
PORT = int(os.environ.get("PORT", 5000))
```

Insert the entire block below immediately after it (before the `# COGS TABLE` section):

```python

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
    if tn.isdigit() and len(tn) in (12, 15, 20, 22):      return "fedex"
    if tn.upper().startswith("1Z") and len(tn) == 18:      return "ups"
    if tn.isdigit() and len(tn) >= 20 or (tn.startswith("9") and len(tn) >= 20):
        return "usps"
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


def _fedex_token() -> str | None:
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
            "expires_at": time.time() + d.get("expires_in", 3600) - 60}
        return d["access_token"]
    except Exception as e:
        print(f"[FEDEX] OAuth error: {e}")
        return None


def _ups_token() -> str | None:
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
        r.raise_for_status()
        d = r.json()
        _CARRIER_TOKENS["ups"] = {"token": d["access_token"],
            "expires_at": time.time() + d.get("expires_in", 14400) - 60}
        return d["access_token"]
    except Exception as e:
        print(f"[UPS] OAuth error: {e}")
        return None


def _usps_token() -> str | None:
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
            "expires_at": time.time() + d.get("expires_in", 3600) - 60}
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
        d     = r.json()
        track = d.get("output", {}).get("completeTrackResults", [{}])[0].get("trackResults", [{}])[0]
        latest = track.get("latestStatusDetail", {})
        loc    = latest.get("scanLocation", {})
        events = [
            {
                "timestamp":   e.get("date", ""),
                "status":      _norm_status("fedex", e.get("eventDescription", ""), e.get("eventType", "")),
                "description": e.get("eventDescription", ""),
                "location":    f"{e.get('scanLocation',{}).get('city','')}, "
                               f"{e.get('scanLocation',{}).get('stateOrProvinceCode','')}".strip(", "),
            }
            for e in track.get("scanEvents", [])
        ]
        eta_w  = track.get("estimatedDeliveryTimeWindow", {}).get("window", {})
        result = {
            "carrier":          "fedex",
            "tracking_number":  tn,
            "status":           _norm_status("fedex", latest.get("description", ""), latest.get("code", "")),
            "status_description": latest.get("description", ""),
            "location":         f"{loc.get('city','')}, {loc.get('stateOrProvinceCode','')}".strip(", "),
            "eta":              eta_w.get("ends", "") if eta_w else "",
            "events":           events[:20],
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
    token = _ups_token()
    if not token:
        return {"carrier": "ups", "tracking_number": tn, "status": "no_credentials", "events": []}
    try:
        r = requests.get(f"https://onlinetools.ups.com/api/track/v1/details/{tn}",
            headers={"Authorization": f"Bearer {token}",
                     "transId": f"ihe-{int(time.time())}", "transactionSrc": "IHE-Dashboard"},
            timeout=15)
        r.raise_for_status()
        pkg    = r.json().get("trackResponse", {}).get("shipment", [{}])[0].get("package", [{}])[0]
        acts   = pkg.get("activity", [])
        latest = acts[0] if acts else {}
        latest_desc = latest.get("status", {}).get("description", "")
        events = [
            {
                "timestamp":   f"{a.get('date','')} {a.get('time','')}".strip(),
                "status":      _norm_status("ups", a.get("status", {}).get("description", "")),
                "description": a.get("status", {}).get("description", ""),
                "location":    f"{a.get('location',{}).get('address',{}).get('city','')}, "
                               f"{a.get('location',{}).get('address',{}).get('stateOrProvinceCode','')}".strip(", "),
            }
            for a in acts
        ]
        eta_entry = pkg.get("deliveryDate", [{}])[0] if pkg.get("deliveryDate") else {}
        result = {
            "carrier":            "ups",
            "tracking_number":    tn,
            "status":             _norm_status("ups", latest_desc),
            "status_description": latest_desc,
            "location":           events[0]["location"] if events else "",
            "eta":                eta_entry.get("date", ""),
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
                "location":    f"{ev.get('eventCity','')}, {ev.get('eventState','')} "
                               f"{ev.get('eventZIPCode','')}".strip(", "),
            }
            for ev in d.get("trackingEvents", [])
        ]
        latest_desc = events[0]["description"] if events else d.get("statusCategory", "")
        result = {
            "carrier":            "usps",
            "tracking_number":    tn,
            "status":             _norm_status("usps", latest_desc),
            "status_description": latest_desc,
            "location":           events[0]["location"] if events else "",
            "eta":                d.get("expectedDeliveryDate", ""),
            "events":             events[:20],
        }
        _TRACK_CACHE[tn] = {"data": result, "ts": time.time()}
        return result
    except Exception as e:
        print(f"[USPS] Track error: {e}")
        return {"carrier": "usps", "tracking_number": tn, "status": "error", "events": [], "error": str(e)}
```

- [ ] **Step 5: Run tests — verify they pass**

```bash
cd /Users/rafaelpenott/IHE-Dashboard/src
pytest tests/test_tracking.py -v
```

Expected output:
```
tests/test_tracking.py::test_detect_fedex_by_digits PASSED
tests/test_tracking.py::test_detect_ups_by_prefix PASSED
tests/test_tracking.py::test_detect_usps_by_prefix PASSED
tests/test_tracking.py::test_detect_by_hint PASSED
tests/test_tracking.py::test_norm_status_fedex PASSED
tests/test_tracking.py::test_norm_status_ups PASSED
tests/test_tracking.py::test_norm_status_usps PASSED
tests/test_tracking.py::test_track_fedex_delivered PASSED
tests/test_tracking.py::test_track_fedex_no_token PASSED
tests/test_tracking.py::test_track_fedex_api_error PASSED
tests/test_tracking.py::test_track_ups_in_transit PASSED
tests/test_tracking.py::test_track_ups_no_token PASSED
tests/test_tracking.py::test_track_usps_delivered PASSED
tests/test_tracking.py::test_track_usps_no_token PASSED
```

The `/api/track` tests (`test_api_track_routes_to_fedex`, `test_api_track_missing_number`) will still fail — the route doesn't exist yet.

- [ ] **Step 6: Commit**

```bash
cd /Users/rafaelpenott/IHE-Dashboard/src
git add requirements.txt tests/ api_server.py
git commit -m "feat: add carrier tracking functions to api_server"
```

---

## Task 2: Add /api/track endpoint to api_server.py

**Files:**
- Modify: `api_server.py` (add route after the `/api/cogs/shopify/<sku>` route, around line 510)

- [ ] **Step 1: Add the /api/track route**

Open `api_server.py`. Find this block (around line 504):

```python
@app.route("/api/cogs/shopify/<sku>")
def get_shopify_cogs(sku):
    rec = _COGS["shopify"].get(sku)
    if not rec:
        return jsonify({"error": f"SKU {sku} not in COGS table",
                        "fallback_per_unit": fee_cfg()["cogs_per_unit"]}), 404
    return jsonify({"sku": sku, **rec})
```

Add immediately after it:

```python

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
```

Also update the docstring at the top of `api_server.py` (lines 17-25). Find:

```python
    GET /api/cogs                          → full COGS lookup table (JSON)
    GET /api/cogs/amazon/<asin>            → single ASIN lookup
    GET /api/cogs/shopify/<sku>            → single SKU lookup
```

Replace with:

```python
    GET /api/cogs                          → full COGS lookup table (JSON)
    GET /api/cogs/amazon/<asin>            → single ASIN lookup
    GET /api/cogs/shopify/<sku>            → single SKU lookup
    GET /api/track?number=XXX             → carrier tracking lookup (FedEx/UPS/USPS)
```

- [ ] **Step 2: Run all tests — verify /api/track tests now pass**

```bash
cd /Users/rafaelpenott/IHE-Dashboard/src
pytest tests/test_tracking.py -v
```

Expected: all 16 tests pass.

- [ ] **Step 3: Smoke test locally**

```bash
cd /Users/rafaelpenott/IHE-Dashboard/src
python api_server.py &
sleep 2
curl "http://localhost:5000/api/track" | python -m json.tool
curl "http://localhost:5000/api/track?number=794644823401" | python -m json.tool
kill %1
```

Expected for missing number: `{"error": "number query param required"}` with status 400.
Expected for a tracking number without env vars: `{"status": "no_credentials", "carrier": "fedex", ...}`.

- [ ] **Step 4: Commit**

```bash
cd /Users/rafaelpenott/IHE-Dashboard/src
git add api_server.py
git commit -m "feat: add /api/track endpoint"
```

---

## Task 3: Pull tracking numbers from Shopify orders

**Files:**
- Modify: `api_server.py` (two spots: `shopify_fetch` fields param and `normalize_shopify_order` return dict)

- [ ] **Step 1: Update shopify_fetch to request fulfillments**

Find in `api_server.py` (around line 114):

```python
        "fields": "id,order_number,created_at,total_price,financial_status,"
                  "billing_address,line_items",
```

Replace with:

```python
        "fields": "id,order_number,created_at,total_price,financial_status,"
                  "billing_address,line_items,fulfillments",
```

- [ ] **Step 2: Update normalize_shopify_order to extract tracking info**

Find in `normalize_shopify_order` (around line 153, just before the `return {` statement):

```python
    bill = o.get("billing_address") or {}
    name = f"{bill.get('first_name','')} {bill.get('last_name','')}".strip() or "Customer"

    return {
```

Replace with:

```python
    bill         = o.get("billing_address") or {}
    name         = f"{bill.get('first_name','')} {bill.get('last_name','')}".strip() or "Customer"
    fulfillments = o.get("fulfillments", [])
    tracking_num = fulfillments[0].get("tracking_number") if fulfillments else None
    carrier_name = fulfillments[0].get("tracking_company") if fulfillments else None

    return {
```

Then in the `return { ... }` dict, find:

```python
        "net":          round(gross - total_fees, 2),
        "line_items":   [
```

Replace with:

```python
        "net":             round(gross - total_fees, 2),
        "tracking_number": tracking_num,
        "carrier":         carrier_name,
        "line_items":      [
```

- [ ] **Step 3: Add a test for tracking number extraction**

Add to `tests/test_tracking.py`:

```python
# ── Shopify tracking extraction ────────────────────────────────

def test_normalize_shopify_order_extracts_tracking():
    from api_server import normalize_shopify_order
    raw = {
        "id": 1, "order_number": 1042,
        "created_at": "2026-04-13T10:00:00Z",
        "total_price": "89.00", "financial_status": "paid",
        "billing_address": {"first_name": "Jane", "last_name": "Doe"},
        "line_items": [{"sku": "HAM-001", "quantity": 1, "price": "89.00",
                         "name": "Iberian Ham", "title": "Iberian Ham"}],
        "fulfillments": [{"tracking_number": "794644823401",
                          "tracking_company": "FedEx",
                          "status": "success"}],
    }
    fees = {"amazon_fee": 15, "shopify_fee": 2, "stripe_pct": 2.9,
            "stripe_fixed": 0.30, "cogs_per_unit": 18}
    order = normalize_shopify_order(raw, fees)
    assert order["tracking_number"] == "794644823401"
    assert order["carrier"] == "FedEx"

def test_normalize_shopify_order_no_fulfillment():
    from api_server import normalize_shopify_order
    raw = {
        "id": 2, "order_number": 1043,
        "created_at": "2026-04-13T10:00:00Z",
        "total_price": "50.00", "financial_status": "paid",
        "billing_address": {}, "line_items": [], "fulfillments": [],
    }
    fees = {"amazon_fee": 15, "shopify_fee": 2, "stripe_pct": 2.9,
            "stripe_fixed": 0.30, "cogs_per_unit": 18}
    order = normalize_shopify_order(raw, fees)
    assert order["tracking_number"] is None
    assert order["carrier"] is None
```

- [ ] **Step 4: Run tests**

```bash
cd /Users/rafaelpenott/IHE-Dashboard/src
pytest tests/test_tracking.py -v
```

Expected: all 18 tests pass.

- [ ] **Step 5: Commit**

```bash
cd /Users/rafaelpenott/IHE-Dashboard/src
git add api_server.py tests/test_tracking.py
git commit -m "feat: include tracking number from Shopify fulfillments"
```

---

## Task 4: Update render.yaml with carrier env vars

**Files:**
- Modify: `render.yaml`

- [ ] **Step 1: Add carrier credentials**

Open `render.yaml`. Find:

```yaml
      - key: TELEGRAM_BOT_TOKEN
        sync: false
      - key: TELEGRAM_CHAT_ID
        sync: false
```

Replace with:

```yaml
      - key: TELEGRAM_BOT_TOKEN
        sync: false
      - key: TELEGRAM_CHAT_ID
        sync: false
      # ── Carrier Tracking ──────────────────────────────────────
      - key: FEDEX_CLIENT_ID
        sync: false
      - key: FEDEX_CLIENT_SECRET
        sync: false
      - key: UPS_CLIENT_ID
        sync: false
      - key: UPS_CLIENT_SECRET
        sync: false
      - key: USPS_CLIENT_ID
        sync: false
      - key: USPS_CLIENT_SECRET
        sync: false
```

- [ ] **Step 2: Commit**

```bash
cd /Users/rafaelpenott/IHE-Dashboard/src
git add render.yaml
git commit -m "config: add carrier tracking env vars to render.yaml"
```

---

## Task 5: Update dashboard.html tracking tab HTML

**Files:**
- Modify: `dashboard.html`

- [ ] **Step 1: Update tracking card CSS (grid + new color classes)**

Find in `dashboard.html` (around line 530):

```css
.tracking-cards{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;margin-bottom:24px}
.track-card{background:var(--card-bg);border:1px solid var(--border);border-radius:12px;padding:20px;text-align:center}
.track-card .label{font-size:.75rem;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;margin-bottom:4px}
.track-card .value{font-size:1.75rem;font-weight:700;color:var(--text)}
.track-card.shipped .value{color:#22c55e}
.track-card.unshipped .value{color:#f59e0b}
.track-card.overdue .value{color:#ef4444}
```

Replace with:

```css
.tracking-cards{display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin-bottom:16px}
.track-card{background:var(--card);border:1px solid var(--border);border-radius:var(--radius);padding:16px;text-align:center}
.track-card .label{font-size:10px;color:var(--text-3);text-transform:uppercase;letter-spacing:.5px;margin-bottom:6px}
.track-card .value{font-size:1.75rem;font-weight:700;color:var(--text)}
.track-card.delivered .value{color:var(--success)}
.track-card.transit .value{color:var(--accent)}
.track-card.pending-trk .value{color:var(--warning)}
.track-card.exception .value{color:var(--danger)}
.plat-shopify{color:var(--shopify)}
.plat-amazon{color:var(--amazon)}
```

Also find (around line 537):

```css
.track-filters{display:flex;gap:8px;margin-bottom:16px}
```

Replace with:

```css
.track-filters{display:flex;align-items:center;gap:8px;margin-bottom:16px;flex-wrap:wrap}
```

- [ ] **Step 2: Replace tracking tab HTML**

Find in `dashboard.html` (around line 783):

```html
<div id="page-tracking" style="display:none">
  <div class="tracking-cards">
    <div class="track-card"><div class="label">Total Orders</div><div class="value" id="trk-total">0</div></div>
    <div class="track-card shipped"><div class="label">Shipped</div><div class="value" id="trk-shipped">0</div></div>
    <div class="track-card unshipped"><div class="label">Unshipped</div><div class="value" id="trk-unshipped">0</div></div>
    <div class="track-card overdue"><div class="label">Overdue (&gt;48h)</div><div class="value" id="trk-overdue">0</div></div>
  </div>
  <div class="track-filters">
    <button class="track-filter active" onclick="filterTracking('all')">All</button>
    <button class="track-filter" onclick="filterTracking('fulfilled')">Shipped</button>
    <button class="track-filter" onclick="filterTracking('unfulfilled')">Unshipped</button>
    <button class="track-filter" onclick="filterTracking('overdue')">Overdue</button>
  </div>
  <div class="tbl-wrap">
    <table class="tbl" id="tracking-table">
      <thead><tr>
        <th>Date</th><th>Order</th><th>Platform</th><th>Customer</th><th>Gross</th><th>Status</th><th>Days</th>
      </tr></thead>
      <tbody id="tracking-body"></tbody>
    </table>
  </div>
</div>
```

Replace with:

```html
<div id="page-tracking" style="display:none">
  <div class="tracking-cards">
    <div class="track-card"><div class="label">Total</div><div class="value" id="trk-total">0</div></div>
    <div class="track-card delivered"><div class="label">Delivered</div><div class="value" id="trk-delivered">0</div></div>
    <div class="track-card transit"><div class="label">In Transit</div><div class="value" id="trk-transit">0</div></div>
    <div class="track-card pending-trk"><div class="label">Pending</div><div class="value" id="trk-pending">0</div></div>
    <div class="track-card exception"><div class="label">Exception</div><div class="value" id="trk-exception">0</div></div>
  </div>
  <div class="track-filters">
    <button class="track-filter active" data-filter="all" onclick="filterTracking('all')">All</button>
    <button class="track-filter" data-filter="delivered" onclick="filterTracking('delivered')">Delivered</button>
    <button class="track-filter" data-filter="in_transit" onclick="filterTracking('in_transit')">In Transit</button>
    <button class="track-filter" data-filter="pending" onclick="filterTracking('pending')">Pending</button>
    <button class="track-filter" data-filter="exception" onclick="filterTracking('exception')">Exception</button>
    <span style="margin-left:auto;display:flex;align-items:center;gap:8px">
      <span id="trk-updated" style="font-size:11px;color:var(--text-3)"></span>
      <button class="btn" onclick="refreshTracking()">↻ Refresh</button>
    </span>
  </div>
  <div class="tbl-wrap">
    <table class="tbl" id="tracking-table">
      <thead><tr>
        <th>Date</th><th>Order</th><th>Platform</th><th>Carrier</th><th>Carrier Status</th><th>Est. Delivery</th><th>Days</th>
      </tr></thead>
      <tbody id="tracking-body"></tbody>
    </table>
  </div>
</div>
```

- [ ] **Step 3: Commit**

```bash
cd /Users/rafaelpenott/IHE-Dashboard/src
git add dashboard.html
git commit -m "feat: update tracking tab HTML for carrier status"
```

---

## Task 6: Rewrite tracking tab JavaScript

**Files:**
- Modify: `dashboard.html` (the `// === TRACKING TAB ===` section, lines ~1444–1507)

- [ ] **Step 1: Replace the tracking JS section**

Find in `dashboard.html`:

```javascript
// === TRACKING TAB ===
let trackingFilter = "all";

function switchPage(page) {
  document.getElementById("page-dashboard").style.display = page === "dashboard" ? "" : "none";
  document.getElementById("page-tracking").style.display = page === "tracking" ? "" : "none";
  document.querySelectorAll(".nav-tab").forEach(function(b, i) {
    b.classList.toggle("active", (i === 0 && page === "dashboard") || (i === 1 && page === "tracking"));
  });
  if (page === "tracking") renderTracking();
}

function daysSince(dateStr) {
  var d = new Date(dateStr);
  var now = new Date();
  return Math.floor((now - d) / 86400000);
}

function filterTracking(f) {
  trackingFilter = f;
  document.querySelectorAll(".track-filter").forEach(function(b) {
    var t = b.textContent.toLowerCase();
    b.classList.toggle("active", (f==="all" && t==="all") || (f==="fulfilled" && t==="shipped") || (f==="unfulfilled" && t==="unshipped") || (f==="overdue" && t==="overdue"));
  });
  renderTracking();
}
function renderTracking() {
  var orders = filteredOrders() || [];
  var total = 0, shipped = 0, unshipped = 0, overdue = 0;
  var rows = [];
  orders.forEach(function(o) {
    var fs = (o.fulfillment_status || "unfulfilled").toLowerCase();
    var days = daysSince(o.date);
    var isShipped = fs === "fulfilled";
    var isOverdue = !isShipped && days > 2;
    total++;
    if (isShipped) shipped++;
    else if (isOverdue) { overdue++; unshipped++; }
    else unshipped++;
    var status = isShipped ? "fulfilled" : isOverdue ? "overdue" : "pending";
    if (trackingFilter === "all" || trackingFilter === status || (trackingFilter === "unfulfilled" && (status === "pending" || status === "overdue"))) {
      rows.push({o: o, status: status, days: days, isShipped: isShipped});
    }
  });
  document.getElementById("trk-total").textContent = total;
  document.getElementById("trk-shipped").textContent = shipped;
  document.getElementById("trk-unshipped").textContent = unshipped;
  document.getElementById("trk-overdue").textContent = overdue;
  var body = document.getElementById("tracking-body");
  body.innerHTML = "";
  rows.forEach(function(r) {
    var tr = document.createElement("tr");
    var badgeClass = r.status;
    var badgeText = r.isShipped ? "Shipped" : r.status === "overdue" ? "Overdue" : "Pending";
    tr.innerHTML = "<td>" + r.o.date.toISOString().slice(0,10) + "</td>" +
      "<td>#" + r.o.id + "</td>" +
      "<td>" + r.o.platform + "</td>" +
      "<td>" + (r.o.customer || "N/A") + "</td>" +
      "<td>" + fmt(r.o.gross) + "</td>" +
      "<td><span class=\"status-badge " + badgeClass + "\">" + badgeText + "</span></td>" +
      "<td>" + r.days + "d</td>";
    body.appendChild(tr);
  });
}
```

Replace the entire block with:

```javascript
// === TRACKING TAB ===
let trackingFilter    = "all";
let _trackCache       = {};          // {trackingNumber: {data, ts}}
const _TRACK_TTL_MS   = 30 * 60 * 1000;
let _trackLastFetch   = null;

function switchPage(page) {
  document.getElementById("page-dashboard").style.display = page === "dashboard" ? "" : "none";
  document.getElementById("page-tracking").style.display  = page === "tracking"  ? "" : "none";
  document.querySelectorAll(".nav-tab").forEach(function(b, i) {
    b.classList.toggle("active", (i === 0 && page === "dashboard") || (i === 1 && page === "tracking"));
  });
  if (page === "tracking") renderTracking();
}

function daysSince(d) {
  return Math.floor((new Date() - new Date(d)) / 86400000);
}

function filterTracking(f) {
  trackingFilter = f;
  document.querySelectorAll(".track-filter").forEach(function(b) {
    b.classList.toggle("active", b.dataset.filter === f);
  });
  _renderTrackingRows();
}

function _liveStatus(order) {
  if (!order.tracking_number) return "no_tracking";
  var c = _trackCache[order.tracking_number];
  return c ? c.data.status : "loading";
}

function _statusBadge(status) {
  var map = {
    delivered:        {text: "Delivered",        cls: "fulfilled"},
    out_for_delivery: {text: "Out for Delivery",  cls: "fulfilled"},
    in_transit:       {text: "In Transit",        cls: "pending"},
    picked_up:        {text: "Picked Up",         cls: "pending"},
    exception:        {text: "Exception",         cls: "overdue"},
    label_created:    {text: "Label Created",     cls: ""},
    no_credentials:   {text: "No tracking",       cls: ""},
    unknown_carrier:  {text: "No tracking",       cls: ""},
    error:            {text: "Unavailable",       cls: ""},
    no_tracking:      {text: "—",                 cls: ""},
    loading:          {text: "…",                 cls: ""},
  };
  return map[status] || {text: status, cls: ""};
}

function _renderTrackingRows() {
  var orders = filteredOrders() || [];

  // Count buckets
  var counts = {total: orders.length, delivered: 0, transit: 0, pending: 0, exception: 0};
  orders.forEach(function(o) {
    var s = _liveStatus(o);
    if (s === "delivered")                                               counts.delivered++;
    else if (["in_transit","out_for_delivery","picked_up"].includes(s)) counts.transit++;
    else if (s === "exception")                                          counts.exception++;
    else                                                                 counts.pending++;
  });
  document.getElementById("trk-total").textContent     = counts.total;
  document.getElementById("trk-delivered").textContent = counts.delivered;
  document.getElementById("trk-transit").textContent   = counts.transit;
  document.getElementById("trk-pending").textContent   = counts.pending;
  document.getElementById("trk-exception").textContent = counts.exception;

  // Filter rows
  var rows = orders.filter(function(o) {
    if (trackingFilter === "all") return true;
    var s = _liveStatus(o);
    if (trackingFilter === "delivered")  return s === "delivered";
    if (trackingFilter === "in_transit") return ["in_transit","out_for_delivery","picked_up"].includes(s);
    if (trackingFilter === "exception")  return s === "exception";
    if (trackingFilter === "pending")    return !["delivered","in_transit","out_for_delivery","picked_up","exception"].includes(s);
    return true;
  });

  // Render rows
  var body = document.getElementById("tracking-body");
  body.innerHTML = "";
  rows.forEach(function(o) {
    var s      = _liveStatus(o);
    var badge  = _statusBadge(s);
    var cached = o.tracking_number ? _trackCache[o.tracking_number] : null;
    var carrier = (cached && cached.data.carrier) ? cached.data.carrier.toUpperCase()
                : (o.carrier || (o.tracking_number ? "…" : "—"));
    var eta = (cached && cached.data.eta) ? cached.data.eta.slice(0, 10) : "—";
    var dateStr = o.date instanceof Date ? o.date.toISOString().slice(0,10) : (o.date || "").slice(0,10);
    var platCls = o.platform === "shopify" ? "plat-shopify" : "plat-amazon";
    var badgeHtml = badge.cls
      ? "<span class='status-badge " + badge.cls + "'>" + badge.text + "</span>"
      : "<span style='color:var(--text-3)'>" + badge.text + "</span>";
    var tr = document.createElement("tr");
    tr.innerHTML =
      "<td>" + dateStr + "</td>" +
      "<td>" + o.id + "</td>" +
      "<td><span class='" + platCls + "'>" + o.platform + "</span></td>" +
      "<td style='color:var(--text-2)'>" + carrier + "</td>" +
      "<td>" + badgeHtml + "</td>" +
      "<td style='color:" + (eta !== "—" ? "var(--text)" : "var(--text-3)") + "'>" + eta + "</td>" +
      "<td style='color:var(--text-2)'>" + daysSince(o.date) + "d</td>";
    body.appendChild(tr);
  });

  // Timestamp
  if (_trackLastFetch) {
    var mins = Math.round((Date.now() - _trackLastFetch) / 60000);
    document.getElementById("trk-updated").textContent =
      "Updated " + (mins < 1 ? "just now" : mins + " min ago");
  }
}

async function renderTracking() {
  _renderTrackingRows();   // show immediately with cached data

  var orders  = filteredOrders() || [];
  var toFetch = [...new Set(
    orders
      .filter(function(o) {
        if (!o.tracking_number) return false;
        var c = _trackCache[o.tracking_number];
        return !c || (Date.now() - c.ts) > _TRACK_TTL_MS;
      })
      .map(function(o) { return o.tracking_number; })
  )];

  if (!toFetch.length) return;

  await Promise.all(toFetch.map(async function(num) {
    try {
      var resp = await fetch("/api/track?number=" + encodeURIComponent(num));
      var data = await resp.json();
      _trackCache[num] = {data: data, ts: Date.now()};
    } catch(e) {
      _trackCache[num] = {
        data: {status: "error", carrier: "unknown", eta: "", tracking_number: num, error: String(e)},
        ts: Date.now()
      };
    }
  }));

  _trackLastFetch = Date.now();
  _renderTrackingRows();
}

function refreshTracking() {
  _trackCache     = {};
  _trackLastFetch = null;
  var el = document.getElementById("trk-updated");
  if (el) el.textContent = "Refreshing…";
  renderTracking();
}
```

- [ ] **Step 2: Open dashboard locally and test manually**

```bash
cd /Users/rafaelpenott/IHE-Dashboard/src
python api_server.py
```

Open http://localhost:5000 in a browser.

Manual checks:
1. Click the **Tracking** tab — it should render immediately without errors
2. Status cards show 5 buckets (Total, Delivered, In Transit, Pending, Exception)
3. Filter buttons (All / Delivered / In Transit / Pending / Exception) toggle correctly
4. "↻ Refresh" button is visible and clickable
5. In demo mode (no API creds), all rows show "—" for Carrier and `…` for status (loading state briefly, then "No tracking")
6. Browser console shows no JS errors

- [ ] **Step 3: Commit**

```bash
cd /Users/rafaelpenott/IHE-Dashboard/src
git add dashboard.html
git commit -m "feat: live carrier tracking in Tracking tab"
```

---

## Task 7: Push to GitHub and deploy

- [ ] **Step 1: Run full test suite one final time**

```bash
cd /Users/rafaelpenott/IHE-Dashboard/src
pytest tests/ -v
```

Expected: all tests pass.

- [ ] **Step 2: Push to GitHub**

```bash
cd /Users/rafaelpenott/IHE-Dashboard/src
git push origin main
```

Render will auto-deploy from the `main` branch. The new service will spin up with the updated code.

- [ ] **Step 3: Add carrier credentials in Render dashboard**

Go to https://dashboard.render.com → select the `iberian-ham-dashboard` service → **Environment** tab.

Add these secret env vars (values from your FedEx/UPS/USPS developer accounts):
- `FEDEX_CLIENT_ID` — from FedEx Developer Portal
- `FEDEX_CLIENT_SECRET` — from FedEx Developer Portal
- `UPS_CLIENT_ID` — from UPS Developer Portal
- `UPS_CLIENT_SECRET` — from UPS Developer Portal
- `USPS_CLIENT_ID` — from USPS Developer Portal (api.usps.com)
- `USPS_CLIENT_SECRET` — from USPS Developer Portal

- [ ] **Step 4: Verify on production**

Open https://ihe-dashboard.onrender.com (wait for Render to finish deploy — ~2 min on free tier).

1. Click Tracking tab
2. For any Shopify orders with fulfillments, the Carrier column should show FedEx/UPS/USPS
3. After a few seconds, Carrier Status should update from "…" to the real status
4. "Updated just now" timestamp appears after first fetch
