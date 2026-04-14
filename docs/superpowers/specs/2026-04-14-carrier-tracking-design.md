# IHE Dashboard — Live Carrier Tracking

**Date:** 2026-04-14
**Status:** Approved

## Goal

Connect the existing Tracking tab to real FedEx, UPS, and USPS carrier APIs so it shows live shipment status (Delivered, In Transit, Out for Delivery, Exception) instead of deriving status from order fulfillment flags.

## Current State

- `api_server.py` has carrier auth helper functions (`_detect_carrier`, `_fedex_token`, `_ups_token`) but no `/api/track` route is exposed.
- `shopify_fetch` does not request the `fulfillments` field, so tracking numbers never reach the frontend.
- The Tracking tab derives status from `fulfillment_status` ("fulfilled" / "unfulfilled") — no real carrier data.
- Amazon SP-API orders endpoint does not return tracking numbers; these will show "No tracking" gracefully.

## Design

### Backend — `api_server.py`

**1. Carrier tracking functions (new)**

Add three functions that call each carrier's tracking API and return a normalized dict:

```python
{
  "tracking_number": str,
  "carrier": str,           # "fedex" | "ups" | "usps" | "unknown"
  "status": str,            # "delivered" | "out_for_delivery" | "in_transit" | "pending" | "exception"
  "status_detail": str,     # Human-readable latest event, e.g. "Delivered - Front Door"
  "estimated_delivery": str | None,  # ISO date string or None
  "last_location": str | None,       # "Miami, FL" or None
  "last_event_time": str | None,     # ISO datetime or None
  "cached": bool
}
```

- `_track_fedex(tracking_number)` — calls `https://apis.fedex.com/track/v1/trackingnumbers` with OAuth token from `_fedex_token()`.
- `_track_ups(tracking_number)` — calls `https://onlinetools.ups.com/api/track/v1/details/{number}` with OAuth token from `_ups_token()`.
- `_track_usps(tracking_number)` — calls the USPS Web Tools TrackV2 XML API using `USPS_USERID` env var.

All three use `_TRACK_CACHE` (already defined, 30-min TTL `_TRACK_CACHE_TTL`). On any carrier API error, return `status: "pending"` with `error` field — never crash the endpoint.

**2. `/api/track` endpoint (new)**

```
GET /api/track?number=XXX
```

- Reads tracking number from query param.
- Calls `_detect_carrier(number)` to pick the right function.
- Returns the normalized tracking dict above.
- Returns HTTP 200 in all cases (errors included in body) so the frontend can handle gracefully.

**3. Shopify fetch — include fulfillments**

Update `shopify_fetch` fields param to add `fulfillments`.

Update `normalize_shopify_order` to extract from the first fulfillment:
- `tracking_number`: `fulfillments[0].tracking_number` or `None`
- `carrier`: `fulfillments[0].tracking_company` or auto-detected via `_detect_carrier`

**4. `render.yaml` — new env vars**

Add (all `sync: false`):
- `FEDEX_CLIENT_ID`
- `FEDEX_CLIENT_SECRET`
- `UPS_CLIENT_ID`
- `UPS_CLIENT_SECRET`
- `USPS_USERID`

### Frontend — `dashboard.html`

**1. Status cards (5 instead of 4)**

Replace existing 4 cards (Total / Shipped / Unshipped / Overdue) with:
- Total Orders (neutral)
- Delivered (green)
- In Transit (accent/indigo)
- Pending (amber)
- Exception (red)

**2. Filter buttons**

Replace All / Shipped / Unshipped / Overdue with:
All / Delivered / In Transit / Pending / Exception

**3. Table columns**

Replace: `Date | Order | Platform | Customer | Gross | Status | Days`
With: `Date | Order | Platform | Customer | Carrier | Carrier Status | Est. Delivery | Days`

**4. `renderTracking()` — fetch live data on tab open**

When the Tracking tab opens:
1. Render orders immediately with a spinner in the Carrier Status column for rows that have a tracking number.
2. Collect all unique tracking numbers from the current order set.
3. Fetch `/api/track?number=XXX` for each in parallel via `Promise.all`.
4. Cache results in a JS object `_trackCache = { number: { data, ts } }` with a 30-min TTL — re-opening the tab uses the cache.
5. Populate Carrier, Carrier Status, and Est. Delivery cells once responses arrive.
6. Orders without a tracking number show "—" in carrier columns.

**5. Refresh button + timestamp**

- "↻ Refresh" button clears `_trackCache` and re-renders.
- "Updated X min ago" label derived from the oldest cache entry timestamp.

**6. Status → color mapping**

| Carrier status | Badge color |
|---|---|
| delivered | green (`--success`) |
| out_for_delivery | indigo (`--accent`) |
| in_transit | indigo (`--accent`) |
| pending | amber (`--warning`) |
| exception | red (`--danger`) |
| no tracking | gray (`--text-3`) |

## Error Handling

- Carrier API down → row shows "Unavailable" in amber, no crash.
- Missing env vars (no FedEx/UPS/USPS credentials) → `/api/track` returns `{status: "pending", error: "carrier not configured"}` — frontend shows "No tracking".
- Amazon orders → no tracking number available from SP-API; frontend shows "—" without attempting a lookup.

## Out of Scope

- Full carrier event timeline (Option C) — not included.
- Amazon tracking number lookup via Shipments API — requires additional SP-API permissions.
- Push notifications for delivery events.
