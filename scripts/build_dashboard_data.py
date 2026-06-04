#!/usr/bin/env python3
"""
Build dashboard_data.json from pure JSON inputs — no Excel/master.xlsx needed.

Inputs:
  - config.json                            (product groups, active campaigns, ust factor)
  - data/shopify_orders_<YYYY-MM-DD>.json  (one file per pulled day; from Shopify API)
  - data/printlabs_user.json               (from MHTML upload via dashboard)
  - data/ad_spend_user.json                (from dashboard ad-spend form)

Output:
  - dashboard_data.json                    (same shape the dashboard JS expects)

Usage:
  python3 build_dashboard_data.py [--root /path/to/profit-tracker]
"""
import argparse
import glob
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

BERLIN = ZoneInfo("Europe/Berlin")


def load_json(path: Path, default=None):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"WARN: {path} ist kaputt: {e}", file=sys.stderr)
        return default


def classify_group(line_items, product_groups, default_group):
    """Return the product group name for an order by matching line item titles."""
    titles = " | ".join((li.get("title") or "") + " " + (li.get("name") or "") for li in line_items)
    for grp in product_groups:
        sub = grp.get("substring", "")
        if sub and sub.lower() in titles.lower():
            return grp.get("name", default_group)
    return default_group


def order_date_berlin(created_at: str) -> str:
    """Convert a Shopify ISO timestamp to a YYYY-MM-DD string in Europe/Berlin."""
    dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    return dt.astimezone(BERLIN).date().isoformat()


def to_float(x, default=0.0):
    if x is None:
        return default
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def build(root: Path) -> dict:
    cfg = load_json(root / "config.json", default={})
    if not cfg:
        print("FEHLER: config.json fehlt oder leer", file=sys.stderr)
        sys.exit(2)

    ust = to_float(cfg.get("ust_factor", 1.19), 1.19)
    product_groups = cfg.get("product_groups", [])
    default_group = cfg.get("default_group", "Sonstiges")
    # Map: campaign name → group (from config + ad_spend entries' own group field)
    campaign_to_group = {c["name"]: c.get("group", default_group) for c in cfg.get("active_campaigns", [])}

    data_dir = root / "data"

    # ----- Load print-labs costs by shopify_order_id -----
    pl = load_json(data_dir / "printlabs_user.json", default={"orders": {}})
    pl_orders = pl.get("orders") or {}
    # Normalize keys to strings (in case JSON loads them as numbers somewhere)
    pl_by_id = {str(k): v for k, v in pl_orders.items()}

    # ----- Load Shopify variant costs (for "Sonstiges" products not in Print-Labs) -----
    vc = load_json(data_dir / "variant_costs.json", default={"costs": {}})
    variant_costs = vc.get("costs") or {}  # variant_id (str) → {cost_net, ...}

    def shopify_cogs_net(line_items):
        """Sum of (cost_per_item * quantity) across all line_items. Returns (cogs_net, n_missing)."""
        total = 0.0
        missing = 0
        for li in line_items or []:
            vid = str(li.get("variant_id") or "")
            qty = int(li.get("quantity") or 0)
            info = variant_costs.get(vid) or {}
            cost = info.get("cost_net")
            if cost is None:
                missing += 1
                continue
            total += float(cost) * qty
        return total, missing

    # ----- Load ad spend -----
    ad = load_json(data_dir / "ad_spend_user.json", default={"entries": {}, "campaigns": []})
    ad_entries = ad.get("entries") or {}
    # Pick up any user-added campaigns too (so missing-in-config campaigns still resolve to a group)
    for c in (ad.get("campaigns") or []):
        if c.get("name") and c["name"] not in campaign_to_group:
            campaign_to_group[c["name"]] = c.get("group", default_group)

    # ----- Load all Shopify orders -----
    shop_files = sorted(glob.glob(str(data_dir / "shopify_orders_*.json")))
    all_orders = []  # flat list of (date_iso, shopify_order)
    for f in shop_files:
        d = load_json(Path(f), default={})
        for o in (d.get("orders") or []):
            try:
                date_iso = order_date_berlin(o["created_at"])
            except Exception:
                continue
            all_orders.append((date_iso, o))

    # Dedup by Shopify id (in case the same order appears in multiple pulls)
    seen = {}
    for d, o in all_orders:
        oid = str(o.get("id") or "")
        if not oid:
            continue
        # Keep the latest pulled one (later in iteration since shop_files is sorted)
        seen[oid] = (d, o)

    # ----- Per-order computation -----
    out_orders = []
    missing_costs = []
    # Aggregations keyed by date and (date, group)
    day_agg = {}     # date → {orders, revenue, cogs}
    group_agg = {}   # (date, group) → {orders, revenue, cogs}

    for oid, (date_iso, o) in sorted(seen.items()):
        revenue = to_float(o.get("total_price"))
        discount = to_float(o.get("total_discounts"))
        shipping = to_float(o.get("total_shipping"))
        line_items = o.get("line_items") or []
        items_qty = sum(int(li.get("quantity") or 0) for li in line_items)
        group = classify_group(line_items, product_groups, default_group)

        pl_match = pl_by_id.get(oid)
        if pl_match:
            cogs_net = to_float(pl_match.get("total_cost_net"))
            cogs_gross = round(cogs_net * ust, 2)
            printlabs_id = pl_match.get("printlabs_id")
            cogs_source = "printlabs"
        elif group == default_group:
            # Sonstiges: nimm die Kosten aus den Shopify-Produkt-Daten (variant unitCost)
            cogs_net, n_missing = shopify_cogs_net(line_items)
            cogs_gross = round(cogs_net * ust, 2)
            printlabs_id = None
            cogs_source = "shopify_variant"
            if n_missing > 0 or cogs_net == 0:
                # Es fehlen Variant-Kosten oder das Produkt hat 0 als Cost in Shopify
                missing_costs.append({
                    "date": date_iso,
                    "shopify_name": o.get("name", ""),
                    "shopify_id": oid,
                    "reason": "no_shopify_cost" if cogs_net == 0 else f"partial_shopify_cost_{n_missing}_missing",
                })
        else:
            # Print-Labs-Gruppe (Schieferherzen / Weingläser) aber kein Match — noch nicht produziert
            cogs_net = 0.0
            cogs_gross = 0.0
            printlabs_id = None
            cogs_source = "missing"
            missing_costs.append({
                "date": date_iso,
                "shopify_name": o.get("name", ""),
                "shopify_id": oid,
                "reason": "no_printlabs_match",
            })

        out_orders.append({
            "date": date_iso,
            "shopify_name": o.get("name", ""),
            "shopify_id": oid,
            "printlabs_id": printlabs_id,
            "group": group,
            "items": items_qty,
            "total": round(revenue, 2),
            "shipping": round(shipping, 2),
            "discount": round(discount, 2),
            "cogs_net": round(cogs_net, 2),
            "cogs_gross": round(cogs_gross, 2),
            "cogs_source": cogs_source,
        })

        a = day_agg.setdefault(date_iso, {"orders": 0, "revenue": 0.0, "cogs": 0.0})
        a["orders"] += 1
        a["revenue"] += revenue
        a["cogs"] += cogs_gross  # use gross since it's what reduces profit

        g = group_agg.setdefault((date_iso, group), {"orders": 0, "revenue": 0.0, "cogs": 0.0})
        g["orders"] += 1
        g["revenue"] += revenue
        g["cogs"] += cogs_gross

    # ----- Ad spend per (date, campaign) and per (date, group) -----
    spend_by_day = {}   # date → spend
    spend_by_group = {} # (date, group) → spend
    campaigns_rows = []

    for date_iso, entry in ad_entries.items():
        for c in (entry.get("campaigns") or []):
            name = c.get("name")
            spend = to_float(c.get("spend"))
            if not name or spend == 0:
                # still record zero spends if present? skip for noise reduction
                if spend == 0 and not name:
                    continue
            group = c.get("group") or campaign_to_group.get(name, default_group)
            campaigns_rows.append({
                "date": date_iso,
                "campaign": name,
                "group": group,
                "spend": round(spend, 2),
                "purchases": None,
                "roas": None,
            })
            spend_by_day[date_iso] = spend_by_day.get(date_iso, 0.0) + spend
            spend_by_group[(date_iso, group)] = spend_by_group.get((date_iso, group), 0.0) + spend

    # ----- Build the days array -----
    all_dates = set(day_agg.keys()) | set(spend_by_day.keys())
    days_rows = []
    for d in sorted(all_dates):
        a = day_agg.get(d, {"orders": 0, "revenue": 0.0, "cogs": 0.0})
        spend = spend_by_day.get(d, 0.0)
        revenue = a["revenue"]
        cogs = a["cogs"]
        profit = revenue - cogs - spend
        margin = profit / revenue if revenue else 0
        roas = revenue / spend if spend else 0
        be_roas = 1 / (1 - cogs / revenue) if revenue and cogs < revenue else 0
        days_rows.append({
            "date": d,
            "orders": a["orders"],
            "revenue": round(revenue, 2),
            "cogs": round(cogs, 2),
            "spend": round(spend, 2),
            "profit": round(profit, 2),
            "margin": round(margin, 4),
            "roas": round(roas, 4),
            "be_roas": round(be_roas, 4),
        })

    # ----- Build the groups array -----
    all_group_keys = set(group_agg.keys()) | set(spend_by_group.keys())
    groups_rows = []
    for (d, g) in sorted(all_group_keys):
        a = group_agg.get((d, g), {"orders": 0, "revenue": 0.0, "cogs": 0.0})
        spend = spend_by_group.get((d, g), 0.0)
        revenue = a["revenue"]
        cogs = a["cogs"]
        profit = revenue - cogs - spend
        margin = profit / revenue if revenue else 0
        roas = revenue / spend if spend else 0
        groups_rows.append({
            "date": d,
            "group": g,
            "orders": a["orders"],
            "revenue": round(revenue, 2),
            "cogs": round(cogs, 2),
            "spend": round(spend, 2),
            "profit": round(profit, 2),
            "margin": round(margin, 4),
            "roas": round(roas, 4),
        })

    # ----- Sort outputs -----
    out_orders.sort(key=lambda x: (x["date"], x["shopify_name"]))
    campaigns_rows.sort(key=lambda x: (x["date"], x["campaign"]))
    missing_costs.sort(key=lambda x: x["date"])

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "days": days_rows,
        "groups": groups_rows,
        "campaigns": campaigns_rows,
        "orders": out_orders,
        "missing_costs": missing_costs,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="Profit-Tracker root (containing config.json and data/)")
    parser.add_argument("--out", default=None, help="Output path (default: <root>/dashboard_data.json)")
    args = parser.parse_args()

    root = Path(args.root).expanduser().resolve()
    out_path = Path(args.out).expanduser().resolve() if args.out else (root / "dashboard_data.json")

    if not (root / "config.json").exists():
        print(f"FEHLER: config.json fehlt in {root}", file=sys.stderr)
        sys.exit(2)

    data = build(root)
    out_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {out_path} — {len(data['days'])} days, {len(data['orders'])} orders, "
          f"{len(data['campaigns'])} campaign rows, {len(data['missing_costs'])} missing-cost rows")


if __name__ == "__main__":
    main()
