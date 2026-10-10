<!-- SPDX-License-Identifier: Apache-2.0 -->

# Fixture data

**Everything in this directory is synthetic.**

Halloway Instruments does not exist. The SKUs are not anyone's catalogue, the
prices are not anyone's pricing, the FX rates are plausible-looking noise rather
than market data, and the people named in these files -- m.jensen, s.patel,
r.okonkwo, t.almeida, d.fairbanks, l.vasquez -- are invented. Nothing here
resembles any real company's books, customers or employees.

You do not have to take that on faith. `_generate.py` is the seeded script that
produced every file, committed alongside them:

```bash
python data/_generate.py
```

It is deterministic, so it rewrites byte-identical files every time. The two
workbooks are built from those CSVs by a second script, also deterministic:

```bash
python data/_workbook.py
```

## The files

| File | Rows | What it is |
|---|---|---|
| `erp_orders.csv` | 200 | The ERP order export the first close ran against |
| `erp_orders_restated.csv` | 214 | The same export plus 14 APAC orders that arrived a day late |
| `pricebook_v3.csv` | 47 | List price per SKU. Three rows carry no approval reference |
| `pricebook_v4.csv` | 47 | The same book after m.jensen repriced those three SKUs on 2026-08-14 |
| `fx_rates.csv` | 276 | Daily USD/EUR/GBP rates across the quarter |
| `adjustments.csv` | 2 | Manual journal entries whose only upstream system is a person |
| `q3_close.xlsx` | — | The close as an analyst built it in Excel: the four files pasted into tabs, formulas over them, a summary page |
| `q3_close_v2.xlsx` | — | The same workbook with `pricebook_v4.csv` pasted into the Pricebook tab |

## The wrinkles are deliberate

Each one exists to make a specific point in the demo:

- **APAC's late export** gives the restatement a cause that is *structural* --
  orders that appear rather than change -- so `diff` has to distinguish "this
  number moved" from "this row is new".
- **The three unapproved pricebook rows** are also the three rows that get
  edited. The figure surfaces them as a finding before anybody goes looking,
  and then they turn out to be exactly what moved the quarter.
- **The manual adjustments** have no upstream system at all. One cites an email
  from the CFO, the other a Slack thread. They are valid, they are recorded,
  and they are permanently flagged as unverifiable.
- **One formula in the workbook was typed over.** In `Revenue!D12` the
  analyst replaced the line-revenue formula with a number: the quantity times
  the price the customer was invoiced at, rather than the list price the close
  recognizes. It is plausible, it is $375.00 off, and nothing on the face of
  the workbook gives it away. The same cell is typed over in both workbooks,
  and is not one of the repriced SKUs, so the restatement and the mistake stay
  separable.
- **Six orders were invoiced off-list.** Revenue is recognized at the approved
  list price, so the discrepancy is recorded on each line and reported.

## If you regenerate

Every identifier in the README transcript and in `SPEC.md` section 3.5 is
derived from these bytes. Changing the fixtures changes all of them, and
`tests/test_spec.py` will fail until they are updated.
