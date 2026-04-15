# IHE Dashboard — Tracking Tab Fixes

**Date:** 2026-04-14
**Status:** Approved

## Goal

Fix the Tracking tab to show accurate data: UPS service type (Ground/2nd Day Air/etc.), correct estimated delivery date, latest event description with location, and remove Amazon orders (which have no tracking data).

## Current Problems

1. `_track_ups` returns `eta` (YYYYMMDD format) but frontend checks `estimated_delivery` — est. delivery never shows.
2. No `service` field extracted from UPS response — Service column would always show "—".
3. Amazon orders appear in the tracking tab despite having no tracking numbers.
4. Table columns include Platform (redundant — all Shopify) and lack Customer name and latest event detail.

## Design

### Backend — `api_server.py`

**Normalized tracking dict** — add two fields, fix one:

```python
{
  "tracking_number": str,
  "carrier": str,
  "status": str,
  "status_description": str,   # already exists — latest event description
  "service": str | None,        # NEW — e.g. "UPS Ground", "UPS 2nd Day Air"
  "estimated_delivery": str | None,  # RENAMED from "eta", ISO date "YYYY-MM-DD"
  "latest_location": str | None,     # already exists as "location" — rename for clarity
  "events": list,
  "cached": bool
}
```

**`_track_ups`:**
- Extract service: `trackResponse["trackResponse"]["shipment"][0]["service"]["description"]` → `service`
- Rename `eta` → `estimated_delivery`, convert `YYYYMMDD` → `YYYY-MM-DD` (insert dashes)
- Rename `location` → `latest_location`

**`_track_fedex`:**
- Add `service`: extract from FedEx response `serviceDetail.description` or `serviceDetail.type`
- Rename `eta`/delivery date field → `estimated_delivery` in `YYYY-MM-DD` format
- Rename `location` → `latest_location`

**`_track_usps`:**
- Add `service`: extract from USPS response `mailClass` or `serviceTypeCode`
- Rename delivery date field → `estimated_delivery` in `YYYY-MM-DD` format
- Rename `location` → `latest_location`

### Frontend — `dashboard.html`

**Filter Amazon orders out of tracking tab**

In `_renderTrackingRows`, skip orders where `o.platform === 'amazon'`:

```javascript
orders.forEach(function(o) {
  if (o.platform === 'amazon') return;  // Amazon has no tracking
  ...
});
```

Also in `renderTracking` (async fetch loop), skip Amazon orders the same way.

**New table columns**

Replace: `Date | Order | Platform | Carrier | Carrier Status | Est. Delivery | Days`

With: `Date | Order | Customer | Service | Status | Latest Event | Est. Delivery`

| Column | Source |
|---|---|
| Date | `o.date` |
| Order | `o.id` |
| Customer | `o.customer` |
| Service | `carrierEntry.data.service` or "—" |
| Status | badge from `_statusBadge(liveStatus)` |
| Latest Event | `carrierEntry.data.status_description` + `· ` + `carrierEntry.data.latest_location` (if location exists) |
| Est. Delivery | `carrierEntry.data.estimated_delivery` or "—" |

**Update `<thead>`** to match new columns (7 columns, same count).

**Status cards** — no change to card layout. Total card counts only Shopify orders now (Amazon filtered out).

## Error Handling

- UPS service field missing in response → `service: null` → frontend shows "—"
- Estimated delivery not available → `estimated_delivery: null` → frontend shows "—"
- Latest location empty → show only `status_description` without the `·` separator

## Out of Scope

- Amazon tracking (SP-API does not return tracking numbers in standard orders endpoint)
- Full event timeline / expand-on-click
- FedEx and USPS actual credential setup (credentials not yet entered in Render)
