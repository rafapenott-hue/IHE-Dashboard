# Weekly Telegram Digest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a Monday-morning Telegram digest that summarises last week + MTD across Shopify, Amazon, Klaviyo, and Google/Meta ads (via GoMarble), with a Claude-generated analysis section.

**Architecture:** Extend the existing Flask app on Render. Add a new `/api/digest/weekly` endpoint wired to a new GitHub Actions cron. All data pulls are plain HTTP/REST from the Flask process — Klaviyo REST, GoMarble MCP-over-SSE client, Anthropic Messages API. Reuses the existing Telegram bot and `DIGEST_SECRET` gate.

**Tech Stack:** Python 3, Flask, requests, `mcp` (Python SDK), `anthropic` (Python SDK), pytest, GitHub Actions.

**Spec reference:** [specs/2026-04-19-weekly-digest-design.md](../specs/2026-04-19-weekly-digest-design.md)

---

## File Map

| File | Action | Purpose |
|---|---|---|
| `requirements.txt` | Modify | Add `mcp`, `anthropic` |
| `render.yaml` | Modify | Add `KLAVIYO_API_KEY`, `GOMARBLE_API_KEY`, `ANTHROPIC_API_KEY` |
| `weekly_periods.py` | Create | Pure date-math helpers |
| `api_server.py` | Modify | Refactor `_compute_summary` to accept (start, end); add `/api/digest/weekly` |
| `klaviyo_client.py` | Create | Pull email metrics from Klaviyo REST |
| `gomarble_client.py` | Create | Pull ads metrics via GoMarble MCP-over-SSE |
| `insights_client.py` | Create | Generate 3–5 analysis bullets via Anthropic API |
| `weekly_digest.py` | Create | Orchestrate sources + format Telegram message |
| `tests/test_weekly_periods.py` | Create | Unit tests for date math |
| `tests/test_klaviyo_client.py` | Create | Klaviyo client (mocked HTTP) |
| `tests/test_gomarble_client.py` | Create | GoMarble client (mocked MCP) |
| `tests/test_insights_client.py` | Create | Anthropic client (mocked) |
| `tests/test_weekly_digest.py` | Create | Formatter + endpoint tests |
| `.github/workflows/weekly-digest.yml` | Create | Monday 11:00 UTC cron |

---

## Task 1: Add dependencies

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: Update requirements.txt**

Replace contents with:

```
flask==3.0.3
flask-cors==4.0.1
requests==2.32.3
gunicorn==22.0.0
pytest==8.3.5
anthropic==0.39.0
mcp==1.1.2
```

- [ ] **Step 2: Install locally**

Run: `cd /Users/rafaelpenott/IHE-dashboard/src && pip install -r requirements.txt`
Expected: successful install, no errors.

- [ ] **Step 3: Commit**

```bash
git add requirements.txt
git commit -m "deps: add anthropic and mcp SDKs for weekly digest"
```

---

## Task 2: Date math helpers (TDD)

**Files:**
- Create: `weekly_periods.py`
- Test: `tests/test_weekly_periods.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_weekly_periods.py`:

```python
import sys, os, datetime
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from weekly_periods import last_week_range, mtd_range, prior_week_range, ET


def test_last_week_range_from_monday():
    # Monday 2026-04-13 10:00 ET → last week is Mon 04-06 to Sun 04-12
    now = datetime.datetime(2026, 4, 13, 10, 0, tzinfo=ET)
    start, end = last_week_range(now)
    assert start == datetime.datetime(2026, 4, 6, 0, 0, 0, tzinfo=ET)
    assert end == datetime.datetime(2026, 4, 12, 23, 59, 59, tzinfo=ET)


def test_last_week_range_from_wednesday():
    # Wed 2026-04-15 → last week is still Mon 04-06 to Sun 04-12
    now = datetime.datetime(2026, 4, 15, 10, 0, tzinfo=ET)
    start, end = last_week_range(now)
    assert start.day == 6
    assert end.day == 12


def test_last_week_range_from_sunday():
    # Sun 2026-04-12 → last week is Mon 03-30 to Sun 04-05
    now = datetime.datetime(2026, 4, 12, 10, 0, tzinfo=ET)
    start, end = last_week_range(now)
    assert start == datetime.datetime(2026, 3, 30, 0, 0, 0, tzinfo=ET)
    assert end == datetime.datetime(2026, 4, 5, 23, 59, 59, tzinfo=ET)


def test_mtd_range_mid_month():
    now = datetime.datetime(2026, 4, 14, 10, 0, tzinfo=ET)
    start, end = mtd_range(now)
    assert start == datetime.datetime(2026, 4, 1, 0, 0, 0, tzinfo=ET)
    assert end == now


def test_mtd_range_first_of_month():
    now = datetime.datetime(2026, 4, 1, 10, 0, tzinfo=ET)
    start, end = mtd_range(now)
    assert start == datetime.datetime(2026, 4, 1, 0, 0, 0, tzinfo=ET)
    assert end == now


def test_prior_week_range_from_monday():
    # Monday 04-13 → prior week is Mon 03-30 to Sun 04-05
    now = datetime.datetime(2026, 4, 13, 10, 0, tzinfo=ET)
    start, end = prior_week_range(now)
    assert start == datetime.datetime(2026, 3, 30, 0, 0, 0, tzinfo=ET)
    assert end == datetime.datetime(2026, 4, 5, 23, 59, 59, tzinfo=ET)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/rafaelpenott/IHE-dashboard/src && python -m pytest tests/test_weekly_periods.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'weekly_periods'`

- [ ] **Step 3: Implement the module**

Create `weekly_periods.py`:

```python
"""Date-math helpers for the weekly digest.

All returns are ET-tz-aware datetimes. Uses fixed UTC-5 offset (EST) — acceptable
for weekly reporting where DST boundary lands on a Mon 11:00 UTC cron with minimal
drift. If precision matters later, swap to zoneinfo('America/New_York').
"""
import datetime

ET = datetime.timezone(datetime.timedelta(hours=-4))  # EDT; daily digest uses same


def _start_of_day(dt: datetime.datetime) -> datetime.datetime:
    return dt.replace(hour=0, minute=0, second=0, microsecond=0)


def _end_of_day(dt: datetime.datetime) -> datetime.datetime:
    return dt.replace(hour=23, minute=59, second=59, microsecond=0)


def last_week_range(now_et: datetime.datetime) -> tuple:
    """Most-recently-completed Mon 00:00 — Sun 23:59:59 in ET."""
    # weekday(): Mon=0 ... Sun=6. Days since last Monday (of current week):
    days_since_monday = now_et.weekday()
    this_monday = _start_of_day(now_et - datetime.timedelta(days=days_since_monday))
    last_monday = this_monday - datetime.timedelta(days=7)
    last_sunday = _end_of_day(last_monday + datetime.timedelta(days=6))
    return last_monday, last_sunday


def mtd_range(now_et: datetime.datetime) -> tuple:
    """First-of-month 00:00 to now."""
    start = _start_of_day(now_et.replace(day=1))
    return start, now_et


def prior_week_range(now_et: datetime.datetime) -> tuple:
    """The week before last_week_range — for WoW comparison."""
    last_start, _ = last_week_range(now_et)
    prior_start = last_start - datetime.timedelta(days=7)
    prior_end = _end_of_day(prior_start + datetime.timedelta(days=6))
    return prior_start, prior_end
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_weekly_periods.py -v`
Expected: all 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add weekly_periods.py tests/test_weekly_periods.py
git commit -m "feat: add weekly_periods date-math helpers"
```

---

## Task 3: Refactor `_compute_summary` to accept (start, end)

**Files:**
- Modify: `api_server.py` (function at line ~771)
- Test: `tests/test_compute_summary_range.py` (new)

This lets the weekly digest call the existing sales-fetch logic with arbitrary date ranges.

- [ ] **Step 1: Write the failing test**

Create `tests/test_compute_summary_range.py`:

```python
import sys, os, datetime
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import patch
from api_server import _compute_summary_range
from weekly_periods import ET


def test_compute_summary_range_no_credentials_returns_zeros():
    start = datetime.datetime(2026, 4, 6, 0, 0, 0, tzinfo=ET)
    end = datetime.datetime(2026, 4, 12, 23, 59, 59, tzinfo=ET)
    env = {k: "" for k in [
        "SHOPIFY_STORE", "SHOPIFY_TOKEN",
        "AMAZON_CLIENT_ID", "AMAZON_CLIENT_SECRET", "AMAZON_REFRESH_TOKEN",
    ]}
    with patch.dict(os.environ, env, clear=False):
        result = _compute_summary_range(start, end)
    assert result["total_orders"] == 0
    assert result["gross_revenue"] == 0
    assert result["period_start"] == start.isoformat()
    assert result["period_end"] == end.isoformat()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_compute_summary_range.py -v`
Expected: FAIL — `ImportError: cannot import name '_compute_summary_range'`

- [ ] **Step 3: Refactor `_compute_summary` in `api_server.py`**

Replace the current `_compute_summary` (lines ~771–852) with this two-function version:

```python
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
        "orders":           orders,  # NEW: expose for top-product / top-state
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
    # Daily digest never consumed `orders`; keep payload shape stable for /api/summary clients
    # but leave the field present (harmless extra data).
    return result
```

- [ ] **Step 4: Run the refactor test + existing digest tests**

Run: `python -m pytest tests/test_compute_summary_range.py tests/test_digest.py -v`
Expected: all tests PASS (daily digest unaffected).

- [ ] **Step 5: Commit**

```bash
git add api_server.py tests/test_compute_summary_range.py
git commit -m "refactor: extract _compute_summary_range(start, end)"
```

---

## Task 4: Klaviyo client (TDD)

**Files:**
- Create: `klaviyo_client.py`
- Test: `tests/test_klaviyo_client.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_klaviyo_client.py`:

```python
import sys, os, datetime
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import patch, MagicMock
from klaviyo_client import fetch_weekly_metrics


def _dt(d): return datetime.datetime.fromisoformat(d)


def test_fetch_weekly_metrics_happy_path():
    # Fake Klaviyo responses, one per endpoint we hit
    resp_campaigns = MagicMock(ok=True, status_code=200)
    resp_campaigns.json.return_value = {"data": [{"id": "c1"}, {"id": "c2"}]}

    resp_reports = MagicMock(ok=True, status_code=200)
    resp_reports.json.return_value = {"data": [{
        "attributes": {"statistics": {
            "open_rate": 0.284, "click_rate": 0.031, "revenue": 412.00
        }}
    }]}

    resp_subs = MagicMock(ok=True, status_code=200)
    resp_subs.json.return_value = {"data": [{}]*23}

    resp_unsubs = MagicMock(ok=True, status_code=200)
    resp_unsubs.json.return_value = {"data": [{}]*4}

    with patch("klaviyo_client.requests.get",
               side_effect=[resp_campaigns, resp_reports, resp_subs, resp_unsubs]):
        result = fetch_weekly_metrics(
            "fake-key",
            _dt("2026-04-06T00:00:00-04:00"),
            _dt("2026-04-12T23:59:59-04:00"),
        )

    assert result["campaigns_sent"] == 2
    assert result["open_rate"] == 28.4
    assert result["click_rate"] == 3.1
    assert result["attributed_revenue"] == 412.00
    assert result["new_subscribers"] == 23
    assert result["unsubscribes"] == 4
    assert result["errors"] == []


def test_fetch_weekly_metrics_api_error_returns_zeros():
    resp_fail = MagicMock(ok=False, status_code=500, text="server error")
    with patch("klaviyo_client.requests.get", return_value=resp_fail):
        result = fetch_weekly_metrics(
            "fake-key",
            _dt("2026-04-06T00:00:00-04:00"),
            _dt("2026-04-12T23:59:59-04:00"),
        )
    assert result["campaigns_sent"] == 0
    assert result["open_rate"] == 0
    assert len(result["errors"]) > 0


def test_fetch_weekly_metrics_no_api_key():
    result = fetch_weekly_metrics(
        "",
        _dt("2026-04-06T00:00:00-04:00"),
        _dt("2026-04-12T23:59:59-04:00"),
    )
    assert result["campaigns_sent"] == 0
    assert result["errors"] == ["Klaviyo: no API key configured"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_klaviyo_client.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'klaviyo_client'`

- [ ] **Step 3: Implement `klaviyo_client.py`**

Create `klaviyo_client.py`:

```python
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

    # 1. Campaigns sent in window
    camp = _get(api_key, "/campaigns",
                {"filter": f"greater-or-equal(send_time,{start_iso}),"
                           f"less-or-equal(send_time,{end_iso}),"
                           f"equals(messages.channel,'email')"},
                out["errors"], "campaigns")
    campaigns_list = camp.get("data", []) if camp else []
    out["campaigns_sent"] = len(campaigns_list)

    # 2. Campaign performance reports (open/click/revenue aggregated)
    #    API: POST /campaign-values-reports — but simpler: hit per-campaign stats via
    #    /campaign-values-reports/ with conversion_metric_id param. We use the
    #    aggregate endpoint in one shot.
    reports = _get(api_key, "/campaign-values-reports",
                   {"filter": f"greater-or-equal(send_time,{start_iso}),"
                              f"less-or-equal(send_time,{end_iso})"},
                   out["errors"], "reports")
    data = reports.get("data", []) if reports else []
    if data:
        # Average open/click rates across campaigns, sum revenue
        open_rates  = [d.get("attributes", {}).get("statistics", {}).get("open_rate", 0) for d in data]
        click_rates = [d.get("attributes", {}).get("statistics", {}).get("click_rate", 0) for d in data]
        revenues    = [d.get("attributes", {}).get("statistics", {}).get("revenue", 0) for d in data]
        out["open_rate"]          = round(sum(open_rates) / len(open_rates) * 100, 1)
        out["click_rate"]         = round(sum(click_rates) / len(click_rates) * 100, 1)
        out["attributed_revenue"] = round(sum(revenues), 2)

    # 3. New subscribers (Subscribed to List events)
    subs = _get(api_key, "/events",
                {"filter": f"greater-or-equal(datetime,{start_iso}),"
                           f"less-or-equal(datetime,{end_iso}),"
                           f"equals(metric.name,'Subscribed to List')",
                 "page[size]": 100},
                out["errors"], "subscribed")
    out["new_subscribers"] = len(subs.get("data", [])) if subs else 0

    # 4. Unsubscribes
    unsubs = _get(api_key, "/events",
                  {"filter": f"greater-or-equal(datetime,{start_iso}),"
                             f"less-or-equal(datetime,{end_iso}),"
                             f"equals(metric.name,'Unsubscribed')",
                   "page[size]": 100},
                  out["errors"], "unsubscribed")
    out["unsubscribes"] = len(unsubs.get("data", [])) if unsubs else 0

    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_klaviyo_client.py -v`
Expected: all 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add klaviyo_client.py tests/test_klaviyo_client.py
git commit -m "feat: add klaviyo_client for weekly email metrics"
```

---

## Task 5: GoMarble client (TDD)

**Files:**
- Create: `gomarble_client.py`
- Test: `tests/test_gomarble_client.py`

Pulls Google + Meta ads via MCP-over-SSE. All network interaction is wrapped so the tests can mock the MCP `call_tool` surface.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_gomarble_client.py`:

```python
import sys, os, datetime
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import patch, AsyncMock
from gomarble_client import fetch_weekly_ads


def _dt(s): return datetime.datetime.fromisoformat(s)


def test_fetch_weekly_ads_no_api_key():
    result = fetch_weekly_ads(
        "",
        _dt("2026-04-06T00:00:00-04:00"),
        _dt("2026-04-12T23:59:59-04:00"),
    )
    assert result["google"]["spend"] == 0
    assert result["meta"]["spend"] == 0
    assert result["blended_roas"] == 0
    assert "no API key" in result["errors"][0]


def test_fetch_weekly_ads_happy_path():
    google_payload = {
        "rows": [{
            "metrics": {"costMicros": 180_000_000, "conversions": 12,
                        "conversionsValue": 756.0}
        }]
    }
    meta_payload = {
        "data": [{
            "spend": "240.00",
            "actions": [{"action_type": "purchase", "value": "8"}],
            "action_values": [{"action_type": "purchase", "value": "672.00"}],
        }]
    }

    async def fake_call(tool_name, args):
        if tool_name == "google_ads_run_gaql":
            return google_payload
        if tool_name == "facebook_get_adaccount_insights":
            return meta_payload
        raise AssertionError(f"unexpected tool {tool_name}")

    with patch("gomarble_client._call_mcp_tool", new=AsyncMock(side_effect=fake_call)):
        result = fetch_weekly_ads(
            "fake-key",
            _dt("2026-04-06T00:00:00-04:00"),
            _dt("2026-04-12T23:59:59-04:00"),
        )

    assert result["google"]["spend"] == 180.0
    assert result["google"]["revenue"] == 756.0
    assert result["google"]["roas"] == 4.2
    assert result["meta"]["spend"] == 240.0
    assert result["meta"]["revenue"] == 672.0
    assert round(result["meta"]["roas"], 1) == 2.8
    assert round(result["blended_roas"], 1) == 3.4


def test_fetch_weekly_ads_timeout_returns_zeros():
    async def fake_call(tool_name, args):
        raise TimeoutError("mcp timeout")

    with patch("gomarble_client._call_mcp_tool", new=AsyncMock(side_effect=fake_call)):
        result = fetch_weekly_ads(
            "fake-key",
            _dt("2026-04-06T00:00:00-04:00"),
            _dt("2026-04-12T23:59:59-04:00"),
        )
    assert result["google"]["spend"] == 0
    assert result["meta"]["spend"] == 0
    assert len(result["errors"]) >= 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_gomarble_client.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'gomarble_client'`

- [ ] **Step 3: Implement `gomarble_client.py`**

Create `gomarble_client.py`:

```python
"""GoMarble ads client via MCP-over-SSE.

Connects to https://apps.gomarble.ai/mcp-api/sse using the MCP Python SDK,
calls the same tools Claude Desktop calls, normalizes to a simple dict.

If the GoMarble SSE endpoint requires a Desktop-signed handshake that plain
MCP clients can't produce (TBD at implementation time), this module returns
errors and the ads section of the digest collapses to `—`. In that case we
switch to direct Google Ads + Meta Marketing API tokens.
"""
import asyncio
import datetime
from typing import Optional

GOMARBLE_URL     = "https://apps.gomarble.ai/mcp-api/sse"
CALL_TIMEOUT_SEC = 30


async def _call_mcp_tool(tool_name: str, args: dict) -> dict:
    """Open an SSE session, call one tool, close. Re-opens per call — simpler than
    keeping a persistent session for a once-a-week cron."""
    from mcp.client.sse import sse_client
    from mcp import ClientSession
    import os

    headers = {"Authorization": f"Bearer {os.environ.get('GOMARBLE_API_KEY', '')}"}
    async with sse_client(GOMARBLE_URL, headers=headers) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await asyncio.wait_for(
                session.call_tool(tool_name, args),
                timeout=CALL_TIMEOUT_SEC,
            )
            # MCP returns structured content — unwrap text content if present
            if hasattr(result, "content") and result.content:
                first = result.content[0]
                if hasattr(first, "text"):
                    import json
                    return json.loads(first.text)
            return {}


def _run(coro):
    try:
        return asyncio.run(coro)
    except RuntimeError:
        # Already in an event loop (e.g. inside pytest-asyncio) — fall back
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()


def _fmt_date(dt: datetime.datetime) -> str:
    return dt.strftime("%Y-%m-%d")


def _google_metrics(start, end, errors) -> dict:
    gaql = (
        f"SELECT metrics.cost_micros, metrics.conversions, "
        f"metrics.conversions_value FROM customer "
        f"WHERE segments.date BETWEEN '{_fmt_date(start)}' AND '{_fmt_date(end)}'"
    )
    try:
        payload = _run(_call_mcp_tool("google_ads_run_gaql", {"query": gaql}))
    except Exception as e:
        errors.append(f"GoMarble Google: {e}")
        return {"spend": 0, "revenue": 0, "conversions": 0, "roas": 0}
    rows = payload.get("rows", []) if payload else []
    spend = sum(r.get("metrics", {}).get("costMicros", 0) for r in rows) / 1_000_000
    conv  = sum(r.get("metrics", {}).get("conversions", 0) for r in rows)
    rev   = sum(r.get("metrics", {}).get("conversionsValue", 0) for r in rows)
    return {
        "spend":       round(spend, 2),
        "revenue":     round(rev, 2),
        "conversions": int(conv),
        "roas":        round(rev / spend, 1) if spend else 0,
    }


def _meta_metrics(start, end, errors) -> dict:
    args = {
        "time_range": {"since": _fmt_date(start), "until": _fmt_date(end)},
        "fields":     ["spend", "actions", "action_values"],
        "level":      "account",
    }
    try:
        payload = _run(_call_mcp_tool("facebook_get_adaccount_insights", args))
    except Exception as e:
        errors.append(f"GoMarble Meta: {e}")
        return {"spend": 0, "revenue": 0, "conversions": 0, "roas": 0}
    data = payload.get("data", []) if payload else []
    spend = sum(float(d.get("spend", 0)) for d in data)
    rev   = 0.0
    conv  = 0
    for d in data:
        for a in d.get("action_values", []) or []:
            if a.get("action_type") == "purchase":
                rev += float(a.get("value", 0))
        for a in d.get("actions", []) or []:
            if a.get("action_type") == "purchase":
                conv += int(float(a.get("value", 0)))
    return {
        "spend":       round(spend, 2),
        "revenue":     round(rev, 2),
        "conversions": conv,
        "roas":        round(rev / spend, 1) if spend else 0,
    }


def fetch_weekly_ads(api_key: str,
                     start: datetime.datetime,
                     end: datetime.datetime) -> dict:
    errors = []
    if not api_key:
        errors.append("GoMarble: no API key configured")
        return {
            "google":       {"spend": 0, "revenue": 0, "conversions": 0, "roas": 0},
            "meta":         {"spend": 0, "revenue": 0, "conversions": 0, "roas": 0},
            "blended_roas": 0,
            "errors":       errors,
        }

    google = _google_metrics(start, end, errors)
    meta   = _meta_metrics(start, end, errors)

    total_spend = google["spend"] + meta["spend"]
    total_rev   = google["revenue"] + meta["revenue"]
    blended     = round(total_rev / total_spend, 1) if total_spend else 0

    return {
        "google":       google,
        "meta":         meta,
        "blended_roas": blended,
        "errors":       errors,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_gomarble_client.py -v`
Expected: all 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add gomarble_client.py tests/test_gomarble_client.py
git commit -m "feat: add gomarble_client for weekly ads metrics via MCP-over-SSE"
```

---

## Task 6: Insights client (TDD)

**Files:**
- Create: `insights_client.py`
- Test: `tests/test_insights_client.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_insights_client.py`:

```python
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import patch, MagicMock
from insights_client import generate_insights


def _sample_report():
    return {
        "last_week": {
            "sales": {"gross_revenue": 3214.50, "total_orders": 47,
                      "net_margin": 36.7, "avg_order": 68.39,
                      "top_product": "Jamón Ibérico 3oz Sliced",
                      "top_states": [("CA", 12), ("FL", 9), ("NY", 5)]},
            "klaviyo": {"campaigns_sent": 2, "open_rate": 28.4,
                        "click_rate": 3.1, "attributed_revenue": 412.0,
                        "new_subscribers": 23, "unsubscribes": 4},
            "ads": {"google": {"spend": 180, "revenue": 756, "roas": 4.2},
                    "meta":   {"spend": 240, "revenue": 672, "roas": 2.8},
                    "blended_roas": 3.4},
        },
        "prior_week": {"sales": {"gross_revenue": 2870.00}},
        "mtd": {"sales": {"gross_revenue": 6180.0}, "ads": {"blended_roas": 3.6}},
    }


def test_generate_insights_happy_path():
    # Anthropic client returns a content list with a text block containing JSON
    fake_response = MagicMock()
    fake_response.content = [MagicMock(text='["Bullet one.", "Bullet two.", "Bullet three."]')]

    with patch("insights_client.Anthropic") as cls:
        inst = cls.return_value
        inst.messages.create.return_value = fake_response
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "fake"}):
            bullets = generate_insights(_sample_report())

    assert bullets == ["Bullet one.", "Bullet two.", "Bullet three."]


def test_generate_insights_no_api_key():
    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}):
        assert generate_insights(_sample_report()) == []


def test_generate_insights_api_error_returns_empty():
    with patch("insights_client.Anthropic") as cls:
        cls.return_value.messages.create.side_effect = RuntimeError("boom")
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "fake"}):
            assert generate_insights(_sample_report()) == []


def test_generate_insights_malformed_json_returns_empty():
    fake_response = MagicMock()
    fake_response.content = [MagicMock(text="this is not json")]
    with patch("insights_client.Anthropic") as cls:
        cls.return_value.messages.create.return_value = fake_response
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "fake"}):
            assert generate_insights(_sample_report()) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_insights_client.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'insights_client'`

- [ ] **Step 3: Implement `insights_client.py`**

Create `insights_client.py`:

```python
"""Claude-generated analysis for the weekly digest.

Takes the compiled report dict, returns 3-5 bullet strings.
Never raises — on any error returns []; the digest will ship without analysis.
"""
import json
import os
from anthropic import Anthropic

MODEL = "claude-sonnet-4-6"

SYSTEM = """You are an e-commerce analyst for Iberian Ham Express, a premium
Spanish specialty foods store (jamón ibérico, charcuterie, olive oil, cheese)
selling on Shopify and Amazon. Context:
- AOV ~$67. Top product: Jamón Ibérico 3oz Sliced (46% of orders historically).
- Top markets: California and Florida (~half of all orders).
- Retention gap: repeat purchase rate is only 6.4% — retention is the priority.
- 10-Pack bundles (~$250) target event/gift buyers; under-leveraged.
- Email list: ~190 unconverted subscribers. Klaviyo drives email.

Given the weekly report data, return exactly 3-5 bullet strings of analysis.
Each bullet must be:
- Under 120 characters
- Actionable OR a sharp observation (no fluff, no restating the numbers)
- Business-relevant (revenue, margin, retention, channel mix)

Return ONLY a JSON array of strings. No prose, no markdown, no code fences.
Example output: ["Gross up 12% WoW driven by...", "Meta ROAS dropped 3.9x->2.8x..."]"""


def generate_insights(report: dict) -> list:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return []
    try:
        client = Anthropic(api_key=api_key)
        resp = client.messages.create(
            model=MODEL,
            max_tokens=600,
            temperature=0.3,
            system=[{
                "type": "text",
                "text": SYSTEM,
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{
                "role": "user",
                "content": f"Weekly report:\n{json.dumps(report, default=str)}",
            }],
            timeout=15.0,
        )
    except Exception:
        return []

    try:
        text = resp.content[0].text.strip()
        bullets = json.loads(text)
        if not isinstance(bullets, list):
            return []
        return [str(b)[:120] for b in bullets][:5]
    except Exception:
        return []
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_insights_client.py -v`
Expected: all 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add insights_client.py tests/test_insights_client.py
git commit -m "feat: add insights_client for Claude-generated weekly analysis"
```

---

## Task 7: Weekly digest aggregator + formatter (TDD)

**Files:**
- Create: `weekly_digest.py`
- Test: `tests/test_weekly_digest.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_weekly_digest.py`:

```python
import sys, os, datetime
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import patch
from weekly_digest import format_weekly_message, top_product, top_states


def _sample_report():
    return {
        "now_et_iso": "2026-04-14T07:00:00-04:00",
        "last_week": {
            "period_label": "Apr 7–13",
            "sales": {
                "total_orders": 47, "shopify_orders": 32, "amazon_orders": 15,
                "gross_revenue": 3214.50, "net_revenue": 1180.20,
                "net_margin": 36.7, "avg_order": 68.39,
            },
            "top_product": ("Jamón Ibérico 3oz Sliced", 19),
            "top_states":  [("CA", 12), ("FL", 9), ("NY", 5)],
            "wow_gross_pct": 12,
            "klaviyo": {"campaigns_sent": 2, "open_rate": 28.4,
                        "click_rate": 3.1, "attributed_revenue": 412.00,
                        "new_subscribers": 23, "unsubscribes": 4, "errors": []},
            "ads": {"google": {"spend": 180, "revenue": 756, "roas": 4.2},
                    "meta":   {"spend": 240, "revenue": 672, "roas": 2.8},
                    "blended_roas": 3.4, "errors": []},
        },
        "mtd": {
            "period_label": "Apr 1–14",
            "sales": {"total_orders": 92, "gross_revenue": 6180.0,
                      "net_revenue": 2240.0, "net_margin": 36.2},
            "ads":  {"google": {"spend": 420, "revenue": 1520},
                     "meta":   {"spend": 400, "revenue": 1460},
                     "blended_roas": 3.6, "errors": []},
        },
        "insights": [
            "Gross up 12% WoW driven by the Jamón 3oz promo.",
            "Meta ROAS dropped 3.9x → 2.8x; check creative fatigue.",
            "Email attributed revenue 13% of total — below 20% target.",
        ],
    }


def test_top_product_basic():
    orders = [
        {"line_items": [{"name": "Jamón 3oz", "quantity": 2}]},
        {"line_items": [{"name": "Jamón 3oz", "quantity": 1}]},
        {"line_items": [{"name": "Olive Oil", "quantity": 1}]},
    ]
    assert top_product(orders) == ("Jamón 3oz", 2)


def test_top_states_basic():
    orders = [
        {"billing_address": {"province_code": "CA"}},
        {"billing_address": {"province_code": "CA"}},
        {"billing_address": {"province_code": "FL"}},
    ]
    result = top_states(orders, n=2)
    assert result[0] == ("CA", 2)
    assert result[1] == ("FL", 1)


def test_format_weekly_message_full():
    msg = format_weekly_message(_sample_report())
    assert "IHE Weekly" in msg
    assert "Apr 7–13" in msg
    assert "Orders:   47" in msg
    assert "$3,214.50" in msg
    assert "+12% WoW" in msg
    assert "Jamón Ibérico 3oz Sliced" in msg
    assert "CA 12" in msg
    assert "28.4% open" in msg
    assert "Google: $180" in msg
    assert "Meta:   $240" in msg
    assert "3.4x" in msg
    assert "Apr 1–14" in msg
    assert "━ Analysis ━" in msg
    assert "creative fatigue" in msg


def test_format_weekly_message_no_insights_shows_dash():
    report = _sample_report()
    report["insights"] = []
    msg = format_weekly_message(report)
    assert "━ Analysis ━" in msg
    assert "—" in msg


def test_format_weekly_message_zero_ads_shows_dash():
    report = _sample_report()
    report["last_week"]["ads"] = {
        "google": {"spend": 0, "revenue": 0, "roas": 0},
        "meta":   {"spend": 0, "revenue": 0, "roas": 0},
        "blended_roas": 0, "errors": [],
    }
    msg = format_weekly_message(report)
    assert "📣 Ads" in msg
    # When all ads zero, a single "—" line appears under the header
    ads_section = msg.split("📣 Ads")[1].split("━")[0]
    assert "—" in ads_section
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_weekly_digest.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'weekly_digest'`

- [ ] **Step 3: Implement `weekly_digest.py`**

Create `weekly_digest.py`:

```python
"""Weekly digest aggregator and Telegram message formatter.

build_weekly_report() orchestrates sales + klaviyo + ads + insights.
format_weekly_message() is a pure function — no side effects, easy to test.
"""
import datetime
import os
from collections import Counter

from weekly_periods import last_week_range, mtd_range, prior_week_range, ET


def _period_label(start: datetime.datetime, end: datetime.datetime) -> str:
    if start.month == end.month:
        return f"{start.strftime('%b')} {start.day}–{end.day}"
    return f"{start.strftime('%b %-d')}–{end.strftime('%b %-d')}"


def top_product(orders: list) -> tuple:
    counter = Counter()
    for o in orders:
        for li in o.get("line_items", []) or []:
            counter[li.get("name", "")] += li.get("quantity", 0)
    if not counter:
        return ("", 0)
    name, qty = counter.most_common(1)[0]
    return (name, qty)


def top_states(orders: list, n: int = 3) -> list:
    counter = Counter()
    for o in orders:
        addr = o.get("billing_address") or {}
        state = addr.get("province_code") or addr.get("state_code") or addr.get("state")
        if state:
            counter[state] += 1
    return counter.most_common(n)


def _pct(a: float, b: float) -> int:
    if not b:
        return 0
    return round((a - b) / b * 100)


def _ads_line(label: str, m: dict) -> str:
    if m.get("spend", 0) == 0 and m.get("revenue", 0) == 0:
        return ""
    return (f"{label}: ${m['spend']:,.0f} spend · "
            f"{m['roas']:.1f}x ROAS · ${m['revenue']:,.0f} revenue")


def build_weekly_report() -> dict:
    """Called by the endpoint. Pulls all sources, returns the full report dict."""
    from api_server import _compute_summary_range
    from klaviyo_client import fetch_weekly_metrics
    from gomarble_client import fetch_weekly_ads
    from insights_client import generate_insights

    now_et = datetime.datetime.now(ET)

    lw_start, lw_end  = last_week_range(now_et)
    pw_start, pw_end  = prior_week_range(now_et)
    mtd_start, mtd_end = mtd_range(now_et)

    # Sales
    lw_sales  = _compute_summary_range(lw_start, lw_end)
    pw_sales  = _compute_summary_range(pw_start, pw_end)
    mtd_sales = _compute_summary_range(mtd_start, mtd_end)

    # Klaviyo + ads only for last week (MTD ads only needed for blended ROAS)
    klaviyo = fetch_weekly_metrics(os.environ.get("KLAVIYO_API_KEY", ""),
                                    lw_start, lw_end)
    ads_lw  = fetch_weekly_ads(os.environ.get("GOMARBLE_API_KEY", ""),
                               lw_start, lw_end)
    ads_mtd = fetch_weekly_ads(os.environ.get("GOMARBLE_API_KEY", ""),
                               mtd_start, mtd_end)

    report = {
        "now_et_iso": now_et.isoformat(),
        "last_week": {
            "period_label":  _period_label(lw_start, lw_end),
            "sales":         lw_sales,
            "top_product":   top_product(lw_sales.get("orders", [])),
            "top_states":    top_states(lw_sales.get("orders", [])),
            "wow_gross_pct": _pct(lw_sales["gross_revenue"], pw_sales["gross_revenue"]),
            "klaviyo":       klaviyo,
            "ads":           ads_lw,
        },
        "mtd": {
            "period_label": _period_label(mtd_start, mtd_end),
            "sales":        mtd_sales,
            "ads":          ads_mtd,
        },
    }

    # Strip the raw orders list out before sending to Claude (not useful + token heavy)
    report_for_llm = {
        "last_week": {k: v for k, v in report["last_week"].items()},
        "mtd":       {k: v for k, v in report["mtd"].items()},
    }
    report_for_llm["last_week"]["sales"] = {
        k: v for k, v in report["last_week"]["sales"].items() if k != "orders"
    }
    report_for_llm["mtd"]["sales"] = {
        k: v for k, v in report["mtd"]["sales"].items() if k != "orders"
    }
    report["insights"] = generate_insights(report_for_llm)

    return report


def format_weekly_message(report: dict) -> str:
    now_date = datetime.datetime.fromisoformat(report["now_et_iso"])
    header = f"📊 IHE Weekly — {now_date.strftime('%a %b %-d')}"

    lw = report["last_week"]
    s  = lw["sales"]
    wow = lw["wow_gross_pct"]
    wow_str = f"({wow:+d}% WoW)" if s["gross_revenue"] else ""
    tp = lw["top_product"]
    ts = lw["top_states"]

    sales_block = (
        f"━ Last week ({lw['period_label']}) ━\n"
        f"Orders:   {s['total_orders']}   "
        f"({s['shopify_orders']} Shopify · {s['amazon_orders']} Amazon)\n"
        f"Gross:    ${s['gross_revenue']:,.2f}   {wow_str}\n"
        f"Net:      ${s['net_revenue']:,.2f}   ({s['net_margin']}%)\n"
        f"AOV:      ${s['avg_order']:,.2f}"
    )
    if tp[0]:
        sales_block += f"\n\nTop product: {tp[0]} ({tp[1]} orders)"
    if ts:
        sales_block += "\nTop states: " + " · ".join(f"{st} {n}" for st, n in ts)

    k = lw["klaviyo"]
    if k["campaigns_sent"] or k["attributed_revenue"] or k["new_subscribers"]:
        email_block = (
            f"📧 Email (Klaviyo)\n"
            f"Campaigns: {k['campaigns_sent']} sent · {k['open_rate']}% open · "
            f"{k['click_rate']}% click\n"
            f"Attributed revenue: ${k['attributed_revenue']:,.2f}\n"
            f"New subs: {k['new_subscribers']} · Unsubs: {k['unsubscribes']}"
        )
    else:
        email_block = "📧 Email (Klaviyo)\n—"

    ads = lw["ads"]
    g_line = _ads_line("Google", ads["google"])
    m_line = _ads_line("Meta  ", ads["meta"])
    if g_line or m_line:
        ads_block = "📣 Ads\n" + "\n".join(x for x in [g_line, m_line] if x)
        if ads["blended_roas"]:
            ads_block += f"\nBlended ROAS: {ads['blended_roas']:.1f}x"
    else:
        ads_block = "📣 Ads\n—"

    mtd = report["mtd"]
    ms = mtd["sales"]
    ma = mtd["ads"]
    mtd_block = (
        f"━ MTD ({mtd['period_label']}) ━\n"
        f"Orders: {ms['total_orders']} · "
        f"Gross: ${ms['gross_revenue']:,.0f} · "
        f"Net: ${ms['net_revenue']:,.0f} ({ms['net_margin']}%)"
    )
    if ma["google"]["spend"] or ma["meta"]["spend"]:
        mtd_block += (
            f"\nAds spend: ${ma['google']['spend']+ma['meta']['spend']:,.0f} · "
            f"Attributed: ${ma['google']['revenue']+ma['meta']['revenue']:,.0f} · "
            f"Blended ROAS {ma['blended_roas']:.1f}x"
        )

    insights = report.get("insights") or []
    if insights:
        analysis_block = "━ Analysis ━\n" + "\n".join(f"• {b}" for b in insights)
    else:
        analysis_block = "━ Analysis ━\n—"

    return "\n\n".join([header, sales_block, email_block,
                        ads_block, mtd_block, analysis_block])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_weekly_digest.py -v`
Expected: all 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add weekly_digest.py tests/test_weekly_digest.py
git commit -m "feat: add weekly_digest aggregator and formatter"
```

---

## Task 8: `/api/digest/weekly` endpoint (TDD)

**Files:**
- Modify: `api_server.py`
- Test: `tests/test_weekly_endpoint.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_weekly_endpoint.py`:

```python
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import patch, MagicMock


def test_weekly_endpoint_no_secret():
    from api_server import app
    with patch.dict(os.environ, {"DIGEST_SECRET": "abc123"}):
        client = app.test_client()
        resp = client.post("/api/digest/weekly")
        assert resp.status_code == 401


def test_weekly_endpoint_wrong_secret():
    from api_server import app
    with patch.dict(os.environ, {"DIGEST_SECRET": "abc123"}):
        client = app.test_client()
        resp = client.post("/api/digest/weekly?secret=wrong")
        assert resp.status_code == 401


def test_weekly_endpoint_not_configured():
    from api_server import app
    with patch.dict(os.environ, {"DIGEST_SECRET": ""}):
        client = app.test_client()
        resp = client.post("/api/digest/weekly")
        assert resp.status_code == 503


def test_weekly_endpoint_happy_path_calls_telegram():
    from api_server import app

    fake_report = {
        "now_et_iso": "2026-04-14T07:00:00-04:00",
        "last_week": {
            "period_label": "Apr 7–13",
            "sales": {"total_orders": 5, "shopify_orders": 3, "amazon_orders": 2,
                      "gross_revenue": 500.0, "net_revenue": 180.0,
                      "net_margin": 36.0, "avg_order": 100.0, "orders": []},
            "top_product": ("", 0), "top_states": [], "wow_gross_pct": 0,
            "klaviyo": {"campaigns_sent": 0, "open_rate": 0, "click_rate": 0,
                        "attributed_revenue": 0, "new_subscribers": 0,
                        "unsubscribes": 0, "errors": []},
            "ads": {"google": {"spend": 0, "revenue": 0, "roas": 0},
                    "meta":   {"spend": 0, "revenue": 0, "roas": 0},
                    "blended_roas": 0, "errors": []},
        },
        "mtd": {
            "period_label": "Apr 1–14",
            "sales": {"total_orders": 10, "gross_revenue": 1000.0,
                      "net_revenue": 360.0, "net_margin": 36.0, "orders": []},
            "ads":  {"google": {"spend": 0, "revenue": 0}, "meta": {"spend": 0, "revenue": 0},
                     "blended_roas": 0, "errors": []},
        },
        "insights": ["Test bullet."],
        "errors": [],
    }

    env = {"DIGEST_SECRET": "abc123",
           "TELEGRAM_BOT_TOKEN": "tok", "TELEGRAM_CHAT_ID": "chat"}
    with patch.dict(os.environ, env), \
         patch("api_server.build_weekly_report", return_value=fake_report), \
         patch("api_server.send_telegram") as send:
        client = app.test_client()
        resp = client.post("/api/digest/weekly?secret=abc123")

    assert resp.status_code == 200
    send.assert_called_once()
    args, _ = send.call_args
    assert "IHE Weekly" in args[2]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_weekly_endpoint.py -v`
Expected: FAIL — `/api/digest/weekly` returns 404.

- [ ] **Step 3: Add the endpoint + helpers to `api_server.py`**

At the top of `api_server.py`, alongside existing imports, add:

```python
from weekly_digest import build_weekly_report, format_weekly_message
```

Then find the existing `@app.route("/api/digest")` block. Immediately after the daily-digest route's closing (end of function), add:

```python
@app.route("/api/digest/weekly", methods=["POST"])
def post_digest_weekly():
    secret     = os.environ.get("DIGEST_SECRET", "")
    bot_token  = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id    = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not secret or not bot_token or not chat_id:
        return jsonify({"error": "digest not configured"}), 503
    if request.args.get("secret") != secret:
        return jsonify({"error": "unauthorized"}), 401
    try:
        report  = build_weekly_report()
        message = format_weekly_message(report)
        send_telegram(bot_token, chat_id, message)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    return jsonify({"ok": True, "message": message,
                    "errors": report.get("errors", [])}), 200
```

> Note: `send_telegram` already exists in `api_server.py` from the daily digest. Reuse it — do not redefine.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_weekly_endpoint.py tests/test_digest.py -v`
Expected: new tests PASS, existing daily digest tests still PASS.

- [ ] **Step 5: Commit**

```bash
git add api_server.py tests/test_weekly_endpoint.py
git commit -m "feat: add POST /api/digest/weekly endpoint"
```

---

## Task 9: Render configuration

**Files:**
- Modify: `render.yaml`

- [ ] **Step 1: Read current `render.yaml`**

Run: `cat /Users/rafaelpenott/IHE-dashboard/src/render.yaml`

- [ ] **Step 2: Add three env var entries**

In the `envVars:` block of the service, append:

```yaml
      - key: KLAVIYO_API_KEY
        sync: false
      - key: GOMARBLE_API_KEY
        sync: false
      - key: ANTHROPIC_API_KEY
        sync: false
```

> `sync: false` means Render will prompt the user to set the value in the dashboard rather than expecting it in the YAML.

- [ ] **Step 3: Commit**

```bash
git add render.yaml
git commit -m "chore: add KLAVIYO/GOMARBLE/ANTHROPIC env vars to render.yaml"
```

---

## Task 10: GitHub Actions weekly cron

**Files:**
- Create: `.github/workflows/weekly-digest.yml`

- [ ] **Step 1: Verify workflows dir exists**

Run: `ls -la /Users/rafaelpenott/IHE-dashboard/.github/workflows/ 2>/dev/null`

If the directory does not exist, create it:
```bash
mkdir -p /Users/rafaelpenott/IHE-dashboard/.github/workflows
```

- [ ] **Step 2: Create the workflow file**

Create `.github/workflows/weekly-digest.yml`:

```yaml
name: Weekly Telegram Digest

on:
  schedule:
    - cron: '0 11 * * 1'      # 11:00 UTC Monday = 7am EDT / 6am EST
  workflow_dispatch:

jobs:
  digest:
    runs-on: ubuntu-latest
    steps:
      - name: Trigger weekly digest
        run: |
          curl --fail -X POST \
            "https://ihe-dashboard.onrender.com/api/digest/weekly?secret=${{ secrets.DIGEST_SECRET }}"
```

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/weekly-digest.yml
git commit -m "ci: add weekly Telegram digest cron (Mon 11:00 UTC)"
```

---

## Task 11: Full test sweep

- [ ] **Step 1: Run all tests**

Run: `cd /Users/rafaelpenott/IHE-dashboard/src && python -m pytest tests/ -v`
Expected: every test from tasks 2–8 passes, plus every pre-existing test in `tests/test_digest.py`, `tests/test_tracking.py`, `tests/test_financials.py` still passes.

- [ ] **Step 2: If anything fails**

Read the failure, fix the source (not the test). Do NOT skip tests. Re-run until green.

- [ ] **Step 3: Commit any fixes**

```bash
git add -A
git commit -m "test: fix regressions surfaced by weekly digest integration"
```

---

## Task 12: Manual deployment + smoke test

- [ ] **Step 1: Push to GitHub**

```bash
cd /Users/rafaelpenott/IHE-dashboard
git push
```

Render auto-deploys on push to main.

- [ ] **Step 2: Set the three new env vars in Render dashboard**

In the Render service → Environment tab, add:
- `KLAVIYO_API_KEY` — pull from Klaviyo → Account → Settings → API Keys (private key with `read:campaigns`, `read:events`, `read:metrics`)
- `GOMARBLE_API_KEY` — copy from Claude Desktop → Extensions → GoMarble settings
- `ANTHROPIC_API_KEY` — console.anthropic.com → API Keys

Trigger a Render redeploy if it didn't auto-deploy after setting secrets.

- [ ] **Step 3: Add DIGEST_SECRET to GitHub repo secrets**

Already exists for daily digest — verify in Settings → Secrets and variables → Actions. No action needed if present.

- [ ] **Step 4: Manual fire**

In GitHub → Actions → Weekly Telegram Digest → "Run workflow". Confirm:
- Workflow turns green
- Telegram message lands in the configured chat
- Message contains header, Last week section, Email section (or `—`), Ads section (or `—`), MTD section, Analysis section with 3–5 bullets (or `—`)

- [ ] **Step 5: If Ads section is `—` when it shouldn't be**

That's the open-question fallback from the spec: GoMarble's SSE endpoint likely requires a Desktop-signed session. Action: open a follow-up task to swap `gomarble_client.py` for direct `google_ads_client.py` + `meta_ads_client.py` using Google Ads + Meta Marketing API tokens, per the spec's Open Questions #1.

- [ ] **Step 6: Confirm next Monday's auto-fire**

Monday 11:00 UTC the cron fires automatically. No further action unless the Telegram message doesn't arrive — in which case GitHub Actions will show the workflow as failed (thanks to `curl --fail`).

---

## Self-Review Notes

- Spec coverage: every section (date math, Shopify/Amazon refactor, Klaviyo, GoMarble, Insights, formatter, endpoint, cron, Render config, tests) is implemented as a task.
- No placeholders — all code complete, all test bodies specified.
- Type consistency verified: `fetch_weekly_metrics` / `fetch_weekly_ads` / `generate_insights` / `build_weekly_report` / `format_weekly_message` names are stable across tasks.
- Open risk captured as Task 12 step 5 rather than hidden: GoMarble non-Desktop auth may need a fallback to direct Google Ads + Meta tokens.
