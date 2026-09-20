<!-- SPDX-License-Identifier: Apache-2.0 -->

# The Halloway close, in a warehouse

The same quarter close as [`whence/pipeline.py`](../whence/pipeline.py), written
in SQL and run by dbt — so that Whence can **check** a warehouse pipeline
rather than ask anyone to replace one.

That distinction is the whole point. Whence's guarantee comes from
[`tracked.py`](../whence/tracked.py): a transform takes Facts and returns a
Fact, so the arithmetic and the provenance are produced by the same call and
cannot drift apart. SQL running inside a warehouse cannot be wrapped that way.
The alternative is not to weaken the claim but to **recompute and reconcile** —
the warehouse computes as it always has, Whence computes independently from the
same source rows, and the two are compared every run. Divergence becomes an
alarm instead of a discovery six months later.

Whence already makes this argument once: [`verify.js`](../whence/report/verify.js)
is a second, independent implementation of the verifier, in another language,
shipped inside the report so that "verified" means more than "the producer says
so." This is the same move, pointed at the warehouse.

## Running it

Requires the dev environment from the repo root:

```bash
pip install -e . dbt-duckdb
```

Then, from this directory:

```bash
DBT_PROFILES_DIR=. dbt build
```

The warehouse is a single DuckDB file, `halloway.duckdb`, created next to this
README. Nothing is installed, no account is needed, and no data leaves the
machine. Swapping the `outputs` block in `profiles.yml` for a Snowflake target
is the only change the models need — which is the reason to develop against
DuckDB first rather than sign up for anything.

## What it produces

| | Whence | warehouse |
|---|---|---|
| AMER | $2,436,713.77 | $2,436,713.77 |
| EMEA | $1,424,942.90 | $1,424,942.90 |
| APAC | $459,849.71 | $459,849.71 |
| LATAM | $503,576.09 | $503,576.09 |
| **Q3 Net Revenue** | **$4,892,582.47** | **$4,892,582.47** |

`whence:b0d8cc3b3885`, 200 order lines, 2 manual adjustments.

The restated close agrees too — `--pricebook data/pricebook_v4.csv --orders
data/erp_orders_restated.csv` against `--vars '{orders_table:
erp_orders_restated, pricebook_table: pricebook_v4}'` gives $5,271,855.30 on
both sides.

## The finding

Agreement is not free, and the thing that nearly broke it is worth more than
the agreement itself.

Whence rounds to whole cents at **every step** — `core.money()` runs inside the
`@tracked` wrapper, so the rounding lands inside the hash and becomes part of
the record. SQL is not normally written that way: a data team carries full
precision through the joins and rounds once, at the figure it reports. Both
conventions are defensible. They are not the same number.

`vars.rounding_policy` in `dbt_project.yml` makes the choice explicit:

```bash
DBT_PROFILES_DIR=. dbt run --vars '{rounding_policy: at_end}'
```

| region | Whence | warehouse (`at_end`) | delta |
|---|---|---|---|
| AMER | $2,436,713.77 | $2,436,713.75 | **−$0.02** |
| EMEA | $1,424,942.90 | $1,424,942.90 | — |
| APAC | $459,849.71 | $459,849.71 | — |
| LATAM | $503,576.09 | $503,576.09 | — |
| **NET** | **$4,892,582.47** | **$4,892,582.45** | **−$0.02** |

Two cents on 200 orders, from nothing but where the rounding happened.

Scale that to a real ledger and it is not two cents. More to the point: at most
companies nobody can tell you which convention their reported revenue follows,
because nothing has ever forced the question. **That is the product.** The
reconciliation does not just confirm a number — it makes somebody state a
policy out loud and makes both systems obey it.

The repo already warned about this class of failure, for the browser verifier:
reproducing decimal arithmetic with identical rounding in two languages is
exactly the kind of thing that silently disagrees. Same trap, new language.

## Model layout

```
staging/   stg_orders, stg_pricebook, stg_fx_rates, stg_adjustments
           typed views over the four CSVs, read in place by DuckDB

marts/     int_order_lines        mirrors `multiply` then `fx_convert`
           fct_region_revenue     mirrors `sum_by_region`
           fct_revenue_summary    mirrors `sum_regions` + `apply_adjustments`
```

`fct_revenue_summary` is the model a Whence figure mapping would point at: one
row, one number, the thing that ends up on slide 4.

Policy choices are carried in `vars` rather than buried in the SQL: which
pricebook and order snapshot to close on, the closing FX date, and the rounding
policy. Changing any of them changes the reported figure, and should.

## What this does not do yet

This is steps 1–3 of the milestone: **the warehouse half**. It produces its own
Q3 number that happens to agree with Whence. Nothing yet connects them.

Still to build:

- **A warehouse source adapter** — mint `source` facts from warehouse rows the
  way `pipeline.cell_fact` does from CSV cells. The digest question needs an
  answer: hash the canonicalized result set actually read (rows sorted by key,
  cells as strings, per SPEC.md §3.3), with Snowflake's `QUERY_ID` and table
  version recorded alongside as corroboration. `Store.observe` already takes
  arbitrary fields, so no format change is needed — the observation split in
  [`core.py`](../whence/core.py) pays off here more than it does for files.
- **A figure mapping** — which model and column is a reported figure, its
  business key, and which Whence transform reproduces it.
- **The reconciliation record** — agreement or divergence written to the run
  journal alongside dbt's `invocation_id` and the project's git SHA, and
  surfaced as a finding in `explain` next to unapproved pricebook rows.
- **Scale.** A fact per cell is fine at 200 orders and unusable at 40 million.
  The answer is to mint facts at the reported grain and Merkle-tree the rows
  beneath, so a bundle carries a root and an auditor drilling into one row gets
  an inclusion proof rather than forty million records.

## What it will never do

If the SQL implements the wrong policy and the Whence transform faithfully
mirrors the wrong policy, both agree and both are wrong. Whence proves a number
follows from its inputs. It has never claimed to prove the policy is right, and
reconciliation does not change that.
