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


def _google_metrics(start, end, errors) -> dict:
    gaql = (
        f"SELECT metrics.cost_micros, metrics.conversions, "
        f"metrics.conversions_value FROM customer "
        f"WHERE segments.date BETWEEN '{_fmt_date(start)}' AND '{_fmt_date(end)}'"
    )
    try:
        payload = _run(_call_mcp_tool("google_ads_run_gaql", {"query": gaql}))
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


def _meta_metrics(start, end, errors) -> dict:
    args = {
        "time_range": {"since": _fmt_date(start), "until": _fmt_date(end)},
        "fields":     ["spend", "actions", "action_values"],
        "level":      "account",
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
