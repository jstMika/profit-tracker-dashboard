#!/usr/bin/env python3
"""Pull Shopify orders for a given date, strip customer PII, save as JSON.

Designed to run via launchd on the user's Mac.
Reads SHOPIFY_SHOP and SHOPIFY_TOKEN from env (sourced from
~/.config/profit-tracker/credentials) or via CLI args.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("Europe/Berlin")
except ImportError:
    TZ = timezone(timedelta(hours=2))  # CEST fallback


def pull_orders(shop, token, start_iso, end_iso):
    """Pull all orders in date range, handling Link-header pagination."""
    url = (
        f"https://{shop}.myshopify.com/admin/api/2025-01/orders.json"
        f"?status=any&created_at_min={start_iso}&created_at_max={end_iso}&limit=250"
    )
    all_orders = []
    page = 0
    while url:
        page += 1
        req = Request(
            url,
            headers={
                "X-Shopify-Access-Token": token,
                "Content-Type": "application/json",
            },
        )
        try:
            with urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read())
                all_orders.extend(data.get("orders", []))
                # Pagination via Link header
                link = resp.headers.get("Link", "")
                next_url = None
                for part in link.split(","):
                    if 'rel="next"' in part:
                        next_url = part.split(";")[0].strip().strip("<>")
                        break
                url = next_url
        except HTTPError as e:
            body = e.read().decode(errors="replace")
            print(f"HTTP {e.code} on page {page}: {body}", file=sys.stderr)
            sys.exit(1)
    return all_orders


def filter_order(o):
    """Keep only profit-relevant fields; drop customer PII."""
    return {
        "id": o["id"],
        "name": o.get("name"),
        "created_at": o.get("created_at"),
        "financial_status": o.get("financial_status"),
        "fulfillment_status": o.get("fulfillment_status"),
        "currency": o.get("currency"),
        "subtotal_price": o.get("subtotal_price"),
        "total_price": o.get("total_price"),
        "total_discounts": o.get("total_discounts"),
        "total_tax": o.get("total_tax"),
        "total_shipping": (o.get("total_shipping_price_set") or {})
        .get("shop_money", {})
        .get("amount"),
        "line_items": [
            {
                "id": li["id"],
                "product_id": li.get("product_id"),
                "variant_id": li.get("variant_id"),
                "title": li.get("title"),
                "name": li.get("name"),
                "quantity": li.get("quantity"),
                "price": li.get("price"),
            }
            for li in o.get("line_items", [])
        ],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="YYYY-MM-DD (default: yesterday in Berlin tz)")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--shop", default=os.environ.get("SHOPIFY_SHOP"))
    ap.add_argument("--token", default=os.environ.get("SHOPIFY_TOKEN"))
    args = ap.parse_args()

    if not args.shop or not args.token:
        sys.exit("Need SHOPIFY_SHOP and SHOPIFY_TOKEN (env or args)")

    if args.date:
        target = datetime.strptime(args.date, "%Y-%m-%d").date()
    else:
        now = datetime.now(TZ)
        target = (now - timedelta(days=1)).date()

    start_dt = datetime.combine(target, datetime.min.time(), tzinfo=TZ)
    end_dt = datetime.combine(
        target, datetime.max.time().replace(microsecond=0), tzinfo=TZ
    )

    print(
        f"Pulling Shopify orders for {target} "
        f"({start_dt.isoformat()} → {end_dt.isoformat()})",
        file=sys.stderr,
    )
    raw = pull_orders(args.shop, args.token, start_dt.isoformat(), end_dt.isoformat())
    orders = [filter_order(o) for o in raw]

    out_dir = Path(args.out_dir).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"shopify_orders_{target.isoformat()}.json"

    payload = {
        "pulled_at": datetime.now(timezone.utc).isoformat(),
        "date": target.isoformat(),
        "shop": args.shop,
        "order_count": len(orders),
        "orders": orders,
    }
    with open(out_path, "w") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)

    total_rev = sum(float(o.get("total_price") or 0) for o in orders)
    total_disc = sum(float(o.get("total_discounts") or 0) for o in orders)
    total_tax = sum(float(o.get("total_tax") or 0) for o in orders)
    total_ship = sum(float(o.get("total_shipping") or 0) for o in orders)

    print("=== Pull complete ===")
    print(f"Datum:           {target}")
    print(f"Bestellungen:    {len(orders)}")
    print(f"Umsatz brutto:   EUR {total_rev:.2f}")
    print(f"  Rabatte:       EUR {total_disc:.2f}")
    print(f"  Steuern:       EUR {total_tax:.2f}")
    print(f"  Versand:       EUR {total_ship:.2f}")
    print(f"Gespeichert:     {out_path}")


if __name__ == "__main__":
    main()
