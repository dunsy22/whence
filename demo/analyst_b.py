# SPDX-License-Identifier: Apache-2.0
"""Priya's EMEA revenue script.

Different shape entirely: bucket the orders by SKU first, work through the
SKUs in reverse alphabetical order, price each bucket once, and accumulate.
Priya also splits the work into helpers rather than doing it in one loop, and
she builds her source facts in a different sequence.

None of that changes the answer, and -- this is the point -- none of it changes
the *id* of the answer either. The set of lines being summed is the same set,
and Whence sorts a fact's parents before hashing precisely so that the order
someone happened to iterate in is not part of what a number means.

    python demo/analyst_b.py --data data --store .whence-priya
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path

from whence.core import Store, active_store
from whence.pipeline import (CLOSE_DATE, cell_fact, fx_convert, line_revenue,
                             load, sum_by_region)


def price_of(prices, sku):
    row = prices.where(sku=sku)
    return cell_fact(prices, row, "list_price",
                     key="src:price:%s.list_price" % sku,
                     label="%s list price" % sku,
                     sku=sku,
                     effective_date=row["effective_date"],
                     modified_by=row["last_modified_by"],
                     approval_ref=row["approval_ref"]), row


def rate_of(rates, currency):
    row = rates.where(rate_date=CLOSE_DATE, currency=currency)
    return cell_fact(rates, row, "usd_per_unit",
                     key="src:fx:%s.%s" % (CLOSE_DATE, currency),
                     label="%s/USD rate at %s close" % (currency, CLOSE_DATE),
                     currency=currency, rate_date=CLOSE_DATE)


def value_one_line(orders, order, price_fact, price_row, rate_fact):
    qty = cell_fact(orders, order, "qty",
                    key="src:order:%s.qty" % order["order_id"],
                    label="quantity ordered",
                    order_id=order["order_id"], sku=order["sku"],
                    region=order["region"], order_date=order["order_date"])

    native = line_revenue(qty, price_fact, meta={
        "key": "line:%s" % order["order_id"],
        "label": "%s x %s" % (order["qty"], order["sku"]),
        "order_id": order["order_id"], "sku": order["sku"],
        "region": order["region"], "currency": order["currency"],
        "order_date": order["order_date"],
        "price_at_sale": order["unit_price_at_sale"],
        "price_recognized": price_row["list_price"],
    })

    return fx_convert(native, rate_fact, meta={
        "key": "usd:%s" % order["order_id"],
        "label": "%s in USD" % order["order_id"],
        "order_id": order["order_id"], "sku": order["sku"],
        "region": order["region"], "currency": order["currency"],
        "rate_date": CLOSE_DATE,
    })


def main() -> str:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data")
    ap.add_argument("--store", default=".whence-priya")
    ap.add_argument("--pricebook", default="pricebook_v3.csv")
    args = ap.parse_args()

    data = Path(args.data)
    store = Store(args.store).init()

    with active_store(store):
        orders = load(data / "erp_orders.csv")
        prices = load(data / args.pricebook)
        rates = load(data / "fx_rates.csv")

        buckets = defaultdict(list)
        for order in orders.rows:
            if order["region"] == "EMEA":
                buckets[order["sku"]].append(order)

        rate_cache = {}
        running = []

        for sku in sorted(buckets, reverse=True):     # note: reverse order
            price_fact, price_row = price_of(prices, sku)
            for order in buckets[sku]:
                currency = order["currency"]
                if currency not in rate_cache:
                    rate_cache[currency] = rate_of(rates, currency)
                running.append(value_one_line(orders, order, price_fact,
                                              price_row, rate_cache[currency]))

        subtotal = sum_by_region(running, meta={
            "key": "region:EMEA",
            "label": "EMEA subtotal",
            "region": "EMEA",
            "order_count": str(len(running)),
        })

    print(subtotal.id)
    return subtotal.id


if __name__ == "__main__":
    main()
