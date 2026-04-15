# IHE Dashboard — Telegram Daily Digest

**Date:** 2026-04-14
**Status:** Approved

## Goal

Send a brief Telegram message every morning with the previous day's sales summary (orders, gross revenue, net revenue, margin, average order value).

## Delivery

- **Time:** 13:00 UTC daily (~8am ET winter / 9am ET summer)
- **Trigger:** GitHub Actions cron calls `POST /api/digest?secret=<DIGEST_SECRET>`
- **Channel:** Telegram bot — credentials already provisioned in `render.yaml`

## Message Format

```
📦 IHE — Mon Apr 14

Orders: 12  (8 Shopify · 4 Amazon)
Gross:  $847.50
Net:    $312.40  (36.9%)
AOV:    $70.63
```

Date line uses the yesterday date (ET timezone), derived from `totals["period_start"]`.
If there were zero orders, send: `📦 IHE — Mon Apr 14\n\nNo orders yesterday.`

## Architecture

### Backend — `api_server.py`

**`build_digest_message(totals: dict) -> str`**

Pure function. Takes the summary dict (same shape as `/api/summary` response) and returns a formatted string. No side effects.

```python
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
```

**`send_telegram(token: str, chat_id: str, text: str) -> None`**

Sends a message via the Telegram Bot API. Raises `RuntimeError` on non-2xx response.

```python
def send_telegram(token: str, chat_id: str, text: str) -> None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    resp = requests.post(url, json={"chat_id": chat_id, "text": text}, timeout=10)
    if not resp.ok:
        raise RuntimeError(f"Telegram error {resp.status_code}: {resp.text}")
```

**`POST /api/digest`**

1. Check `request.args.get("secret")` against `DIGEST_SECRET` env var — return `{"error": "unauthorized"}` with status 401 if missing or wrong.
2. Call the same summary logic as `/api/summary` with `period="yesterday"` (inline, not via HTTP).
3. Call `build_digest_message(totals)`.
4. Call `send_telegram(token, chat_id, message)`.
5. Return `{"ok": True, "message": message}` with status 200.
6. On any error in steps 3–4, return `{"ok": False, "error": str(e)}` with status 500.

`DIGEST_SECRET` missing from env → treat as unconfigured, return 503 with `{"error": "digest not configured"}`.

### GitHub Actions — `.github/workflows/daily-digest.yml`

```yaml
name: Daily Telegram Digest

on:
  schedule:
    - cron: '0 13 * * *'   # 13:00 UTC = ~8am ET
  workflow_dispatch:        # allow manual trigger from GitHub UI

jobs:
  digest:
    runs-on: ubuntu-latest
    steps:
      - name: Trigger digest
        run: |
          curl --fail -X POST \
            "https://ihe-dashboard.onrender.com/api/digest?secret=${{ secrets.DIGEST_SECRET }}"
```

`--fail` causes curl to exit non-zero on HTTP errors, making the GitHub Actions run show as failed if the endpoint returns 4xx/5xx.

`workflow_dispatch` allows manually triggering from the GitHub Actions UI for testing without waiting for the cron.

### Configuration

| Where | Key | Value |
|---|---|---|
| `render.yaml` | `DIGEST_SECRET` | `sync: false` — set in Render dashboard |
| GitHub repo secrets | `DIGEST_SECRET` | Same value as above |
| `render.yaml` | `TELEGRAM_BOT_TOKEN` | Already exists |
| `render.yaml` | `TELEGRAM_CHAT_ID` | Already exists |

### Tests — `tests/test_digest.py`

**`test_build_digest_message_normal`**

```python
def test_build_digest_message_normal():
    totals = {
        "period_start": "2026-04-13T00:00:00-04:00",
        "total_orders": 12, "shopify_orders": 8, "amazon_orders": 4,
        "gross_revenue": 847.50, "net_revenue": 312.40,
        "net_margin": 36.9, "avg_order": 70.63,
    }
    msg = build_digest_message(totals)
    assert "Mon Apr 13" in msg
    assert "Orders: 12" in msg
    assert "8 Shopify · 4 Amazon" in msg
    assert "$847.50" in msg
    assert "36.9%" in msg
    assert "$70.63" in msg
```

**`test_build_digest_message_zero_orders`**

```python
def test_build_digest_message_zero_orders():
    totals = {
        "period_start": "2026-04-13T00:00:00-04:00",
        "total_orders": 0, "shopify_orders": 0, "amazon_orders": 0,
        "gross_revenue": 0, "net_revenue": 0, "net_margin": 0, "avg_order": 0,
    }
    msg = build_digest_message(totals)
    assert "No orders yesterday" in msg
```

**`test_digest_endpoint_no_secret`**

```python
def test_digest_endpoint_no_secret():
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from api_server import app
    with patch.dict(os.environ, {"DIGEST_SECRET": "abc123"}):
        client = app.test_client()
        resp = client.post("/api/digest")
        assert resp.status_code == 401
```

**`test_digest_endpoint_wrong_secret`**

```python
def test_digest_endpoint_wrong_secret():
    from api_server import app
    with patch.dict(os.environ, {"DIGEST_SECRET": "abc123"}):
        client = app.test_client()
        resp = client.post("/api/digest?secret=wrong")
        assert resp.status_code == 401
```

**`test_digest_endpoint_not_configured`**

```python
def test_digest_endpoint_not_configured():
    from api_server import app
    env = {k: "" for k in ["DIGEST_SECRET", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"]}
    with patch.dict(os.environ, env):
        client = app.test_client()
        resp = client.post("/api/digest")
        assert resp.status_code == 503
```

## Error Handling

| Scenario | Behaviour |
|---|---|
| `DIGEST_SECRET` not set | 503 — digest not configured |
| Wrong or missing secret | 401 — unauthorized |
| Shopify/Amazon API failure | Log error, still send digest with whatever data succeeded (same as `/api/summary`) |
| Telegram API failure | Return 500, GitHub Actions run shows as failed — visible alert |
| Zero orders | Send "No orders yesterday." message rather than empty or skipping |

## Out of Scope

- Weekly or monthly digest variants
- Per-product or per-SKU breakdown in the message
- Retry logic on Telegram failure (GitHub Actions re-run is sufficient)
- Dashboard UI button to trigger digest manually
