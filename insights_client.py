"""Claude-generated analysis for the weekly digest.

Takes the compiled report dict. Returns {"bullets": [...], "errors": [...]}.
Never raises — errors land in the error list; the digest ships without analysis
if the API call fails.
"""
import json
import os
from anthropic import Anthropic

MODEL = "claude-sonnet-4-5"

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
- Actionable OR a sharp observation
- Business-relevant (revenue, margin, retention, channel mix)

If the data is sparse or mostly zero, still return 3 bullets — observations
about the data gap itself (e.g., "no Klaviyo campaigns sent last week — list
of 190 unconverted subscribers still untouched") are valid.

Return ONLY a JSON array of strings. No prose, no markdown, no code fences.
Example: ["Gross up 12% WoW driven by...", "Meta ROAS dropped 3.9x->2.8x..."]"""


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
            max_tokens=700,
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
        out["bullets"] = [str(b)[:120] for b in bullets][:5]
    except Exception as e:
        preview = ""
        try:
            preview = resp.content[0].text[:160]
        except Exception:
            pass
        out["errors"].append(f"parse error: {e} | preview: {preview}")

    return out
