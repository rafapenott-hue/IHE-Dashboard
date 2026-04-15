# Tracking Tab Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix carrier tracking to show service type, accurate estimated delivery, and latest event detail; remove Amazon orders from the tracking tab.

**Architecture:** Two files change. `api_server.py` — three `_track_*` functions get new fields (`service`, `latest_location`) and rename `eta` → `estimated_delivery` with normalized ISO format. `dashboard.html` — `_renderTrackingRows` filters Amazon, new thead, and new row template using the corrected field names.

**Tech Stack:** Python 3.9, Flask, requests; vanilla JS; pytest + unittest.mock

---

## File Map

| File | Change |
|---|---|
| `api_server.py` | `_track_ups`, `_track_fedex`, `_track_usps` — add `service`, rename `eta`→`estimated_delivery`, rename `location`→`latest_location` |
| `tests/test_tracking.py` | Update assertions to use new field names; add service assertions |
| `dashboard.html` | Filter Amazon, new `<thead>`, rewrite row template in `_renderTrackingRows` |

---

## Task 1: Update tests for new field names + service field

**Files:**
- Modify: `tests/test_tracking.py`

- [ ] **Step 1: Update `_ups_response` helper to include service**

In `tests/test_tracking.py`, change `_ups_response` (line ~100):

```python
def _ups_response(description, city="Atlanta", state="GA", eta_date="20260415", service="UPS Ground"):
    return {
        "trackResponse": {"shipment": [{"service": {"description": service}, "package": [{
            "activity": [{
                "location": {"address": {"city": city, "stateOrProvinceCode": state}},
                "status": {"description": description},
                "date": "20260413", "time": "214700"
            }],
            "deliveryDate": [{"date": eta_date}],
        }]}]}
    }
```

- [ ] **Step 2: Update `test_track_ups_in_transit` to check new fields**

Replace the existing `test_track_ups_in_transit` test:

```python
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
    assert result["estimated_delivery"] == "2026-04-15"   # was eta=="20260415"
    assert result["service"] == "UPS Ground"
    assert result["latest_location"] == "Atlanta, GA"     # was location
    assert "eta" not in result                            # old field gone
```

- [ ] **Step 3: Update `test_track_fedex_delivered` to check new fields**

Replace `test_track_fedex_delivered` (uses `_fedex_response` which returns eta `"2026-04-14T00:00:00"`):

```python
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
    assert result["latest_location"] == "Miami, FL"       # was location
    assert result["estimated_delivery"] == "2026-04-14"   # was eta (sliced from ISO)
    assert "eta" not in result
```

Also update `_fedex_response` helper to include a `serviceDetail` field:

```python
def _fedex_response(code, description, city="Miami", state="FL", eta="2026-04-14T00:00:00"):
    return {
        "output": {"completeTrackResults": [{"trackResults": [{
            "latestStatusDetail": {
                "code": code, "description": description,
                "scanLocation": {"city": city, "stateOrProvinceCode": state}
            },
            "serviceDetail": {"description": "FedEx Ground"},
            "estimatedDeliveryTimeWindow": {"window": {"ends": eta}},
            "scanEvents": [],
        }]}]}
    }
```

And add a service assertion to `test_track_fedex_delivered`:

```python
    assert result["service"] == "FedEx Ground"
```

- [ ] **Step 4: Update `test_track_usps_delivered` to check new fields**

The `_usps_response` helper already returns `expectedDeliveryDate: "2026-04-16"` — USPS already returns ISO format so no conversion needed. Update assertions:

```python
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
    assert result["estimated_delivery"] == "2026-04-16"   # was eta
    assert result["latest_location"] == "Miami, FL"       # was location
    assert "eta" not in result
```

Also update `_usps_response` to include a `mailClass` field:

```python
def _usps_response(event_type, city="Miami", state="FL", eta="2026-04-16"):
    return {
        "trackingEvents": [{
            "eventTimestamp": "2026-04-14T08:00:00",
            "eventType": event_type,
            "eventCity": city, "eventState": state, "eventZIPCode": "33101"
        }],
        "expectedDeliveryDate": eta,
        "mailClass": "USPS Ground Advantage"
    }
```

And add to `test_track_usps_delivered`:

```python
    assert result["service"] == "USPS Ground Advantage"
```

- [ ] **Step 5: Run tests — expect failures**

```bash
cd /Users/rafaelpenott/IHE-Dashboard/src
python3 -m pytest tests/test_tracking.py -v 2>&1 | tail -20
```

Expected: several FAILED (AssertionError on `estimated_delivery`, `latest_location`, `service` — old code still uses `eta`, `location`, no service field)

---

## Task 2: Fix `_track_ups` in backend

**Files:**
- Modify: `api_server.py` lines 204–249

- [ ] **Step 1: Replace `_track_ups` result dict**

Find and replace the `result = { ... }` block inside `_track_ups` (currently lines ~236–244):

```python
        shipment    = r.json().get("trackResponse", {}).get("shipment", [{}])[0]
        pkg         = shipment.get("package", [{}])[0]
        service_raw = shipment.get("service", {}).get("description", "") or None
        acts        = pkg.get("activity", [])
        latest      = acts[0] if acts else {}
        latest_desc = latest.get("status", {}).get("description", "")
        events = [
            {
                "timestamp":   f"{a.get('date','')} {a.get('time','')}".strip(),
                "status":      _norm_status("ups", a.get("status", {}).get("description", "")),
                "description": a.get("status", {}).get("description", ""),
                "location":    ", ".join(p for p in [a.get('location',{}).get('address',{}).get('city',''), a.get('location',{}).get('address',{}).get('stateOrProvinceCode','')] if p),
            }
            for a in acts
        ]
        eta_entry   = pkg.get("deliveryDate", [{}])[0] if pkg.get("deliveryDate") else {}
        eta_raw     = eta_entry.get("date", "")   # "20260421"
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
```

The full updated `_track_ups` function:

```python
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
        return {"carrier": "ups", "tracking_number": tn, "status": "auth_error", "events": [],
                "error": "UPS OAuth token fetch failed — check UPS_CLIENT_ID/SECRET in Render"}
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
```

- [ ] **Step 2: Run UPS tests**

```bash
python3 -m pytest tests/test_tracking.py::test_track_ups_in_transit tests/test_tracking.py::test_track_ups_no_token tests/test_tracking.py::test_track_ups_cache_hit -v
```

Expected: all 3 PASS

---

## Task 3: Fix `_track_fedex` in backend

**Files:**
- Modify: `api_server.py` lines 159–201

- [ ] **Step 1: Replace `_track_fedex` result dict**

The full updated `_track_fedex` function:

```python
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
```

- [ ] **Step 2: Run FedEx tests**

```bash
python3 -m pytest tests/test_tracking.py::test_track_fedex_delivered tests/test_tracking.py::test_track_fedex_no_token tests/test_tracking.py::test_track_fedex_cache_hit tests/test_tracking.py::test_track_fedex_api_error -v
```

Expected: all 4 PASS

---

## Task 4: Fix `_track_usps` in backend

**Files:**
- Modify: `api_server.py` lines 252–289

- [ ] **Step 1: Replace `_track_usps` result dict**

The full updated `_track_usps` function:

```python
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
                "location":    ", ".join(p for p in [ev.get('eventCity',''), ev.get('eventState',''), ev.get('eventZIPCode','')] if p),
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
```

- [ ] **Step 2: Run all tests**

```bash
python3 -m pytest tests/test_tracking.py -v
```

Expected: 21 PASS, 0 FAIL

- [ ] **Step 3: Commit**

```bash
git add api_server.py tests/test_tracking.py
git commit -m "feat: add service/estimated_delivery/latest_location to carrier tracking"
```

---

## Task 5: Update tracking tab frontend

**Files:**
- Modify: `dashboard.html`

Context: the tracking tab JS lives at the bottom of the file (`// === TRACKING TAB ===` section). The `<thead>` is around line 808. The `_renderTrackingRows` function renders rows.

- [ ] **Step 1: Update `<thead>` columns**

Find:
```html
<th>Date</th><th>Order</th><th>Platform</th><th>Carrier</th><th>Carrier Status</th><th>Est. Delivery</th><th>Days</th>
```

Replace with:
```html
<th>Date</th><th>Order</th><th>Customer</th><th>Service</th><th>Status</th><th>Latest Event</th><th>Est. Delivery</th>
```

- [ ] **Step 2: Rewrite `_renderTrackingRows`**

Replace the entire `_renderTrackingRows` function (from `function _renderTrackingRows() {` to its closing `}`) with:

```javascript
function _renderTrackingRows() {
  var orders = (typeof filteredOrders === "function" ? filteredOrders() : S.orders) || [];
  var total = 0, nDelivered = 0, nTransit = 0, nPending = 0, nException = 0;
  var rows = [];

  orders.forEach(function(o) {
    if (o.platform === 'amazon') return;   // Amazon has no tracking data
    total++;
    var liveStatus = _liveStatus(o);
    var bucket;
    if (liveStatus === "delivered") bucket = "delivered";
    else if (liveStatus === "in_transit" || liveStatus === "out_for_delivery" || liveStatus === "picked_up") bucket = "in_transit";
    else if (liveStatus === "exception") bucket = "exception";
    else bucket = "pending";

    if (liveStatus !== "no_tracking" && liveStatus !== "loading" && liveStatus !== "unknown" && liveStatus !== "unknown_carrier") {
      if (bucket === "delivered") nDelivered++;
      else if (bucket === "in_transit") nTransit++;
      else if (bucket === "exception") nException++;
      else nPending++;
    }

    var show = trackingFilter === "all" || trackingFilter === bucket ||
               (trackingFilter === "in_transit" && (liveStatus === "out_for_delivery" || liveStatus === "picked_up"));
    if (show) rows.push({ o: o, liveStatus: liveStatus, bucket: bucket });
  });

  document.getElementById("trk-total").textContent     = total;
  document.getElementById("trk-delivered").textContent = nDelivered;
  document.getElementById("trk-transit").textContent   = nTransit;
  document.getElementById("trk-pending").textContent   = nPending;
  document.getElementById("trk-exception").textContent = nException;

  if (_trackLastFetch) {
    var minsAgo = Math.floor((Date.now() - _trackLastFetch) / 60000);
    document.getElementById("trk-updated").textContent = minsAgo < 1 ? "Updated just now" : "Updated " + minsAgo + " min ago";
  } else {
    document.getElementById("trk-updated").textContent = "";
  }

  var body = document.getElementById("tracking-body");
  body.innerHTML = "";
  rows.forEach(function(r) {
    var badge        = _statusBadge(r.liveStatus);
    var carrierEntry = r.o.tracking_number ? _trackCache[r.o.tracking_number] : null;
    var data         = carrierEntry ? carrierEntry.data : null;
    var service      = (data && data.service) ? data.service : "—";
    var estDelivery  = (data && data.estimated_delivery) ? data.estimated_delivery.slice(0,10) : "—";
    var latestDesc   = (data && data.status_description) ? data.status_description : "";
    var latestLoc    = (data && data.latest_location) ? data.latest_location : "";
    var latestEvent  = latestDesc ? (latestLoc ? latestDesc + " · " + latestLoc : latestDesc) : "—";
    var dateStr      = r.o.date instanceof Date ? r.o.date.toISOString().slice(0,10) : String(r.o.date).slice(0,10);
    var tr = document.createElement("tr");
    tr.innerHTML =
      "<td>" + dateStr + "</td>" +
      "<td>#" + r.o.id + "</td>" +
      "<td>" + (r.o.customer || "—") + "</td>" +
      "<td>" + service + "</td>" +
      "<td><span class=\"status-badge " + badge.cls + "\">" + badge.text + "</span></td>" +
      "<td style=\"font-size:12px;color:var(--text-3)\">" + latestEvent + "</td>" +
      "<td>" + estDelivery + "</td>";
    body.appendChild(tr);
  });
}
```

- [ ] **Step 3: Update `renderTracking` async fetch to also skip Amazon**

In `renderTracking()`, find:
```javascript
  orders.forEach(function(o) {
    if (!o.tracking_number) return;
```

Replace with:
```javascript
  orders.forEach(function(o) {
    if (o.platform === 'amazon' || !o.tracking_number) return;
```

- [ ] **Step 4: Commit**

```bash
git add dashboard.html
git commit -m "feat: tracking tab shows service, latest event, accurate est delivery; removes Amazon orders"
```

---

## Task 6: Push and verify

**Files:** none

- [ ] **Step 1: Push to GitHub**

```bash
git remote set-url origin https://<PAT>@github.com/rafapenott-hue/IHE-Dashboard.git
git push origin main
git remote set-url origin https://github.com/rafapenott-hue/IHE-Dashboard.git
```

- [ ] **Step 2: Wait for Render deploy, then verify**

```bash
curl -s "https://ihe-dashboard.onrender.com/api/track?number=1ZC1B491YN77608743&carrier=UPS" | python3 -m json.tool
```

Expected: response includes `"service": "UPS Ground"` (or actual service), `"estimated_delivery": "2026-04-21"`, `"latest_location": "Miami"`, no `"eta"` field.

- [ ] **Step 3: Hard refresh dashboard and check Tracking tab**

Open https://ihe-dashboard.onrender.com, press `Cmd+Shift+R`, click Tracking tab. Verify:
- No Amazon orders in table
- Service column shows e.g. "UPS Ground"
- Latest Event column shows e.g. "Arrived at Facility · Miami"
- Est. Delivery shows formatted date e.g. "2026-04-21"
