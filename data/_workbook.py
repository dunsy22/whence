# SPDX-License-Identifier: Apache-2.0
"""Build the synthetic analyst workbooks from the fixture CSVs.

    python data/_workbook.py

writes two files:

    q3_close.xlsx     the close as an analyst would build it in Excel
    q3_close_v2.xlsx  the same workbook after the pricebook was restated

Like _generate.py, this is committed so a reviewer can see exactly what is in
the binary files rather than take it on faith. Everything is derived from the
CSVs in this directory; nothing new is invented except the layout and one
analyst's mistake.

The workbook is written with XlsxWriter (BSD) because it can store a formula
together with its calculated result. Excel saves both; a workbook that only
had formulas would not look like anything a person ever sent anyone. The
results are computed here in Decimal, with the same rounding Excel's ROUND
uses, so the cached values are exactly what Excel would display.

The analyst's mistake: in one row of the line-revenue column the formula has
been overwritten with a typed number -- the quantity times the price the
customer was actually invoiced at, rather than the list price the close
recognizes. It is a plausible edit, it is off by a small amount, and nothing
on the face of the workbook gives it away.
"""

from __future__ import annotations

import csv
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import xlsxwriter

HERE = Path(__file__).resolve().parent
CLOSE_DATE = date(2026, 9, 30)
REGIONS = ["AMER", "EMEA", "APAC", "LATAM"]
AUTHOR = "d.fairbanks"

# A fixed timestamp, so the generator writes byte-identical files every run.
STAMP = datetime(2026, 10, 1, 9, 0, 0)

# SKUs whose price changes between pricebook_v3 and _v4. The typed-over row is
# chosen away from these so the restatement and the mistake stay separable.
REPRICED = {"SKU-4471", "SKU-4472", "SKU-5013"}


def _rows(name):
    with (HERE / name).open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _d(text):
    return Decimal(text)


def _cents(x: Decimal) -> Decimal:
    return x.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _date(text):
    return datetime.strptime(text, "%Y-%m-%d")


def build(path: Path, pricebook_csv: str) -> None:
    orders = _rows("erp_orders.csv")
    pricebook = _rows(pricebook_csv)
    fx = _rows("fx_rates.csv")
    adjustments = _rows("adjustments.csv")

    price = {r["sku"]: _d(r["list_price"]) for r in pricebook}
    rate = {r["currency"]: _d(r["usd_per_unit"]) for r in fx
            if r["rate_date"] == CLOSE_DATE.isoformat()}

    # The row the analyst typed over: the first off-list order whose SKU is
    # not repriced in v4. Same row in both workbooks.
    typed_row = next(
        i for i, r in enumerate(orders)
        if r["unit_price_at_sale"] != pricebook_by_sku(pricebook)[r["sku"]]
        and r["sku"] not in REPRICED)

    wb = xlsxwriter.Workbook(str(path))
    wb.set_properties({"title": "Q3 close", "author": AUTHOR,
                       "company": "Halloway Instruments", "created": STAMP})
    fmt_head = wb.add_format({"bold": True})
    fmt_date = wb.add_format({"num_format": "yyyy-mm-dd"})
    fmt_int = wb.add_format({"num_format": "0"})
    fmt_money = wb.add_format({"num_format": "#,##0.00"})
    fmt_rate = wb.add_format({"num_format": "0.0000"})

    summary = wb.add_worksheet("Summary")
    revenue = wb.add_worksheet("Revenue")

    # -- input sheets: pasted in as values, exactly as exported --------------
    def table(name, header, rows, writers):
        ws = wb.add_worksheet(name)
        ws.write_row(0, 0, header, fmt_head)
        for r, row in enumerate(rows, start=1):
            for c, col in enumerate(header):
                writers.get(col, _text)(ws, r, c, row[col])
        return ws

    def _text(ws, r, c, v):
        ws.write_string(r, c, v) if v != "" else ws.write_blank(r, c, None)

    def _dt(ws, r, c, v):
        ws.write_datetime(r, c, _date(v), fmt_date)

    def num(fmt):
        return lambda ws, r, c, v: ws.write_number(r, c, float(v), fmt)

    table("Orders",
          ["order_id", "order_date", "sku", "qty", "region", "currency",
           "unit_price_at_sale"], orders,
          {"order_date": _dt, "qty": num(fmt_int),
           "unit_price_at_sale": num(fmt_money)})
    table("Pricebook",
          ["sku", "list_price", "currency", "effective_date",
           "last_modified_by", "approval_ref"], pricebook,
          {"list_price": num(fmt_money), "effective_date": _dt})
    table("FX", ["rate_date", "currency", "usd_per_unit"], fx,
          {"rate_date": _dt, "usd_per_unit": num(fmt_rate)})
    table("Adjustments",
          ["adj_id", "entry_date", "amount_usd", "reason", "author", "source"],
          adjustments, {"entry_date": _dt, "amount_usd": num(fmt_money)})

    n_orders, n_prices, n_fx = len(orders), len(pricebook), len(fx)

    # -- the analyst's working sheet -----------------------------------------
    revenue.write_row(0, 0, ["order_id", "region", "currency", "line_native",
                             "usd_rate", "line_usd"], fmt_head)
    by_region = {r: Decimal("0") for r in REGIONS}
    for i, o in enumerate(orders):
        x = i + 2                                   # 1-based sheet row
        revenue.write_formula(i + 1, 0, "=Orders!A%d" % x, None, o["order_id"])
        revenue.write_formula(i + 1, 1, "=Orders!E%d" % x, None, o["region"])
        revenue.write_formula(i + 1, 2, "=Orders!F%d" % x, None, o["currency"])

        if i == typed_row:
            native = _d(o["qty"]) * _d(o["unit_price_at_sale"])
            revenue.write_number(i + 1, 3, float(native), fmt_money)
        else:
            native = _d(o["qty"]) * price[o["sku"]]
            revenue.write_formula(
                i + 1, 3,
                "=Orders!D%d*VLOOKUP(Orders!C%d,Pricebook!$A$2:$B$%d,2,FALSE)"
                % (x, x, n_prices + 1), fmt_money, float(native))

        r = rate[o["currency"]]
        revenue.write_formula(
            i + 1, 4,
            "=SUMIFS(FX!$C$2:$C$%d,FX!$A$2:$A$%d,Summary!$B$2,"
            "FX!$B$2:$B$%d,C%d)" % (n_fx + 1, n_fx + 1, n_fx + 1, x),
            fmt_rate, float(r))

        usd = _cents(native * r)
        revenue.write_formula(i + 1, 5, "=ROUND(D%d*E%d,2)" % (x, x),
                              fmt_money, float(usd))
        by_region[o["region"]] += usd

    # -- the page the board sees ---------------------------------------------
    last = n_orders + 1
    summary.write_string(0, 0, "Halloway Instruments - FY2026 Q3 close",
                         fmt_head)
    summary.write_string(1, 0, "Close date")
    summary.write_datetime(1, 1, datetime.combine(CLOSE_DATE, datetime.min.time()),
                           fmt_date)
    summary.write_string(2, 0, "Revenue by region (USD)", fmt_head)
    for k, region in enumerate(REGIONS):
        row = 3 + k
        summary.write_string(row, 0, region)
        summary.write_formula(
            row, 1, "=SUMIFS(Revenue!$F$2:$F$%d,Revenue!$B$2:$B$%d,A%d)"
            % (last, last, row + 1), fmt_money, float(by_region[region]))
    gross = sum(by_region.values(), Decimal("0"))
    adj = sum((_d(a["amount_usd"]) for a in adjustments), Decimal("0"))
    summary.write_string(7, 0, "Gross revenue")
    summary.write_formula(7, 1, "=SUM(B4:B7)", fmt_money, float(gross))
    summary.write_string(8, 0, "Manual adjustments")
    summary.write_formula(8, 1, "=SUM(Adjustments!C2:C%d)"
                          % (len(adjustments) + 1), fmt_money, float(adj))
    summary.write_string(9, 0, "Q3 Net Revenue", fmt_head)
    summary.write_formula(9, 1, "=B8+B9", fmt_money, float(gross + adj))
    summary.set_column(0, 0, 26)
    summary.set_column(1, 1, 16)

    # The figure the workbook exists to produce. `whence ingest-workbook`
    # looks for this name when it is not told which cell to follow.
    wb.define_name("Headline", "=Summary!$B$10")
    wb.close()


def pricebook_by_sku(rows):
    return {r["sku"]: r["list_price"] for r in rows}


if __name__ == "__main__":
    build(HERE / "q3_close.xlsx", "pricebook_v3.csv")
    build(HERE / "q3_close_v2.xlsx", "pricebook_v4.csv")
    print("wrote q3_close.xlsx and q3_close_v2.xlsx")
