#!/usr/bin/env python3
"""Pull InventoryItem.unitCost for all variants seen in shopify_orders_*.json.

Caches into data/variant_costs.json — used by build_dashboard_data.py to compute
COGS for products that don't go through Print-Labs (e.g. "Sonstiges" group).

Reads SHOPIFY_SHOP and SHOPIFY_TOKEN from env.

Required app permissions: read_orders, read_products, read_inventory.
"""
import argparse
import json
import os
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path


def gql(shop, token, query, variables=None, retries=2):
    body = json.dumps({"query": query, "variables": variables or {}}).encode("utf-8")
    url = f"https://{shop}.myshopify.com/admin/api/2025-01/graphql.json"
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "X-Shopify-Access-Token": token,
            "Content-Type": "application/json",
        },
    )
    last_err = None
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read())
            if data.get("errors"):
                raise RuntimeError(f"GraphQL errors: {data['errors']}")
            return data
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")
            print(f"HTTP {e.code}: {body}", file=sys.stderr)
            last_err = e
            if e.code in (429, 502, 503, 504):
                continue  # transient — retry
            raise
        except Exception as e:
            last_err = e
            if attempt < retries:
                continue
            raise
    raise last_err


def fetch_costs_for(shop, token, variant_ids, chunk_size=100):
    """Fetch unit costs for the given Shopify variant IDs (numeric)."""
    out = {}
    ids = list({str(v) for v in variant_ids if v})
    ids.sort()
    print(f"Fetching costs for {len(ids)} variants…", file=sys.stderr)
    query = """
    query ($ids: [ID!]!) {
      nodes(ids: $ids) {
        ... on ProductVariant {
          legacyResourceId
          title
          sku
          product { title legacyResourceId }
          inventoryItem { unitCost { amount currencyCode } }
        }
      }
    }
    """
    for i in range(0, len(ids), chunk_size):
        chunk = ids[i : i + chunk_size]
        gids = [f"gid://shopify/ProductVariant/{vid}" for vid in chunk]
        data = gql(shop, token, query, {"ids": gids})
        nodes = (data.get("data") or {}).get("nodes") or []
        for node in nodes:
            if not node:
                continue  # null node = variant doesn't exist (deleted?)
            vid = node.get("legacyResourceId")
            if not vid:
                continue
            ii = node.get("inventoryItem") or {}
            uc = ii.get("unitCost") or {}
            amount = uc.get("amount")
            out[str(vid)] = {
                "cost_net": float(amount) if amount is not None else None,
                "currency": uc.get("currencyCode"),
                "variant_title": node.get("title"),
                "sku": node.get("sku"),
                "product_title": (node.get("product") or {}).get("title"),
                "product_id": (node.get("product") or {}).get("legacyResourceId"),
            }
    return out


def collect_variant_ids(data_dir: Path):
    """Walk all shopify_orders_*.json files and collect unique variant_ids."""
    variants = set()
    for f in sorted(data_dir.glob("shopify_orders_*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"WARN: {f} not readable: {e}", file=sys.stderr)
            continue
        for o in d.get("orders") or []:
            for li in o.get("line_items") or []:
                vid = li.get("variant_id")
                if vid:
                    variants.add(str(vid))
    return sorted(variants)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=".", help="Profit-Tracker root")
    ap.add_argument("--shop", default=os.environ.get("SHOPIFY_SHOP"))
    ap.add_argument("--token", default=os.environ.get("SHOPIFY_TOKEN"))
    args = ap.parse_args()

    if not args.shop or not args.token:
        sys.exit("Need SHOPIFY_SHOP and SHOPIFY_TOKEN (env or args)")

    root = Path(args.root).expanduser().resolve()
    data_dir = root / "data"
    out_path = data_dir / "variant_costs.json"

    variants = collect_variant_ids(data_dir)
    if not variants:
        print("Keine Variants in shopify_orders_*.json gefunden.", file=sys.stderr)
        # Write empty file so build_dashboard_data has something to read
        out_path.write_text(json.dumps({"generated_at": datetime.now(timezone.utc).isoformat(),
                                        "costs": {}}, indent=2, ensure_ascii=False))
        return

    # Merge with existing cache (preserve costs even for variants we don't fetch this run)
    existing = {}
    if out_path.exists():
        try:
            existing = json.loads(out_path.read_text()).get("costs") or {}
        except Exception:
            existing = {}

    fresh = fetch_costs_for(args.shop, args.token, variants)
    now = datetime.now(timezone.utc).isoformat()
    for vid, info in fresh.items():
        info["fetched_at"] = now

    merged = {**existing, **fresh}  # fresh wins
    out = {"generated_at": now, "costs": merged}
    out_path.write_text(json.dumps(out, indent=2, ensure_ascii=False))

    n_with = sum(1 for c in merged.values() if c.get("cost_net") is not None)
    n_without = len(merged) - n_with
    print(f"Wrote {out_path} — {len(merged)} variants total ({n_with} mit Kosten, {n_without} ohne)")


if __name__ == "__main__":
    main()
