import sys, os, time
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
            "serviceDetail": {"description": "FedEx Ground"},
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
    assert result["latest_location"] == "Miami, FL"
    assert result["estimated_delivery"] == "2026-04-14"
    assert result["service"] == "FedEx Ground"
    assert "eta" not in result

def test_track_fedex_no_token():
    with patch("api_server._fedex_token", return_value=None):
        result = _track_fedex("794644823401")
    assert result["status"] == "no_credentials"

def test_track_fedex_cache_hit():
    cached_data = {"carrier": "fedex", "tracking_number": "794644823401",
                   "status": "delivered", "events": []}
    with patch.dict(api_server._TRACK_CACHE,
                    {"794644823401": {"data": cached_data, "ts": time.time()}}), \
         patch("api_server._fedex_token") as mock_token:
        result = _track_fedex("794644823401")
    assert result["status"] == "delivered"
    mock_token.assert_not_called()  # no HTTP call made

def test_track_fedex_api_error():
    with patch("api_server._fedex_token", return_value="tok"), \
         patch("api_server.requests.post", side_effect=Exception("timeout")), \
         patch.dict(api_server._TRACK_CACHE, {}, clear=True):
        result = _track_fedex("794644823401")
    assert result["status"] == "error"
    assert "timeout" in result["error"]


# ── _track_ups ─────────────────────────────────────────────────

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

def test_track_ups_in_transit():
    mock_resp = MagicMock()
    mock_resp.json.return_value = _ups_response("In Transit")
    mock_resp.raise_for_status = MagicMock()
    with patch("api_server._ups_token", return_value="tok"), \
         patch("api_server.requests.get", return_value=mock_resp), \
         patch.dict(os.environ, {"UPS_CLIENT_ID": "x", "UPS_CLIENT_SECRET": "y"}), \
         patch.dict(api_server._TRACK_CACHE, {}, clear=True):
        result = _track_ups("1Z999AA10123456784")
    assert result["status"] == "in_transit"
    assert result["carrier"] == "ups"
    assert result["estimated_delivery"] == "2026-04-15"
    assert result["service"] == "UPS Ground"
    assert result["latest_location"] == "Atlanta, GA"
    assert "eta" not in result

def test_track_ups_no_credentials():
    with patch.dict(os.environ, {"UPS_CLIENT_ID": "", "UPS_CLIENT_SECRET": ""}):
        result = _track_ups("1Z999AA10123456784")
    assert result["status"] == "no_credentials"

def test_track_ups_auth_error():
    with patch.dict(os.environ, {"UPS_CLIENT_ID": "x", "UPS_CLIENT_SECRET": "y"}), \
         patch("api_server._ups_token", return_value=None), \
         patch.dict(api_server._TRACK_CACHE, {}, clear=True):
        result = _track_ups("1Z999AA10123456784")
    assert result["status"] == "auth_error"

def test_track_ups_cache_hit():
    cached_data = {"carrier": "ups", "tracking_number": "1Z999AA10123456784",
                   "status": "in_transit", "events": []}
    with patch.dict(api_server._TRACK_CACHE,
                    {"1Z999AA10123456784": {"data": cached_data, "ts": time.time()}}), \
         patch("api_server._ups_token") as mock_token:
        result = _track_ups("1Z999AA10123456784")
    assert result["status"] == "in_transit"
    mock_token.assert_not_called()


# ── _track_usps ────────────────────────────────────────────────

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
    assert result["estimated_delivery"] == "2026-04-16"
    assert result["latest_location"] == "Miami, FL"
    assert result["service"] == "USPS Ground Advantage"
    assert "eta" not in result

def test_track_usps_no_token():
    with patch("api_server._usps_token", return_value=None):
        result = _track_usps("9400111899223397846233")
    assert result["status"] == "no_credentials"

def test_track_usps_cache_hit():
    cached_data = {"carrier": "usps", "tracking_number": "9400111899223397846233",
                   "status": "delivered", "events": []}
    with patch.dict(api_server._TRACK_CACHE,
                    {"9400111899223397846233": {"data": cached_data, "ts": time.time()}}), \
         patch("api_server._usps_token") as mock_token:
        result = _track_usps("9400111899223397846233")
    assert result["status"] == "delivered"
    mock_token.assert_not_called()


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
