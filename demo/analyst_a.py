# SPDX-License-Identifier: Apache-2.0
"""Dana's EMEA revenue script.

Straightforward: walk the order file top to bottom, price each line, convert
it, and add the EMEA ones up at the end.

This file and demo/analyst_b.py were written to be read side by side. They
share no code beyond the Whence primitives, they run in separate directories
against separate fact stores, and they never talk to each other. What they
share is the *definition* of the calculation -- the same four transforms, the
same business keys -- which is exactly the thing a spec is supposed to pin
down. Whether that shared definition was implemented the same way is the
question the ids answer.

    python demo/analyst_a.py --data data --store .whence-dana
"""

from __future__ import annotations

import argparse
from pathlib import Path

from whence.core import Store, active_store
from whence.pipeline import (CLOSE_DATE, cell_fact, fx_convert, line_revenue,
                             load, sum_by_region)


def main() -> str:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data")
    ap.add_argument("--store", default=".whence-dana")
    ap.add_argument("--pricebook", default="pricebook_v3.csv")
    args = ap.parse_args()

    data = Path(args.data)
    store = Store(args.store).init()

    with active_store(store):
        orders = load(data / "erp_orders.csv")
        prices = load(data / args.pricebook)
        rates = load(data / "fx_rates.csv")

        emea_lines = []

        for order in orders.rows:
            if order["region"] != "EMEA":
                continue

            price_row = prices.where(sku=order["sku"])
            rate_row = rates.where(rate_date=CLOSE_DATE,
                                   currency=order["currency"])

            qty = cell_fact(orders, order, "qty",
                            key="src:order:%s.qty" % order["order_id"],
                            label="quantity ordered",
                            order_id=order["order_id"], sku=order["sku"],
                            region="EMEA", order_date=order["order_date"])

            price = cell_fact(prices, price_row, "list_price",
                              key="src:price:%s.list_price" % order["sku"],
                              label="%s list price" % order["sku"],
                              sku=order["sku"],
                              effective_date=price_row["effective_date"],
                              modified_by=price_row["last_modified_by"],
                              approval_ref=price_row["approval_ref"])

            rate = cell_fact(rates, rate_row, "usd_per_unit",
                             key="src:fx:%s.%s" % (CLOSE_DATE, order["currency"]),
                             label="%s/USD rate at %s close"
                                   % (order["currency"], CLOSE_DATE),
                             currency=order["currency"], rate_date=CLOSE_DATE)

            native = line_revenue(qty, price, meta={
                "key": "line:%s" % order["order_id"],
                "label": "%s x %s" % (order["qty"], order["sku"]),
                "order_id": order["order_id"], "sku": order["sku"],
                "region": "EMEA", "currency": order["currency"],
                "order_date": order["order_date"],
                "price_at_sale": order["unit_price_at_sale"],
                "price_recognized": price_row["list_price"],
            })

            emea_lines.append(fx_convert(native, rate, meta={
                "key": "usd:%s" % order["order_id"],
                "label": "%s in USD" % order["order_id"],
                "order_id": order["order_id"], "sku": order["sku"],
                "region": "EMEA", "currency": order["currency"],
                "rate_date": CLOSE_DATE,
            }))

        subtotal = sum_by_region(emea_lines, meta={
            "key": "region:EMEA",
            "label": "EMEA subtotal",
            "region": "EMEA",
            "order_count": str(len(emea_lines)),
        })

    print(subtotal.id)
    return subtotal.id


if __name__ == "__main__":
    main()
