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
        names_in_order = set()
        for li in o.get("line_items", []) or []:
            name = li.get("name", "")
            if name:
                names_in_order.add(name)
        for name in names_in_order:
            counter[name] += 1
    if not counter:
        return ("", 0)
    name, count = counter.most_common(1)[0]
    return (name, count)


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
    # Align "$" at position 8: "Google: $" = 9 chars, "Meta:   $" = 9 chars
    padded = f"{label}:{' ' * (7 - len(label))}"
    return (f"{padded}${m['spend']:,.0f} spend · "
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

    lw_sales  = _compute_summary_range(lw_start, lw_end)
    pw_sales  = _compute_summary_range(pw_start, pw_end)
    mtd_sales = _compute_summary_range(mtd_start, mtd_end)

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
    insights_result = generate_insights(report_for_llm)
    if isinstance(insights_result, dict):
        report["insights"] = insights_result.get("bullets", [])
        insights_errors = insights_result.get("errors", [])
    else:
        report["insights"] = insights_result or []
        insights_errors = []

    report["errors"] = (
        list(lw_sales.get("errors", []))
        + list(mtd_sales.get("errors", []))
        + [f"klaviyo: {e}" for e in klaviyo.get("errors", [])]
        + [f"ads_lw: {e}"  for e in ads_lw.get("errors", [])]
        + [f"ads_mtd: {e}" for e in ads_mtd.get("errors", [])]
        + [f"insights: {e}" for e in insights_errors]
    )

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
    m_line = _ads_line("Meta", ads["meta"])
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
