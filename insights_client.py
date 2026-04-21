"""Claude-generated analysis for the weekly digest.

Takes the compiled report dict. Returns {"bullets": [...], "errors": [...]}.
Never raises — errors land in the error list; the digest ships without analysis
if the API call fails.
"""
import json
import os
from anthropic import Anthropic

MODEL = "claude-sonnet-4-5"

SYSTEM = """You are an e-commerce advisor writing a weekly summary for the
owner of Iberian Ham Express, a premium Spanish specialty foods store
(jamón ibérico, charcuterie, olive oil, cheese) selling on Shopify and Amazon.

Business context:
- Average order value around $67. Top seller: Jamón Ibérico 3oz Sliced.
- Best markets: California and Florida (together roughly half of orders).
- Only 6.4% of customers come back — improving repeat purchases is the top goal.
- 10-Pack bundles (~$250) are great for events and gifts but rarely pushed.
- Email list has 190+ subscribers who never bought. Email runs on Klaviyo.
- Google Ads + Meta Ads run via GoMarble when active.

Write 5 to 7 bullets in plain, conversational English. Think short voice note
to a business-savvy friend — no analyst jargon.

STRICT rules:
- Never use abbreviations like WoW, MTD, YoY, CAC, LTV, AOV, ROAS, CPA, MoM,
  CPC, CTR, CR, CPM. Instead, spell them out in plain words
  ("compared to last week", "so far this month", "cost per new customer",
  "repeat-buyer revenue", "average spend per order", "return on ad spend",
  "month vs last month", etc.) — or just say the plain idea.
- Each bullet has three parts, flowing as one sentence or two short ones:
    1) What happened (with a real number)
    2) Why it matters in practical terms
    3) A specific, concrete action the owner can take this week
- Each bullet under 240 characters. Plain numbers ("$2,675" not "$2.7K").
- Prioritize usefulness over cleverness. Skip throat-clearing.

If a metric is zero (no campaigns sent, no ad spend), treat it as a missed
chance and give a concrete fix — not "consider doing something".

Example (good): "Sales dropped 7% from last week to $2,675, with Florida
carrying 18 of 37 orders. Send a Florida-only 10% off email in Klaviyo this
week to protect the rest of the month."

Example (bad — never write like this): "Gross dipped 7% WoW; Klaviyo campaigns
idle eroding CAC." — too terse, uses jargon.

Return ONLY a JSON array of 5–7 plain-English sentences. No prose, no markdown,
no code fences, no headings."""


def generate_insights(report: dict) -> dict:
    """Returns {'bullets': [...], 'errors': [...]}. Never raises."""
    out = {"bullets": [], "errors": []}
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        out["errors"].append("no ANTHROPIC_API_KEY")
        return out

    try:
        client = Anthropic(api_key=api_key, timeout=30.0)
        resp = client.messages.create(
            model=MODEL,
            max_tokens=1400,
            temperature=0.4,
            system=[{
                "type": "text",
                "text": SYSTEM,
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{
                "role": "user",
                "content": f"Weekly report:\n{json.dumps(report, default=str)}",
            }],
        )
    except Exception as e:
        out["errors"].append(f"{type(e).__name__}: {str(e)[:200]}")
        return out

    try:
        text = resp.content[0].text.strip()
        # Strip markdown fences if the model ignored the no-fence instruction
        if text.startswith("```"):
            text = text.split("```", 2)[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip("` \n")
        bullets = json.loads(text)
        if not isinstance(bullets, list):
            out["errors"].append("response was not a JSON array")
            return out
        out["bullets"] = [str(b)[:240] for b in bullets][:7]
    except Exception as e:
        preview = ""
        try:
            preview = resp.content[0].text[:160]
        except Exception:
            pass
        out["errors"].append(f"parse error: {e} | preview: {preview}")

    return out
