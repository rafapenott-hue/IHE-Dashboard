"""Klaviyo REST client for weekly digest metrics.

Uses revision 2024-10-15 headers. On any per-endpoint error, that field returns 0
and the error is appended to the returned `errors` list, so the digest still ships.
"""
import datetime
import requests

BASE = "https://a.klaviyo.com/api"
REV  = "2024-10-15"


def _headers(api_key: str) -> dict:
    return {
        "Authorization": f"Klaviyo-API-Key {api_key}",
        "revision":      REV,
        "accept":        "application/json",
    }


def _get(api_key: str, path: str, params: dict, errors: list, label: str) -> dict:
    try:
        r = requests.get(f"{BASE}{path}", headers=_headers(api_key),
                         params=params, timeout=20)
        if not r.ok:
            errors.append(f"Klaviyo {label}: {r.status_code} {r.text[:120]}")
            return {}
        return r.json()
    except Exception as e:
        errors.append(f"Klaviyo {label}: {e}")
        return {}


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
        out["errors"].append("Klaviyo: no API key configured")
        return out

    start_iso = start.astimezone(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
    end_iso   = end.astimezone(datetime.timezone.utc).isoformat().replace("+00:00", "Z")

    camp = _get(api_key, "/campaigns",
                {"filter": f"greater-or-equal(send_time,{start_iso}),"
                           f"less-or-equal(send_time,{end_iso}),"
                           f"equals(messages.channel,'email')"},
                out["errors"], "campaigns")
    campaigns_list = camp.get("data", []) if camp else []
    out["campaigns_sent"] = len(campaigns_list)

    reports = _get(api_key, "/campaign-values-reports",
                   {"filter": f"greater-or-equal(send_time,{start_iso}),"
                              f"less-or-equal(send_time,{end_iso})"},
                   out["errors"], "reports")
    data = reports.get("data", []) if reports else []
    if data:
        open_rates  = [d.get("attributes", {}).get("statistics", {}).get("open_rate", 0) for d in data]
        click_rates = [d.get("attributes", {}).get("statistics", {}).get("click_rate", 0) for d in data]
        revenues    = [d.get("attributes", {}).get("statistics", {}).get("revenue", 0) for d in data]
        out["open_rate"]          = round(sum(open_rates) / len(open_rates) * 100, 1)
        out["click_rate"]         = round(sum(click_rates) / len(click_rates) * 100, 1)
        out["attributed_revenue"] = round(sum(revenues), 2)

    subs = _get(api_key, "/events",
                {"filter": f"greater-or-equal(datetime,{start_iso}),"
                           f"less-or-equal(datetime,{end_iso}),"
                           f"equals(metric.name,'Subscribed to List')",
                 "page[size]": 100},
                out["errors"], "subscribed")
    out["new_subscribers"] = len(subs.get("data", [])) if subs else 0

    unsubs = _get(api_key, "/events",
                  {"filter": f"greater-or-equal(datetime,{start_iso}),"
                             f"less-or-equal(datetime,{end_iso}),"
                             f"equals(metric.name,'Unsubscribed')",
                   "page[size]": 100},
                  out["errors"], "unsubscribed")
    out["unsubscribes"] = len(unsubs.get("data", [])) if unsubs else 0

    return out
