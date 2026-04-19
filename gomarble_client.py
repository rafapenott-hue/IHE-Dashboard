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
import os

GOMARBLE_URL     = "https://apps.gomarble.ai/mcp-api/sse"
CALL_TIMEOUT_SEC = 30


async def _call_mcp_tool(tool_name: str, args: dict) -> dict:
    """Open an SSE session, call one tool, close. Re-opens per call — simpler than
    keeping a persistent session for a once-a-week cron.

    mcp is imported lazily so this module can be imported on Python 3.9 without
    the mcp package installed (tests patch out this function entirely)."""
    from mcp.client.sse import sse_client
    from mcp import ClientSession

    headers = {"Authorization": f"Bearer {os.environ.get('GOMARBLE_API_KEY', '')}"}
    async with sse_client(GOMARBLE_URL, headers=headers) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await asyncio.wait_for(
                session.call_tool(tool_name, args),
                timeout=CALL_TIMEOUT_SEC,
            )
            # If MCP flagged the call as an error, raise with the text so it surfaces
            if getattr(result, "isError", False):
                texts = []
                for c in (getattr(result, "content", []) or []):
                    if hasattr(c, "text"):
                        texts.append(c.text)
                raise RuntimeError(f"MCP tool error: {' | '.join(texts)[:260]}")
            # Otherwise parse the first text block as JSON, tolerating non-JSON
            if hasattr(result, "content") and result.content:
                first = result.content[0]
                if hasattr(first, "text") and first.text:
                    import json
                    text = first.text.strip()
                    try:
                        return json.loads(text)
                    except Exception:
                        raise RuntimeError(
                            f"non-JSON tool response: {text[:200]}"
                        )
            return {}


def _run(coro):
    try:
        return asyncio.run(coro)
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()


def _fmt_date(dt: datetime.datetime) -> str:
    return dt.strftime("%Y-%m-%d")


def _unwrap_error(e: BaseException, depth: int = 0) -> str:
    """Recursively dig through ExceptionGroup / __cause__ chains to find
    the real leaf exception. Returns a flat string with type and message."""
    if depth > 6:
        return f"{type(e).__name__}: {str(e)[:160]}"
    inner = getattr(e, "exceptions", None)
    if inner:
        # Recurse into the first sub-exception (usually the only one)
        return _unwrap_error(inner[0], depth + 1)
    cause = getattr(e, "__cause__", None)
    if cause is not None and cause is not e:
        return f"{type(e).__name__} <- {_unwrap_error(cause, depth + 1)}"
    return f"{type(e).__name__}: {str(e)[:200]}"


def _first_google_customer_id(errors) -> str:
    try:
        resp = _run(_call_mcp_tool("google_ads_list_accounts", {}))
    except Exception as e:
        errors.append(f"GoMarble Google list_accounts: {_unwrap_error(e)}")
        return ""
    # Response shape varies; try common shapes
    for k in ("accounts", "customers", "data", "rows"):
        arr = resp.get(k) if isinstance(resp, dict) else None
        if arr and isinstance(arr, list):
            first = arr[0]
            if isinstance(first, dict):
                return str(first.get("id") or first.get("customer_id")
                           or first.get("customerId") or first.get("resourceName", "").split("/")[-1])
            return str(first)
    return ""


def _google_metrics(start, end, errors) -> dict:
    customer_id = _first_google_customer_id(errors)
    if not customer_id:
        errors.append("GoMarble Google: no customer_id found via list_accounts")
        return {"spend": 0, "revenue": 0, "conversions": 0, "roas": 0}
    gaql = (
        f"SELECT metrics.cost_micros, metrics.conversions, "
        f"metrics.conversions_value FROM customer "
        f"WHERE segments.date BETWEEN '{_fmt_date(start)}' AND '{_fmt_date(end)}'"
    )
    try:
        payload = _run(_call_mcp_tool(
            "google_ads_run_gaql",
            {"customer_id": customer_id, "query": gaql},
        ))
    except Exception as e:
        errors.append(f"GoMarble Google: {_unwrap_error(e)}")
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


def _first_meta_account_id(errors) -> str:
    try:
        resp = _run(_call_mcp_tool("facebook_list_ad_accounts", {}))
    except Exception as e:
        errors.append(f"GoMarble Meta list_accounts: {_unwrap_error(e)}")
        return ""
    if not isinstance(resp, dict):
        errors.append(f"Meta list_accounts: unexpected shape: {str(resp)[:200]}")
        return ""
    for k in ("accounts", "data", "ad_accounts", "rows", "result"):
        arr = resp.get(k)
        if arr and isinstance(arr, list):
            first = arr[0]
            if isinstance(first, dict):
                for id_key in ("id", "account_id", "ad_account_id", "accountId", "adAccountId"):
                    val = first.get(id_key)
                    if val:
                        return str(val)
                errors.append(f"Meta list_accounts: first account has no id field. Keys: {list(first.keys())[:10]}")
                return ""
            return str(first)
    errors.append(f"Meta list_accounts: no list found. Top-level keys: {list(resp.keys())[:10]}")
    return ""


def _meta_metrics(start, end, errors) -> dict:
    account_id = _first_meta_account_id(errors)
    if not account_id:
        errors.append("GoMarble Meta: no ad_account_id found via list_ad_accounts")
        return {"spend": 0, "revenue": 0, "conversions": 0, "roas": 0}
    args = {
        "ad_account_id": account_id,
        "time_range":    {"since": _fmt_date(start), "until": _fmt_date(end)},
        "fields":        ["spend", "actions", "action_values"],
        "level":         "account",
    }
    try:
        payload = _run(_call_mcp_tool("facebook_get_adaccount_insights", args))
    except Exception as e:
        errors.append(f"GoMarble Meta: {_unwrap_error(e)}")
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
