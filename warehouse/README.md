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

## Reconciling

`whence reconcile` reads the warehouse's **source rows**, recomputes the close
through the same transforms the CSV pipeline uses, and compares the answer to
what the warehouse published.

```bash
python -m whence reconcile --dbt-target warehouse/target
```

```
      figure                                warehouse         whence  delta
  OK  Q3 Net Revenue                    $4,892,582.47  $4,892,582.47      -
  OK  Gross revenue before adjustments  $4,825,082.47  $4,825,082.47      -
  OK  AMER subtotal                     $2,436,713.77  $2,436,713.77      -
  OK  EMEA subtotal                     $1,424,942.90  $1,424,942.90      -
  OK  APAC subtotal                       $459,849.71    $459,849.71      -
  OK  LATAM subtotal                      $503,576.09    $503,576.09      -

    stg_adjustments      sha256 ffbf65e4fd79cef3
    stg_fx_rates         sha256 7895943da935df34
    stg_orders           sha256 786f62c4c589f412
    stg_pricebook        sha256 32bb01dc854acf65
    dbt invocation       917e5fdb-13fd-47bc-ad63-0f0372cae048

  AGREED  every figure recomputed from the warehouse's own rows
  matched what the warehouse published.

  whence:b0d8cc3b3885  is the reconciled figure
```

**That is the same id the CSV close mints.** The same quarter, computed from a
database rather than from four files, lands on the identical fingerprint —
content addressing holding across a system boundary rather than only within one
program. Nothing coordinates the two; they agree because the claims are the
same.

Break it and it says so, with the cause attributed. Rebuilt under the other
rounding policy:

```
  X   Q3 Net Revenue                    $4,892,582.45  $4,892,582.47  -$0.02
  X   Gross revenue before adjustments  $4,825,082.45  $4,825,082.47  -$0.02
  X   AMER subtotal                     $2,436,713.75  $2,436,713.77  -$0.02
  OK  EMEA subtotal                     $1,424,942.90  $1,424,942.90       -

  DIVERGED  3 of 6 figures do not match.
```

AMER is named as the source and the two cents are traced up through gross into
the reported total, while the three untouched regions pass. The command exits
non-zero, so it belongs in whatever pipeline already runs `dbt build`.

### How it fits together

`warehouse/mapping.json` is the only new configuration: which tables hold the
source rows, and which model columns are reported figures. It is meant to be
short enough for a controller to read.

Three things are worth knowing about the implementation
([`whence/warehouse.py`](../whence/warehouse.py)):

**The close is not duplicated.** `pipeline.close()` runs unchanged over
warehouse tables, because a `WarehouseTable` is shaped like a `SourceFile` and
reports its own cell coordinates. A second copy of the close would reintroduce,
one level up, exactly the drift `tracked.py` exists to prevent.

**The digest is of the result set, not of a file.** A warehouse has no bytes to
hash, so what gets pinned is the canonicalized rows actually read: sorted (SQL
without `ORDER BY` promises nothing about order), every cell a string, by the
same rules as SPEC.md section 3.3. The table name, the warehouse and the
engine's own query id are *coordinates* — they go in the observation, never in
the hash, for the same reason a file's path does not (section 3.8).

**Agreement is a run, not a fact.** Two systems agreeing at one moment is a
property of that moment, like a clock, so it lives in the run journal rather
than in any hash. Because the record carries the figure's root id, `whence
attest` sweeps it into the bundle: an auditor opening the `.wbundle` sees not
only that the arithmetic holds but that a second system independently produced
the same number. `whence explain` shows it as corroboration — in green, kept
apart from findings, because a page that renders good news with a warning
marker is telling the reader something untrue.

Every read goes through DB-API 2.0, so nothing here is DuckDB-specific. A real
deployment builds its own connection with `snowflake-connector-python` or
`psycopg` and hands it to `reconcile()`, which never asks what made it.

### Fidelity is the hard part

Three source-faithfulness bugs surfaced while building this, and every one of
them would have shown up as spurious divergence:

| | The warehouse said | The file says |
|---|---|---|
| A blank approval reference | `NULL` | the empty string |
| An FX rate | `1.093400` (6dp) | `1.0934` |
| An adjustment's evidence column | renamed to `evidence` | `source` |

None of them changes any total. All three change a *claim*, and therefore an
id. They are fixed in the staging models rather than in the Python adapter, so
each correction is visible in SQL a reviewer can read instead of hidden at the
boundary.

That is the recurring lesson of the exercise. Reconciling two systems is mostly
not about arithmetic; it is about finding the places where they were quietly
describing slightly different things.

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

- **Scale.** A fact per cell is fine at 200 orders and unusable at 40 million,
  and reading whole tables into memory to recompute them stops working long
  before that. The answer is to mint facts at the reported grain and
  Merkle-tree the rows beneath, so a bundle carries a root and an auditor
  drilling into one row gets an inclusion proof rather than forty million
  records.
- **Data that will not leave the warehouse.** Some organizations will not allow
  it, which is the argument that eventually forces the hashing into SQL as a
  UDF. Worth learning which side of that line a first customer sits on before
  building it.
- **A real warehouse.** Everything goes through DB-API 2.0 and should work
  against Snowflake by swapping the connection, but *should* is not *does*
  until someone runs it.
- **Column renames.** The mapping names tables and figures; it cannot yet
  express that staging renamed a column. The prototype sidesteps this by
  keeping the staging models faithful, which a real project would not.

## What it will never do

If the SQL implements the wrong policy and the Whence transform faithfully
mirrors the wrong policy, both agree and both are wrong. Whence proves a number
follows from its inputs. It has never claimed to prove the policy is right, and
reconciliation does not change that.
