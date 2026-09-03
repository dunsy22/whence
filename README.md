<!-- SPDX-License-Identifier: Apache-2.0 -->

# Whence

**Every number your business produces should be able to cite its sources.**

---

When someone asks where a figure on slide 4 came from, the honest answer is
usually a week of email. Whence makes that answer instant: as each number is
calculated, it records what it was built from, who touched it, and which file
and row every input came from — automatically, as part of the calculation
rather than as paperwork afterwards. Each figure gets a short fingerprint, and
because that fingerprint is derived from the entire chain beneath it, two
people who start from the same data and do the same arithmetic get the *same
fingerprint* without ever comparing spreadsheets. When a number changes, Whence
tells you exactly which inputs moved, by how much, and who changed them — in
seconds, not days. And any figure can be exported as a single file that an
auditor can verify on their own laptop, with no access to your ERP, your
pricebook, or your systems at all.

Numbers that a person simply typed in — the accrual from the CFO's email, the
adjustment agreed in a Slack thread — are recorded too, and permanently marked
as unverifiable. That is a feature. Those are the numbers you most want to see
flagged, and no system that quietly launders them into the total is telling you
the truth.

> **This is a proof of concept**, built to demonstrate an idea. It is not
> production software. See [Honest limitations](#honest-limitations).

---

## Try it

Requires Python 3.11 or newer.

```bash
pip install -e .
python -m whence demo
```

Or, without managing a virtual environment at all:

```bash
uv run whence demo
```

The demo is a scripted seven-beat walkthrough of a fictional company's quarter
close. It runs in about four seconds and pauses between beats; add `--no-pause`
to run straight through.

```bash
python -m whence run --report board-deck.html   # a board page you can double-click
python -m pytest                                # 99 tests
```

> **Why `python -m whence` rather than just `whence`?**
>
> Installing does put a `whence` command on your `PATH`, and it works fine on
> bash, Linux and Windows. But `whence` is also a **shell builtin in zsh and
> ksh** — a `which`-style lookup — and builtins take precedence over installed
> programs. On macOS, where zsh has been the default shell since Catalina,
> typing `whence demo` runs the builtin and prints *nothing at all*: no output,
> no error, just the prompt back.
>
> `python -m whence` has no such collision and works everywhere, so the
> documentation uses it. If you are on zsh and want the short form,
> `command whence demo` bypasses the builtin. The `uv run whence` line above is
> safe either way — there, `whence` is an argument to `uv` rather than a command
> the shell has to resolve.

---

## The scenario

Halloway Instruments — fictional, along with every SKU, price and person in
`data/` — is closing FY2026 Q3. Four files go in:

| File | What it is | The wrinkle |
|---|---|---|
| `erp_orders.csv` | 200 order lines from the ERP | APAC's export was a day late, so 14 orders are missing |
| `pricebook_v3.csv` | List price per SKU, with approval references | Three rows have no approval reference at all |
| `fx_rates.csv` | Daily EUR/GBP/USD rates | — |
| `adjustments.csv` | Manual journal entries | These have no upstream system whatsoever |

The pipeline is: **orders × pricebook → line revenue → FX-normalise to USD →
roll up by region → apply adjustments → `Q3 Net Revenue`**.

---

## The demo, in full

Verbatim output of `python -m whence demo --no-pause`. Nothing below is a
hardcoded string; every figure and every identifier is computed at the moment
it prints.

```text
  Whence - where did this number come from?
  Halloway Instruments is closing FY2026 Q3. Nothing below is pre-recorded.

1 / 7  Run the close --------------------------------------------------------------

  Four files go in: an ERP order export, the pricebook, daily FX rates, and a
  handful of manual journal entries. One number comes out - the one that goes on
  slide 4.

  Q3 Net Revenue
  $4,892,582.47   whence:b0d8cc3b3885

  200 order lines | 47 SKUs | 658 facts recorded along the way

  That id is not a database key. It is the SHA-256 of this figure's
  entire derivation. Change anything upstream and it becomes a different id.

2 / 7  Ask the question everyone asks ---------------------------------------------

  Someone on the board asks where the number came from. Today that is a week of
  email. Here it is one command, and the answer is a sentence, not a graph.

  Q3 Net Revenue = $4,892,582.47   whence:b0d8cc3b3885

    Built from 4 regional subtotals and 2 manual adjustments tracing back to 200
    orders across 658 recorded facts.

  -> AMER         $2,436,713.77   from 98 orders, FX-converted at Sep 30 rates
  -> APAC           $459,849.71   from 17 orders, FX-converted at Sep 30 rates
  -> EMEA         $1,424,942.90   from 63 orders, FX-converted at Sep 30 rates
  -> LATAM          $503,576.09   from 22 orders, FX-converted at Sep 30 rates
  -> Adjustment      $80,000.00 ! asserted by s.patel, "Q3 rebate accrual per CFO
                                  email"
                                  no upstream system - this number cannot be
                                  verified
  -> Adjustment     -$12,500.00 ! asserted by r.okonkwo, "Reverse duplicate LATAM
                                  shipment billed twice"
                                  no upstream system - this number cannot be
                                  verified


3 / 7  The uncomfortable part -----------------------------------------------------

  Nobody went looking for these. They are what falls out of asking a figure to
  account for itself.

  ! 2 manual entries ($92,500.00 of movement, $67,500.00 net) were asserted by
    people with no upstream system and cannot be recomputed: ADJ-001, ADJ-002.

  ! 3 of 47 SKUs in this figure were priced from pricebook rows with no approval
    reference (SKU-4471, SKU-4472, SKU-5013).

  ! 6 orders were invoiced at a unit price different from the approved list price;
    revenue is recognized at list (ORD-10011, ORD-10014, ORD-10076, ORD-10080,
    ORD-10103, ORD-10179).

  The $80,000 accrual is not an error and Whence does not treat it as one. It is
  a number a person typed on the strength of an email. It is recorded, it is
  unaltered, and it is marked - permanently - as something no amount of
  recomputation can confirm.

4 / 7  The restatement ------------------------------------------------------------

  APAC's export was a day late, so the first close was missing 14 orders.
  Meanwhile someone edited the pricebook. Close it again.

  first pass                $4,892,582.47   whence:b0d8cc3b3885
  restated                  $5,271,855.30   whence:0acf5ad5904c

  Both figures still exist. Nothing was overwritten - facts are append-only,
  so a restatement adds a number rather than replacing one.

5 / 7  The reconciliation that used to take a week --------------------------------

  Two numbers, both called Q3 net revenue, $379,272.83 apart. The usual next step
  is two analysts and five days of spreadsheet archaeology.

  These two figures differ by $379,272.83.

    first     $4,892,582.47   whence:b0d8cc3b3885
    second    $5,271,855.30   whence:0acf5ad5904c

  The entire difference traces to 2 independent causes:

  1  3 SKUs changed list price between the two runs
      SKU-4471   $1,200.00 -> $1,340.00                   +$10,360.00
        changed 2026-08-14 by m.jensen, no approval ref
      SKU-4472   $890.00 -> $980.00                       +$10,800.00
        changed 2026-08-14 by m.jensen, no approval ref
      SKU-5013   $2,100.00 -> $2,310.00                   +$22,680.00
        changed 2026-08-14 by m.jensen, no approval ref
                                                          +$43,840.00

  2  14 orders appear in the second figure that were not in the first (APAC)
      APAC: ORD-10201, ORD-10202, ORD-10203 and 11 more  +$335,432.83
                                                         +$335,432.83

  Everything else is identical: 619 of the 739 facts behind these two figures are
  the same facts, and the causes above add up to the difference exactly.

  Named actor, dated change, exact amount, and the assurance that
  nothing else moved.

6 / 7  Two analysts, one truth ----------------------------------------------------

  Dana and Priya were both asked for Q3 EMEA revenue. They wrote separate scripts
  - different structure, different iteration order, separate fact stores, no
  contact. Read demo/analyst_a.py and demo/analyst_b.py; they share no code.

  Dana         cc71f1c3227d2ef8a942b96b393e2ac46cf645db6954882eec98d0408d6f252c
  Priya        cc71f1c3227d2ef8a942b96b393e2ac46cf645db6954882eec98d0408d6f252c

  Identical. Not 'the totals matched' - the same 64 characters.
  Neither of them had to send the other a spreadsheet. Two people who start from
  the same inputs and apply the same logic land on the same id, and that
  agreement is checkable by a third person who saw neither calculation.

  Now give Priya one edited input - the pricebook with three changed rows.

  Priya (v4)   043926edf6a07862b3804b4efb274cd9ce6dae93ba44707a031e212129936ec5

  Different, instantly. No comparison of numbers was needed to know that.

7 / 7  Hand it to the auditor -----------------------------------------------------

  An auditor wants to check the restated figure. They do not get a login to the
  ERP, and they should not need one.

  bundle           q3-net-revenue.wbundle
  contents         700 facts, 584 KB
  format           DSSE envelope, in-toto Statement v1

  The auditor's machine, in full:
    q3-net-revenue.wbundle

  No ERP. No pricebook. No .whence directory. No network.


  Verifying whence:0acf5ad5904c  Q3 Net Revenue = $5,271,855.30
  from bundle q3-net-revenue.wbundle

    OK   700 records intact       every id re-derived from its own contents
    OK   434 figures recomputed   transforms re-run against their inputs
    OK   264 source values        read from files, digests recorded
    !     2 asserted values      no upstream system; cannot be recomputed
         ADJ-002  -$12,500.00  r.okonkwo - "Reverse duplicate LATAM shipment billed
twice"
         ADJ-001  $80,000.00  s.patel - "Q3 rebate accrual per CFO email"

  VERIFIED  every derived figure was re-run and matched.
  The asserted values above are recorded and unaltered, but nothing
  can confirm they are right.
  This bundle is unsigned: the hashes prove what it says and that
  nothing has been altered, but not who produced it.

  Built from files this machine has never seen:
    adjustments.csv            sha256 a53a93b59b757d2c
    erp_orders_restated.csv    sha256 869665ef2059489e
    fx_rates.csv               sha256 eb9b9384d9b26d30
    pricebook_v4.csv           sha256 b38dcbdde8ae3264

-----------------------------------------------------------------------------------

  Every figure in this demo can still be interrogated. With `python -m whence`,
  try:

    python -m whence explain 0acf5ad5904c
    python -m whence verify b0d8cc3b3885
    python -m whence run --report board-deck.html
```

---

## How it works

### One primitive

Everything is a **Fact**: a value, how it was produced, and what it was
produced from.

```python
Fact:
  id         # sha256 of the canonical serialization of everything below
  value      # the number, as text
  kind       # "source" | "derived" | "asserted"
  transform  # "multiply", "fx_convert", "sum_by_region", ...
  inputs     # parent Fact ids
  metadata   # actor, sku, approval reference, notes
```

Facts reference their parents by hash, so a set of Facts is a Merkle DAG and
the root id commits to the whole derivation beneath it. Change one price in one
row and every figure downstream gets a new id — which is what makes both
agreement and tampering visible.

Read [`whence/core.py`](whence/core.py) first. It is about 200 lines of
substance and it contains the entire trust model.

### Computing and recording are the same act

The argument this project is really making lives in
[`whence/tracked.py`](whence/tracked.py):

```python
@tracked("multiply", commutative=True)
def line_revenue(qty, unit_price):
    return qty * unit_price
```

A tracked transform takes Facts and returns a Fact. There is no code path that
produces the number without the provenance, because they are the same object,
produced by the same call, from the same values. This is the difference between
Whence and a lineage logger: a logger is a second description of the pipeline
that starts out accurate and drifts. Here there is nothing to drift *from*.

`whence verify` re-runs these very functions from the registry they register
themselves in, so verification exercises the same code the pipeline did rather
than a reimplementation of it.

### Verification is recomputation

Nothing is secret and nothing is signed. To verify a figure you re-derive every
id from the record's own bytes, confirm every parent is present, and re-run
every transform against its parents' values. If the numbers come out the same,
the figure holds.

[`whence/attest.py`](whence/attest.py) is the file to read if you don't trust
the rest of the repository. It uses only the standard library, and it is
written to be auditable in an afternoon.

### The format is the artifact

[`SPEC.md`](SPEC.md) defines the record, the canonical serialization, the hash
construction and the bundle format precisely enough to write an independent
verifier in another language. The Python here is one implementation of that
spec.

To keep that claim honest rather than aspirational, there is a second
implementation in the repository: about 150 lines of JavaScript in
[`whence/report/verify.js`](whence/report/verify.js), written against the spec
and embedded in every generated report. Click **Verify this page** and the
browser re-derives all 658 identifiers itself, in around 30 ms, using its own
SHA-256 (which self-tests against a published vector first). Two independent
implementations agreeing on 658 hashes is worth more than either one asserting
it is correct.

The spec's four conformance vectors are parsed out of `SPEC.md` by
`tests/test_spec.py` and checked against the code, so the document cannot rot
without a test failing.

---

## The commands

| Command | What it does |
|---|---|
| `whence run` | Execute the close; print the headline figure and its id |
| `whence explain <id>` | Walk a figure back to raw sources, in plain English |
| `whence verify <id>` | Recompute from sources; confirm or report drift |
| `whence diff <a> <b>` | Explain why two figures differ, in business language |
| `whence attest <id> -o f.wbundle` | Export a portable, verifiable bundle |
| `whence verify-bundle f.wbundle` | Verify with no access to source systems |

`explain` also takes `--tree` for the full DAG and `--json` for machines; the
narrative is the default because it is the view that has to land with people
who do not want a graph. Ids may be abbreviated to any unambiguous prefix.

`diff` does the real work of the set: it walks both DAGs, stops descending
wherever two ids match (content-addressing prunes the search), and corners each
difference at the leaf that caused it. It then attributes an exact dollar
amount to each cause and checks that the attributed amounts add up to the
difference between the two headline figures. In the demo that is $43,840.00
from three repriced SKUs plus $335,432.83 from fourteen late orders, summing to
the $379,272.83 delta with a residual of exactly zero — out of the ~739 facts
behind the two figures, of which 619 are shared and silent.

---

## Design decisions worth arguing with

Three choices shape everything, and a reviewer should push on all three. Each
is argued at length in `SPEC.md`; the short versions:

**Nothing hashed is a number.** Every value is a string, and money is
fixed-point text quantized to cents at the moment a Fact is minted. JSON
numbers have no canonical text form and floats differ across languages, so
allowing either would make two correct implementations disagree for reasons
unrelated to the data. The cost is that producers must state their rounding —
which is arguably the right thing to force anyway.

**Timestamps are not in the hash.** If they were, the same computation over the
same inputs would get a new id on every run and content-addressing would buy
nothing. When something ran is a property of the *run*, recorded in
`runs.jsonl` and joined back in for display.

**Nor is where a value was read.** A source Fact does not hash the file path or
the file's digest; those go in an append-only observation log. This one is easy
to get wrong: if the file digest were hashed, editing one row of a 47-row
pricebook would give all 47 prices new ids, and `diff` could no longer tell you
which three actually changed. The id answers *what is claimed*; the observation
log answers *where we saw it claimed*. Business attributes that describe the
claim — effective date, who modified the row, whether it carries an approval
reference — **are** hashed, because those are part of what the row asserts.

---

## Repository layout

```
whence/
  core.py         Fact, hashing, the store -- read this first
  tracked.py      the decorator that fuses compute and provenance
  pipeline.py     the Q3 close, readable top to bottom
  explain.py      narrative rendering
  diff.py         minimal causal difference
  attest.py       bundle export and standalone verification
  cli.py          six commands, plus shared renderers
  demo.py         the scripted walkthrough
  report/         jinja2 template, CSS and JS, inlined at render time
data/             the four fixture CSVs, their restated variants, and the
                  seeded generator that produced them
demo/             two analysts' independently written scripts
tests/            99 tests: determinism, tamper-detection, diff correctness,
                  bundle verification, spec conformance
SPEC.md           the format, independent of this implementation
```

The whole codebase is meant to be readable in half an hour. If any part of it
is not, that is a bug.

---

## Honest limitations

This is a demonstration of an idea, not a product.

- **Nothing is signed.** Bundles are unsigned DSSE envelopes. The hashes prove
  what a bundle says and that it has not been altered; they prove nothing about
  who produced it. Key custody is a real problem and this does not solve it.
  The exact bytes a signer would sign are implemented and tested (`attest.pae`)
  so that adding signing is not archaeology.
- **The store is a directory of files.** Single machine, single writer, no
  concurrency control, no compaction. A quarter close writes ~700 small files.
- **The pipeline is one hardcoded close.** Real ledgers have hundreds of
  transforms, intercompany eliminations, and revenue recognition rules that do
  not fit in a 250-line file.
- **The browser verifier re-adds sums but does not re-run multiplications.**
  Reproducing decimal multiplication with identical rounding in two languages
  is exactly the kind of thing that silently disagrees, so the page says which
  checks it performed rather than implying it did more. `whence verify` re-runs
  everything.
- **Only the closing FX rate is used.** The daily rate file is read and cited,
  but a transaction-date policy would be a different (one-line) choice.
- **`diff` cannot always split a delta.** When two inputs to a single
  multiplication both change, the causes are still identified but the dollar
  attribution between them is not exact. The report says so instead of
  guessing.

## What a real product would add

- **Source-system connectors** — reading from NetSuite, SAP, Snowflake and the
  like directly, so facts are minted at the boundary instead of from a CSV
  export somebody made.
- **A spreadsheet add-in.** Numbers escape into Excel immediately and forever.
  Provenance that does not survive a copy-paste into a spreadsheet has not
  solved the actual problem.
- **A shared, multi-user provenance store** with concurrent writers, access
  control, and retention — rather than a directory on one laptop.
- **Signing with real key custody**, so a bundle attests to its origin and not
  only to its contents: HSM or KMS-backed keys, rotation, and a trust policy
  saying whose attestations count.
- **Continuous re-verification** of previously certified figures, so that a
  restatement upstream raises an alarm against the board deck that already went
  out, rather than waiting to be discovered.
- Deeper things this deliberately leaves alone: a graph visualisation canvas,
  user accounts, a server, a web API, and configuration options nobody asked
  for.

---

## Licence

Apache License 2.0 — see [`LICENSE`](LICENSE) and [`NOTICE`](NOTICE).

That choice is strategic rather than incidental. A provenance system whose
verification logic cannot be inspected is not verification; it is an assertion.
Anyone must be able to read exactly what goes into a hash and confirm for
themselves that the verifier is not simply returning green. Nothing in this
repository is held back for a future commercial tier — everything here works,
completely, for free.

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for which parts are stable and which
are explicitly experimental.
