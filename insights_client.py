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
