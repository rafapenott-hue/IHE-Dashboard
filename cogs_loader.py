"""
Iberian Ham Express — COGS Loader Utility
==========================================
Run this whenever you update your Amazon or Shopify COGS files.
It rebuilds cogs_data.json which api_server.py reads at startup.

USAGE:
    python cogs_loader.py \
        --amazon  "COGS Amazon .xlsx" \
        --shopify "products_export.csv" \
        --output  cogs_data.json

    # Or run with defaults (looks for files in current directory):
    python cogs_loader.py

After running, restart api_server.py (or redeploy to Render) so it
picks up the new COGS table.
"""

import argparse
import json
import sys
from pathlib import Path
import datetime

try:
    import pandas as pd
except ImportError:
    print("❌ pandas is required: pip install pandas openpyxl --break-system-packages")
    sys.exit(1)


# ─────────────────────────────────────────────────────────────
# DEFAULTS
# ─────────────────────────────────────────────────────────────

DEFAULT_AMAZON_FILE  = "COGS Amazon .xlsx"
DEFAULT_SHOPIFY_FILE = "products_export.csv"
DEFAULT_OUTPUT       = "cogs_data.json"


# ─────────────────────────────────────────────────────────────
# LOADERS
# ─────────────────────────────────────────────────────────────

def load_amazon(path: str) -> dict:
    """
    Load Amazon COGS from your Business Report Excel file.
    Required columns: (Child) ASIN, Costo, Amazon (selling price)
    Optional columns: Title
    Returns: { ASIN: { cogs, price, title } }
    """
    df = pd.read_excel(path)
    df = df.dropna(subset=["(Child) ASIN", "Costo"])
    df = df[df["(Child) ASIN"].astype(str).str.startswith("B")]  # valid ASINs only

    result = {}
    skipped = []
    for _, row in df.iterrows():
        asin  = str(row["(Child) ASIN"]).strip()
        costo = float(row["Costo"])
        price = float(row["Amazon"]) if "Amazon" in row and pd.notna(row.get("Amazon")) else None
        title = str(row.get("Title", "")).strip()[:100]

        if costo <= 0:
            skipped.append(asin)
            continue

        result[asin] = {"cogs": round(costo, 2), "price": price, "title": title}

    print(f"  Amazon  : {len(result)} ASINs loaded" +
          (f", {len(skipped)} skipped (zero cost)" if skipped else ""))
    if skipped:
        print(f"            Skipped: {', '.join(skipped[:5])}" +
              (f"... +{len(skipped)-5} more" if len(skipped) > 5 else ""))
    return result


def load_shopify(path: str) -> dict:
    """
    Load Shopify COGS from your products export CSV.
    Required columns: Variant SKU, Cost per item
    Optional columns: Title, Variant Price
    Returns: { SKU: { cogs, price, title } }
    """
    df = pd.read_csv(path)
    df = df[df["Variant SKU"].notna() & df["Cost per item"].notna()]
    df = df[df["Cost per item"] > 0]

    result  = {}
    no_cost = []
    for _, row in df.iterrows():
        sku   = str(row["Variant SKU"]).strip()
        cost  = float(row["Cost per item"])
        price = float(row["Variant Price"]) if pd.notna(row.get("Variant Price")) else None
        title = str(row.get("Title", "")).strip()[:100] if pd.notna(row.get("Title")) else ""

        if not sku:
            continue
        result[sku] = {"cogs": round(cost, 2), "price": price, "title": title}

    # Also note SKUs that exist but have no cost
    all_skus = df_all = pd.read_csv(path) if True else df
    all_skus = pd.read_csv(path)
    missing  = all_skus[all_skus["Variant SKU"].notna() & all_skus["Cost per item"].isna()]["Variant SKU"].dropna()
    if len(missing):
        print(f"  Shopify : {len(result)} SKUs loaded")
        print(f"  ⚠️  {len(missing)} SKUs have no Cost per item in Shopify:")
        for s in missing[:10]:
            print(f"      • {s}")
        if len(missing) > 10:
            print(f"      ... +{len(missing)-10} more")
    else:
        print(f"  Shopify : {len(result)} SKUs loaded (all have COGS ✅)")
    return result


def compute_amazon_ratio(amazon: dict) -> float:
    """Weighted COGS-to-price ratio for Amazon (used as fallback when ASIN unknown)."""
    items = [(v["cogs"], v["price"]) for v in amazon.values() if v.get("price")]
    if not items:
        return 0.6255  # hardcoded default
    total_cogs  = sum(c for c, _ in items)
    total_price = sum(p for _, p in items)
    return round(total_cogs / total_price, 4) if total_price else 0.6255


# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Rebuild cogs_data.json from source files")
    parser.add_argument("--amazon",  default=DEFAULT_AMAZON_FILE,
                        help=f"Path to Amazon COGS Excel (default: {DEFAULT_AMAZON_FILE})")
    parser.add_argument("--shopify", default=DEFAULT_SHOPIFY_FILE,
                        help=f"Path to Shopify products CSV (default: {DEFAULT_SHOPIFY_FILE})")
    parser.add_argument("--output",  default=DEFAULT_OUTPUT,
                        help=f"Output JSON path (default: {DEFAULT_OUTPUT})")
    args = parser.parse_args()

    print(f"\n🔄 Rebuilding COGS table...")
    errors = []

    # Load Amazon
    amazon = {}
    if Path(args.amazon).exists():
        try:
            amazon = load_amazon(args.amazon)
        except Exception as e:
            print(f"  ❌ Amazon load failed: {e}")
            errors.append(str(e))
    else:
        print(f"  ⚠️  Amazon file not found: {args.amazon} — skipping")

    # Load Shopify
    shopify = {}
    if Path(args.shopify).exists():
        try:
            shopify = load_shopify(args.shopify)
        except Exception as e:
            print(f"  ❌ Shopify load failed: {e}")
            errors.append(str(e))
    else:
        print(f"  ⚠️  Shopify file not found: {args.shopify} — skipping")

    if not amazon and not shopify:
        print("\n❌ Both files failed or not found. Nothing saved.")
        sys.exit(1)

    amazon_ratio = compute_amazon_ratio(amazon)

    output = {
        "amazon":  amazon,
        "shopify": shopify,
        "meta": {
            "amazon_skus":       len(amazon),
            "shopify_skus":      len(shopify),
            "amazon_cogs_ratio": amazon_ratio,
            "generated":         str(datetime.date.today()),
            "amazon_source":     args.amazon,
            "shopify_source":    args.shopify,
            "errors":            errors,
        }
    }

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"\n✅ Saved to {args.output}")
    print(f"   Amazon COGS ratio (fallback): {amazon_ratio*100:.1f}%")
    print(f"\nNext step: restart api_server.py (or redeploy to Render) to apply changes.\n")


if __name__ == "__main__":
    main()
