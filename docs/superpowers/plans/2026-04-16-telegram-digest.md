# Telegram Daily Digest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `POST /api/digest` endpoint that builds a brief Telegram message from yesterday's sales summary and sends it via the Telegram Bot API, triggered daily by a GitHub Actions cron at 13:00 UTC.

**Architecture:** Extract the summary computation from the existing `/api/summary` route into a reusable `_compute_summary(period)` helper. Add `build_digest_message(totals)` (pure formatter) and `send_telegram(token, chat_id, text)` (HTTP sender) as standalone functions. A new `POST /api/digest` route checks a `DIGEST_SECRET` query param, calls those helpers, and returns `{"ok": true}`. A GitHub Actions workflow calls the endpoint daily.

**Tech Stack:** Python 3.9, Flask, `requests` (already imported), GitHub Actions, Telegram Bot API

---

## File Map

| File | Change |
|---|---|
| `api_server.py` | Add `_compute_summary()`, `build_digest_message()`, `send_telegram()`, `POST /api/digest`; refactor `get_summary()` to call `_compute_summary()` |
| `tests/test_digest.py` | New — 5 tests (2 message format, 3 endpoint auth) |
| `render.yaml` | Add `DIGEST_SECRET` env var (`sync: false`) |
| `.github/workflows/daily-digest.yml` | New — daily cron + manual trigger |

---

## Task 1: `build_digest_message` + message tests

**Files:**
- Create: `tests/test_digest.py`
- Modify: `api_server.py` (add function after `fee_cfg`, before any routes)

- [ ] **Step 1: Create `tests/test_digest.py` with 2 failing tests**

```python
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from api_server import build_digest_message


def test_build_digest_message_normal():
    totals = {
        "period_start":   "2026-04-13T00:00:00-04:00",
        "total_orders":   12,
        "shopify_orders": 8,
        "amazon_orders":  4,
        "gross_revenue":  847.50,
        "net_revenue":    312.40,
        "net_margin":     36.9,
        "avg_order":      70.63,
    }
    msg = build_digest_message(totals)
    assert "Mon Apr 13" in msg
    assert "Orders: 12" in msg
    assert "8 Shopify · 4 Amazon" in msg
    assert "$847.50" in msg
    assert "36.9%" in msg
    assert "$70.63" in msg


def test_build_digest_message_zero_orders():
    totals = {
        "period_start":   "2026-04-13T00:00:00-04:00",
        "total_orders":   0,
        "shopify_orders": 0,
        "amazon_orders":  0,
        "gross_revenue":  0,
        "net_revenue":    0,
        "net_margin":     0,
        "avg_order":      0,
    }
    msg = build_digest_message(totals)
    assert "No orders yesterday" in msg
```

- [ ] **Step 2: Run tests — expect 2 failures**

```bash
cd /Users/rafaelpenott/IHE-Dashboard/src
python3 -m pytest tests/test_digest.py -v
```

Expected: `ImportError: cannot import name 'build_digest_message'`

- [ ] **Step 3: Add `build_digest_message` to `api_server.py`**

Find the line `def fee_cfg() -> dict:` (around line 356). Add the new function **directly before it**:

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

- [ ] **Step 4: Run tests — expect 2 passing**

```bash
cd /Users/rafaelpenott/IHE-Dashboard/src
python3 -m pytest tests/test_digest.py -v
```

Expected: `2 passed`

- [ ] **Step 5: Run full test suite — expect no regressions**

```bash
cd /Users/rafaelpenott/IHE-Dashboard/src
python3 -m pytest tests/ -v
```

Expected: all previously passing tests still pass (26 total before this task).

- [ ] **Step 6: Commit**

```bash
cd /Users/rafaelpenott/IHE-Dashboard/src
git add api_server.py tests/test_digest.py
git commit -m "feat: add build_digest_message with tests"
```

---

## Task 2: Refactor `get_summary` + add `send_telegram` + `POST /api/digest` + endpoint auth tests

**Files:**
- Modify: `api_server.py` (lines 674–755 area — refactor `get_summary`, add `send_telegram`, add `POST /api/digest`)
- Modify: `tests/test_digest.py` (add 3 endpoint auth tests)

### Step 2a — Extract `_compute_summary` from `get_summary`

- [ ] **Step 1: Replace `get_summary` with a refactored version that delegates to `_compute_summary`**

Find the existing `get_summary` route (starts around line 674). Replace the **entire function** (from `@app.route("/api/summary")` through `return jsonify(totals)`) with:

```python
def _compute_summary(period: str = "yesterday") -> dict:
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

    start_iso = start.astimezone(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
    fees      = fee_cfg()
    errors    = []
    orders    = []

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
        "total_orders":   len(orders),
        "shopify_orders": sum(1 for o in orders if o["platform"] == "shopify"),
        "amazon_orders":  sum(1 for o in orders if o["platform"] == "amazon"),
        "gross_revenue":  round(sum(o["gross"]          for o in orders), 2),
        "amazon_fees":    round(sum(o["platform_fee"]   for o in orders
                                    if o["platform"] == "amazon"), 2),
        "shopify_fees":   round(sum(o["platform_fee"]   for o in orders
                                    if o["platform"] == "shopify"), 2),
        "stripe_fees":    round(sum(o["stripe_fee"]     for o in orders), 2),
        "cogs":           round(sum(o["cogs"]           for o in orders), 2),
        "shipping":       round(sum(o.get("shipping",0) for o in orders), 2),
        "total_fees":     round(sum(o["total_fees"]     for o in orders), 2),
        "net_revenue":    round(sum(o["net"]            for o in orders), 2),
        "total_units":    sum(o["units"] for o in orders),
        "period":         period,
        "period_start":   start.isoformat(),
        "period_end":     end.isoformat(),
        "errors":         errors,
    }
    g = totals["gross_revenue"]
    totals["net_margin"]  = round(totals["net_revenue"] / g * 100, 1) if g else 0
    totals["avg_order"]   = round(g / totals["total_orders"], 2) if totals["total_orders"] else 0
    totals["vendor_owed"] = totals["cogs"]
    totals["cogs_source"] = "per_sku" if _COGS["shopify"] else "flat_rate"
    return totals


@app.route("/api/summary")
def get_summary():
    period = request.args.get("period", "yesterday")
    return jsonify(_compute_summary(period))
```

- [ ] **Step 2: Run full test suite — verify no regressions**

```bash
cd /Users/rafaelpenott/IHE-Dashboard/src
python3 -m pytest tests/ -v
```

Expected: all tests still pass (28 total — the 2 from Task 1 + 26 prior).

### Step 2b — Add `send_telegram` and `POST /api/digest`

- [ ] **Step 3: Add `send_telegram` and `POST /api/digest` to `api_server.py`**

Find the comment line `# ─── MAIN` (near the end of the file, before `if __name__ == "__main__":`). Add the following **immediately before that comment block**:

```python
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
    if request.args.get("secret") != secret:
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

```

- [ ] **Step 4: Add 3 endpoint auth tests to `tests/test_digest.py`**

Append to the end of `tests/test_digest.py`:

```python
from unittest.mock import patch


def test_digest_endpoint_no_secret():
    from api_server import app
    with patch.dict(os.environ, {"DIGEST_SECRET": "abc123"}):
        client = app.test_client()
        resp = client.post("/api/digest")
        assert resp.status_code == 401


def test_digest_endpoint_wrong_secret():
    from api_server import app
    with patch.dict(os.environ, {"DIGEST_SECRET": "abc123"}):
        client = app.test_client()
        resp = client.post("/api/digest?secret=wrong")
        assert resp.status_code == 401


def test_digest_endpoint_not_configured():
    from api_server import app
    env = {"DIGEST_SECRET": "", "TELEGRAM_BOT_TOKEN": "", "TELEGRAM_CHAT_ID": ""}
    with patch.dict(os.environ, env):
        client = app.test_client()
        resp = client.post("/api/digest")
        assert resp.status_code == 503
```

- [ ] **Step 5: Run tests — expect 5 passing**

```bash
cd /Users/rafaelpenott/IHE-Dashboard/src
python3 -m pytest tests/test_digest.py -v
```

Expected: `5 passed`

- [ ] **Step 6: Run full test suite — no regressions**

```bash
cd /Users/rafaelpenott/IHE-Dashboard/src
python3 -m pytest tests/ -v
```

Expected: all tests pass (31 total).

- [ ] **Step 7: Commit**

```bash
cd /Users/rafaelpenott/IHE-Dashboard/src
git add api_server.py tests/test_digest.py
git commit -m "feat: add /api/digest endpoint with Telegram send and auth"
```

---

## Task 3: Config + GitHub Actions workflow + push

**Files:**
- Modify: `render.yaml`
- Create: `.github/workflows/daily-digest.yml`

- [ ] **Step 1: Add `DIGEST_SECRET` to `render.yaml`**

Find the block:
```yaml
      - key: TELEGRAM_BOT_TOKEN
        sync: false
      - key: TELEGRAM_CHAT_ID
        sync: false
```

Add directly after it:
```yaml
      - key: DIGEST_SECRET
        sync: false       # set in Render dashboard — must match GitHub secret
```

- [ ] **Step 2: Create `.github/workflows/daily-digest.yml`**

```yaml
name: Daily Telegram Digest

on:
  schedule:
    - cron: '0 13 * * *'   # 13:00 UTC = ~8am ET (9am EDT)
  workflow_dispatch:        # allow manual trigger from GitHub Actions UI

jobs:
  digest:
    runs-on: ubuntu-latest
    steps:
      - name: Send digest
        run: |
          curl --fail --silent --show-error -X POST \
            "https://ihe-dashboard.onrender.com/api/digest?secret=${{ secrets.DIGEST_SECRET }}"
```

- [ ] **Step 3: Commit**

```bash
cd /Users/rafaelpenott/IHE-Dashboard/src
git add render.yaml .github/workflows/daily-digest.yml
git commit -m "chore: add DIGEST_SECRET env var and GitHub Actions daily digest cron"
```

- [ ] **Step 4: Push to GitHub**

```bash
cd /Users/rafaelpenott/IHE-Dashboard/src
git remote set-url origin https://<PAT>@github.com/rafapenott-hue/IHE-Dashboard.git
git push origin main
git remote set-url origin https://github.com/rafapenott-hue/IHE-Dashboard.git
```

---

## Task 4: Set secrets and verify end-to-end

- [ ] **Step 1: Set `DIGEST_SECRET` in Render dashboard**

Go to https://dashboard.render.com → iberian-ham-dashboard → Environment.
Add `DIGEST_SECRET` with any strong value (e.g. a random 32-char string). Render will redeploy automatically.

- [ ] **Step 2: Set `DIGEST_SECRET` in GitHub repo secrets**

Go to https://github.com/rafapenott-hue/IHE-Dashboard → Settings → Secrets and variables → Actions → New repository secret.
Name: `DIGEST_SECRET`. Value: the **same** string set in Render.

- [ ] **Step 3: Verify the endpoint directly (after Render redeploys)**

```bash
curl -X POST "https://ihe-dashboard.onrender.com/api/digest?secret=YOUR_SECRET_HERE"
```

Expected response:
```json
{"ok": true, "message": "📦 IHE — ..."}
```

If Telegram is fully configured, you'll also receive the message in your Telegram chat.

- [ ] **Step 4: Trigger GitHub Actions manually to verify the cron works**

Go to https://github.com/rafapenott-hue/IHE-Dashboard → Actions → Daily Telegram Digest → Run workflow → Run workflow.

Expected: workflow run completes green. Telegram message arrives.
