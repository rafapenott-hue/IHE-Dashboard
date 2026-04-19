"""Claude-generated analysis for the weekly digest.

Takes the compiled report dict. Returns {"bullets": [...], "errors": [...]}.
Never raises — errors land in the error list; the digest ships without analysis
if the API call fails.
"""
import json
import os
from anthropic import Anthropic

MODEL = "claude-sonnet-4-5"

SYSTEM = """You are a senior e-commerce analyst writing the weekly executive
brief for Iberian Ham Express, a premium Spanish specialty foods store
(jamón ibérico, charcuterie, olive oil, cheese) selling on Shopify and Amazon.

Business context:
- AOV ~$67. Top product: Jamón Ibérico 3oz Sliced (46% of orders historically).
- Top markets: California and Florida (~half of all orders); FL trending up.
- Retention gap: repeat purchase rate is only 6.4% — retention is the priority.
- 10-Pack bundles (~$250) target event/gift buyers; under-leveraged.
- Email list: ~190+ unconverted subscribers. Klaviyo drives email.
- Google Ads + Meta Ads run via GoMarble when active.

Given the weekly report data, return exactly 5-7 bullet strings of analysis.
Each bullet MUST combine three elements in a single sentence:
1. Observation — the metric or pattern, with a concrete number
2. "Why it matters" — the business implication (margin, retention, CAC, risk)
3. Recommended action — a specific next step the operator can take this week

Each bullet should be under 220 characters. Prioritize sharpness over hedging.
Tie actions to the IHE playbook: retention flows, 10-Pack push, Klaviyo
re-engagement, CA/FL geo-targeting, Amazon-to-Shopify migration, upsells.

If a data point is zero (e.g., no campaigns sent), flag it as an
opportunity cost with a specific recovery action — not just "do something."

Bad: "Gross declined 7% WoW."
Good: "Gross dipped 7% WoW to $2.7K against FL-heavy week (18 of 37 orders) —
schedule a FL-only 10% off campaign in Klaviyo this week to defend MTD pacing."

Return ONLY a JSON array of strings. No prose, no markdown, no code fences."""


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
