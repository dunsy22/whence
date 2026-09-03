# SPDX-License-Identifier: Apache-2.0
"""Regenerate the synthetic fixture CSVs.

Everything this writes is invented. Halloway Instruments does not exist, the
SKUs are not anyone's catalogue, the people are not real people, and the prices
are not anyone's pricing. The FX rates are plausible-looking noise, not market
data. Nothing here should be mistaken for a real company's books.

The generator is committed alongside the CSVs so a reviewer can confirm that
claim rather than take it on faith:

    python data/_generate.py

It is seeded, so it rewrites byte-identical files every time.
"""

from __future__ import annotations

import csv
import random
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
SEED = 20260930

Q3_START = date(2026, 7, 1)
Q3_END = date(2026, 9, 30)          # the close date, and the FX date we translate at

REGIONS = ["AMER", "EMEA", "APAC", "LATAM"]
ORDERS_PER_REGION = {"AMER": 98, "EMEA": 63, "LATAM": 22, "APAC": 17}

# APAC's export missed the final day of the quarter and arrived the next
# morning. The first close ran without these; the restatement includes them.
LATE_APAC_ORDERS = 14

PEOPLE = ["m.jensen", "s.patel", "r.okonkwo", "t.almeida", "d.fairbanks", "l.vasquez"]

# The three rows nobody signed off on. They are also, in the restatement, the
# three rows that move -- which is the point of the whole demo.
UNAPPROVED = ["SKU-4471", "SKU-4472", "SKU-5013"]
RESTATED_PRICES = {"SKU-4471": "1340.00", "SKU-4472": "980.00", "SKU-5013": "2310.00"}


def build_catalogue(rng):
    """47 SKUs across three list currencies."""
    skus = []
    fixed = [4471, 4472, 5013]
    numbers = [n for n in list(range(4460, 4500)) + list(range(5000, 5020))
               if n not in fixed]
    rng.shuffle(numbers)
    for n in sorted(numbers[:44] + fixed):
        code = "SKU-%d" % n
        if code == "SKU-4471":
            price, cur = Decimal("1200.00"), "USD"
        elif code == "SKU-4472":
            price, cur = Decimal("890.00"), "USD"
        elif code == "SKU-5013":
            price, cur = Decimal("2100.00"), "USD"
        else:
            cur = rng.choice(["USD", "USD", "EUR", "GBP"])
            price = Decimal(rng.randrange(45, 240) * 10).quantize(Decimal("0.01"))
        skus.append({"sku": code, "list_price": price, "currency": cur})
    return skus


def write_pricebook(path, skus, rng, *, restated):
    rows = []
    for s in skus:
        unapproved = s["sku"] in UNAPPROVED
        if restated and s["sku"] in RESTATED_PRICES:
            price = RESTATED_PRICES[s["sku"]]
            effective, modifier = "2026-08-14", "m.jensen"
        else:
            price = format(s["list_price"], "f")
            effective = "2026-07-01"
            modifier = "m.jensen" if unapproved else rng.choice(PEOPLE)
        rows.append({
            "sku": s["sku"],
            "list_price": price,
            "currency": s["currency"],
            "effective_date": effective,
            "last_modified_by": modifier,
            # A blank approval reference is not an error. It is a row that
            # went into the numbers without anyone signing it.
            "approval_ref": "" if unapproved else "APR-%04d" % rng.randrange(1000, 9999),
        })
    _write(path, ["sku", "list_price", "currency", "effective_date",
                  "last_modified_by", "approval_ref"], rows)


def build_orders(rng, skus):
    by_code = {s["sku"]: s for s in skus}
    codes = [s["sku"] for s in skus]
    on_time, late = [], []
    counter = 10000

    for region in REGIONS:
        for _ in range(ORDERS_PER_REGION[region]):
            counter += 1
            sku = by_code[rng.choice(codes)]
            day = Q3_START + timedelta(days=rng.randrange(0, (Q3_END - Q3_START).days))
            # The price the ERP happened to capture at the till. Almost always
            # list price; a handful of lines were invoiced off-list. See the
            # note in pipeline.py about which one the close recognizes.
            drift = Decimal(rng.choice([-50, 25, -25]) if rng.randrange(100) < 6
                            else 0)
            on_time.append({
                "order_id": "ORD-%d" % counter,
                "order_date": day.isoformat(),
                "sku": sku["sku"],
                "qty": str(rng.randrange(2, 31)),
                "region": region,
                "currency": sku["currency"],
                "unit_price_at_sale": format(sku["list_price"] + drift, "f"),
            })

    for _ in range(LATE_APAC_ORDERS):
        counter += 1
        sku = by_code[rng.choice(codes)]
        late.append({
            "order_id": "ORD-%d" % counter,
            "order_date": Q3_END.isoformat(),
            "sku": sku["sku"],
            "qty": str(rng.randrange(2, 31)),
            "region": "APAC",
            "currency": sku["currency"],
            "unit_price_at_sale": format(sku["list_price"], "f"),
        })

    on_time.sort(key=lambda r: r["order_id"])
    return on_time, late


def write_orders(path, rows):
    _write(path, ["order_id", "order_date", "sku", "qty", "region",
                  "currency", "unit_price_at_sale"], rows)


def write_fx(path, rng):
    rows = []
    eur, gbp = Decimal("1.0840"), Decimal("1.2710")
    day = Q3_START
    while day <= Q3_END:
        eur += Decimal(rng.randrange(-30, 31)) / Decimal("10000")
        gbp += Decimal(rng.randrange(-35, 36)) / Decimal("10000")
        for cur, rate in (("USD", Decimal("1.0000")), ("EUR", eur), ("GBP", gbp)):
            rows.append({"rate_date": day.isoformat(), "currency": cur,
                         "usd_per_unit": format(rate, "f")})
        day += timedelta(days=1)
    _write(path, ["rate_date", "currency", "usd_per_unit"], rows)


def write_adjustments(path):
    """Journal entries whose upstream system is a person."""
    rows = [
        {"adj_id": "ADJ-001", "entry_date": "2026-09-30", "amount_usd": "80000.00",
         "reason": "Q3 rebate accrual per CFO email", "author": "s.patel",
         "source": "email from CFO, 2026-09-29"},
        {"adj_id": "ADJ-002", "entry_date": "2026-09-30", "amount_usd": "-12500.00",
         "reason": "Reverse duplicate LATAM shipment billed twice",
         "author": "r.okonkwo", "source": "Slack thread #q3-close"},
    ]
    _write(path, ["adj_id", "entry_date", "amount_usd", "reason", "author",
                  "source"], rows)


def _write(path, fields, rows):
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    print("wrote %-34s %4d rows" % (path.name, len(rows)))


def main():
    rng = random.Random(SEED)
    skus = build_catalogue(rng)
    on_time, late = build_orders(rng, skus)

    write_orders(HERE / "erp_orders.csv", on_time)
    write_orders(HERE / "erp_orders_restated.csv",
                 sorted(on_time + late, key=lambda r: r["order_id"]))
    write_pricebook(HERE / "pricebook_v3.csv", skus, random.Random(SEED + 1),
                    restated=False)
    write_pricebook(HERE / "pricebook_v4.csv", skus, random.Random(SEED + 1),
                    restated=True)
    write_fx(HERE / "fx_rates.csv", random.Random(SEED + 2))
    write_adjustments(HERE / "adjustments.csv")


if __name__ == "__main__":
    main()
