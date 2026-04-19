"""Klaviyo REST client for weekly digest metrics.

API revision 2024-10-15. On any per-endpoint error, that field returns 0 and
the error is appended to the returned `errors` list, so the digest still ships.

Verified endpoint behavior (2026-04-19):
- GET /api/campaigns only accepts `scheduled_at` (NOT `send_time`) as a filter.
- POST /api/campaign-values-reports takes a `timeframe: {start, end}` body
  (no `key` field) and requires `conversion_metric_id` (we look up "Placed Order").
- POST /api/metric-aggregates returns totals for a metric in a date window.
"""
import datetime
import requests

BASE = "https://a.klaviyo.com/api"
REV  = "2024-10-15"


def _headers(api_key: str, include_content_type: bool = False) -> dict:
    h = {
        "Authorization": f"Klaviyo-API-Key {api_key}",
        "revision":      REV,
        "accept":        "application/json",
    }
    if include_content_type:
        h["content-type"] = "application/json"
    return h


def _get(api_key: str, path: str, params: dict, errors: list, label: str) -> dict:
    try:
        r = requests.get(f"{BASE}{path}", headers=_headers(api_key),
                         params=params, timeout=20)
        if not r.ok:
            errors.append(f"{label}: {r.status_code} {r.text[:200]}")
            return {}
        return r.json()
    except Exception as e:
        errors.append(f"{label}: {type(e).__name__}: {str(e)[:160]}")
        return {}


def _post(api_key: str, path: str, body: dict, errors: list, label: str) -> dict:
    try:
        r = requests.post(f"{BASE}{path}", headers=_headers(api_key, True),
                          json=body, timeout=20)
        if not r.ok:
            errors.append(f"{label}: {r.status_code} {r.text[:200]}")
            return {}
        return r.json()
    except Exception as e:
        errors.append(f"{label}: {type(e).__name__}: {str(e)[:160]}")
        return {}


def _iso_utc(dt: datetime.datetime) -> str:
    return dt.astimezone(datetime.timezone.utc).isoformat().replace("+00:00", "Z")


def _find_metric_id(api_key: str, name: str, errors: list) -> str:
    data = _get(api_key, "/metrics", {"page[size]": 200}, errors, f"metrics({name})")
    for m in data.get("data", []):
        if m.get("attributes", {}).get("name") == name:
            return m["id"]
    return ""


def _sum_metric_aggregate(api_key: str, metric_id: str,
                          start: datetime.datetime, end: datetime.datetime,
                          errors: list, label: str) -> int:
    if not metric_id:
        return 0
    body = {"data": {"type": "metric-aggregate", "attributes": {
        "metric_id":    metric_id,
        "interval":     "day",
        "measurements": ["count"],
        "filter": [
            f"greater-or-equal(datetime,{_iso_utc(start)})",
            f"less-than(datetime,{_iso_utc(end)})",
        ],
        "page_size": 5000,
    }}}
    resp = _post(api_key, "/metric-aggregates", body, errors, label)
    data = resp.get("data", {}).get("attributes", {}).get("data", [])
    total = 0.0
    for bucket in data:
        total += sum(bucket.get("measurements", {}).get("count", []))
    return int(total)


def fetch_weekly_metrics(api_key: str,
                         start: datetime.datetime,
                         end: datetime.datetime) -> dict:
    out = {
        "campaigns_sent":     0,
        "open_rate":          0.0,
        "click_rate":         0.0,
        "attributed_revenue": 0.0,
        "new_subscribers":    0,
        "unsubscribes":       0,
        "errors":             [],
    }
    if not api_key:
        out["errors"].append("no API key configured")
        return out

    start_iso = _iso_utc(start)
    end_iso   = _iso_utc(end)

    # 1. Campaigns sent in window (filter uses scheduled_at, not send_time).
    campaigns_resp = _get(
        api_key, "/campaigns",
        {"filter": (f"greater-or-equal(scheduled_at,{start_iso}),"
                    f"less-or-equal(scheduled_at,{end_iso}),"
                    f'equals(messages.channel,"email")'),
         "page[size]": 50},
        out["errors"], "campaigns",
    )
    sent_campaigns = [
        c for c in campaigns_resp.get("data", [])
        if c.get("attributes", {}).get("status") == "Sent"
    ]
    out["campaigns_sent"] = len(sent_campaigns)

    # 2. Campaign performance report for the window.
    placed_order_id = _find_metric_id(api_key, "Placed Order", out["errors"])
    if placed_order_id:
        body = {"data": {"type": "campaign-values-report", "attributes": {
            "statistics":           ["opens", "clicks", "conversions",
                                     "conversion_value", "delivered"],
            "timeframe":            {"start": start_iso, "end": end_iso},
            "conversion_metric_id": placed_order_id,
        }}}
        report = _post(api_key, "/campaign-values-reports", body,
                       out["errors"], "campaign-report")
        results = report.get("data", {}).get("attributes", {}).get("results", [])
        if results:
            opens     = sum(r["statistics"].get("opens", 0)            for r in results)
            clicks    = sum(r["statistics"].get("clicks", 0)           for r in results)
            delivered = sum(r["statistics"].get("delivered", 0)        for r in results)
            revenue   = sum(r["statistics"].get("conversion_value", 0) for r in results)
            out["open_rate"]          = round(opens / delivered * 100, 1)  if delivered else 0
            out["click_rate"]         = round(clicks / delivered * 100, 1) if delivered else 0
            out["attributed_revenue"] = round(revenue, 2)

    # 3. New subscribers via Subscribed to List metric.
    subs_id = _find_metric_id(api_key, "Subscribed to List", out["errors"])
    out["new_subscribers"] = _sum_metric_aggregate(
        api_key, subs_id, start, end, out["errors"], "subs-aggregate"
    )

    # 4. Unsubscribes via Unsubscribed from Email Marketing metric.
    unsubs_id = _find_metric_id(api_key, "Unsubscribed from Email Marketing",
                                 out["errors"])
    out["unsubscribes"] = _sum_metric_aggregate(
        api_key, unsubs_id, start, end, out["errors"], "unsubs-aggregate"
    )

    return out
