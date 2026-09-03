# SPDX-License-Identifier: Apache-2.0
"""The Q3 revenue close, written to be read top to bottom.

    orders x pricebook  ->  line revenue
                        ->  FX-normalize to USD
                        ->  roll up by region
                        ->  apply manual adjustments
                        ->  Q3 Net Revenue

Every arithmetic step below is a @tracked transform, so running this function
is the same act as recording where the answer came from. There is no separate
lineage pass, and there is no way to add a step here that computes a number
without leaving a trail -- a tracked transform only accepts Facts and only
returns Facts.

Two modelling decisions a finance reviewer would rightly ask about, made
explicit here rather than buried:

  * Revenue is recognized at the approved pricebook list price, not at the
    price the ERP happened to capture at the point of sale. Where the two
    disagree, the captured price is recorded on the line (`price_at_sale`) and
    `explain` surfaces the count. That is why editing the pricebook restates
    the quarter -- which is the behaviour the demo turns on.

  * All lines are translated at the 2026-09-30 closing rate, not at each
    order's own date. The daily rate file is still the cited source, so
    switching to a transaction-date policy is a one-line change to
    `_rate_row_for` and would produce visibly different ids.
"""

from __future__ import annotations

import csv
import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Dict, Iterable, List, Mapping

from .core import Fact, Store, active_store, asserted_fact, current_store, source_fact
from .tracked import tracked

CLOSE_DATE = "2026-09-30"
REGION_ORDER = ["AMER", "EMEA", "APAC", "LATAM"]

DATA = Path(__file__).resolve().parent.parent / "data"


# ---------------------------------------------------------------------------
# Reading files, and turning cells into source facts.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SourceFile:
    """A CSV that has been read once, with its content pinned by digest."""
    name: str                 # display name only -- see the note in cell_fact
    path: Path
    digest: str               # sha256 of the exact bytes on disk
    rows: List[dict]          # each row carries `_line`, its physical line number

    def where(self, **match) -> dict:
        for row in self.rows:
            if all(row.get(k) == v for k, v in match.items()):
                return row
        raise KeyError("no row in %s matching %r" % (self.name, match))


def load(path: Path | str) -> SourceFile:
    path = Path(path)
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    text = raw.decode("utf-8")
    reader = csv.DictReader(text.splitlines())
    rows = []
    for offset, row in enumerate(reader, start=2):   # line 1 is the header
        row["_line"] = str(offset)
        rows.append(row)
    return SourceFile(name=path.name, path=path, digest=digest, rows=rows)


def cell_fact(src: SourceFile, row: Mapping[str, str], column: str,
              key: str, **extra: str) -> Fact:
    """Mint the source fact for one cell of one row of one file.

    The hashed part is the *claim*: this SKU lists at this price, effective
    this date, approved by this reference (or by nothing). The file it was read
    from, the row number, and the file's digest are recorded separately as an
    observation -- see Store.observe for why that split matters.

    The short version: if the digest were hashed into the fact, then editing
    one row of the pricebook would give all 47 prices new ids, and `diff` could
    no longer tell you which three actually moved.
    """
    fact = source_fact(row[column], {
        "key": key,
        "label": extra.pop("label", column),
        **extra,
    })
    store = current_store()
    store.put(fact)
    store.observe(fact.id, file=src.name, row=row["_line"], column=column,
                  digest=src.digest)
    return fact


# ---------------------------------------------------------------------------
# The transforms. Four operations; that is the whole calculation.
# ---------------------------------------------------------------------------

@tracked("multiply", commutative=True)
def line_revenue(qty: Decimal, unit_price: Decimal) -> Decimal:
    """Quantity times unit price. Order of operands is irrelevant, so two
    people who write it the other way round get the same id."""
    return qty * unit_price


@tracked("fx_convert")
def fx_convert(amount: Decimal, usd_per_unit: Decimal) -> Decimal:
    """Translate to USD. NOT commutative: an amount scaled by a rate is a
    different claim from a rate scaled by an amount, even though the
    multiplication is the same, so the operand roles are recorded in the hash."""
    return amount * usd_per_unit


@tracked("sum_by_region", commutative=True, variadic=True)
def sum_by_region(amounts: List[Decimal]) -> Decimal:
    """Roll the USD lines of one region into a subtotal."""
    return sum(amounts, Decimal("0"))


@tracked("sum_regions", commutative=True, variadic=True)
def sum_regions(amounts: List[Decimal]) -> Decimal:
    """Gross revenue: the regional subtotals, added up."""
    return sum(amounts, Decimal("0"))


@tracked("apply_adjustments", commutative=True, variadic=True)
def apply_adjustments(amounts: List[Decimal]) -> Decimal:
    """Gross revenue plus every manual journal entry. Addition is addition --
    the adjustments are marked in the DAG by their `asserted` kind, not by
    being treated specially here."""
    return sum(amounts, Decimal("0"))


# ---------------------------------------------------------------------------
# The result of a run.
# ---------------------------------------------------------------------------

@dataclass
class RunResult:
    label: str
    net: Fact
    gross: Fact
    regions: List[Fact]
    adjustments: List[Fact]
    order_count: int
    sku_count: int
    unapproved_skus: List[str]
    price_variance_orders: int
    sources: Dict[str, str] = field(default_factory=dict)
    ran_at: str = ""

    @property
    def id(self) -> str:
        return self.net.id


# ---------------------------------------------------------------------------
# The close.
# ---------------------------------------------------------------------------

def run_close(store: Store,
              orders_path: Path | str = None,
              pricebook_path: Path | str = None,
              fx_path: Path | str = None,
              adjustments_path: Path | str = None,
              label: str = "Q3 close") -> RunResult:
    """Execute the quarter close against the four input files."""
    orders_path = Path(orders_path or DATA / "erp_orders.csv")
    pricebook_path = Path(pricebook_path or DATA / "pricebook_v3.csv")
    fx_path = Path(fx_path or DATA / "fx_rates.csv")
    adjustments_path = Path(adjustments_path or DATA / "adjustments.csv")

    with active_store(store):
        orders = load(orders_path)
        pricebook = load(pricebook_path)
        fx = load(fx_path)
        adjustments = load(adjustments_path)

        price_facts = {}
        rate_facts = {}
        by_region: Dict[str, List[Fact]] = {}
        unapproved: set = set()
        variance_orders = 0

        for row in orders.rows:
            sku, region = row["sku"], row["region"]
            currency = row["currency"]

            price = _price_fact(pricebook, sku, price_facts)
            rate = _rate_fact(fx, currency, rate_facts)

            pricebook_row = pricebook.where(sku=sku)
            if not pricebook_row["approval_ref"].strip():
                unapproved.add(sku)

            qty = cell_fact(
                orders, row, "qty",
                key="src:order:%s.qty" % row["order_id"],
                label="quantity ordered",
                order_id=row["order_id"], sku=sku, region=region,
                order_date=row["order_date"],
            )

            at_sale = row["unit_price_at_sale"]
            if at_sale != pricebook_row["list_price"]:
                variance_orders += 1

            native = line_revenue(qty, price, meta={
                "key": "line:%s" % row["order_id"],
                "label": "%s x %s" % (row["qty"], sku),
                "order_id": row["order_id"], "sku": sku, "region": region,
                "currency": currency, "order_date": row["order_date"],
                # Recorded, hashed, and visible: what the ERP actually charged,
                # next to the approved price we recognized at.
                "price_at_sale": at_sale,
                "price_recognized": pricebook_row["list_price"],
            })

            usd = fx_convert(native, rate, meta={
                "key": "usd:%s" % row["order_id"],
                "label": "%s in USD" % row["order_id"],
                "order_id": row["order_id"], "sku": sku, "region": region,
                "currency": currency, "rate_date": CLOSE_DATE,
            })
            by_region.setdefault(region, []).append(usd)

        regions = []
        for region in REGION_ORDER:
            lines = by_region.get(region)
            if not lines:
                continue
            regions.append(sum_by_region(lines, meta={
                "key": "region:%s" % region,
                "label": "%s subtotal" % region,
                "region": region,
                "order_count": str(len(lines)),
            }))

        gross = sum_regions(regions, meta={
            "key": "gross",
            "label": "Gross revenue before adjustments",
            "region_count": str(len(regions)),
        })

        adj_facts = [_adjustment_fact(adjustments, row) for row in adjustments.rows]

        net = apply_adjustments([gross] + adj_facts, meta={
            "key": "net:q3",
            "label": "Q3 Net Revenue",
            "period": "FY2026-Q3",
            "adjustment_count": str(len(adj_facts)),
        })

        ran_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        sources = {
            orders.name: orders.digest,
            pricebook.name: pricebook.digest,
            fx.name: fx.digest,
            adjustments.name: adjustments.digest,
        }
        # Wall-clock time lives here, in the run journal, and never in a Fact.
        # That is what makes `whence run` twice produce the same id.
        store.record_run({
            "ran_at": ran_at,
            "label": label,
            "root": net.id,
            "gross": gross.id,
            "sources": sources,
            "orders": len(orders.rows),
        })

        return RunResult(
            label=label, net=net, gross=gross, regions=regions,
            adjustments=adj_facts, order_count=len(orders.rows),
            sku_count=len(pricebook.rows), unapproved_skus=sorted(unapproved),
            price_variance_orders=variance_orders, sources=sources, ran_at=ran_at,
        )


def _price_fact(pricebook: SourceFile, sku: str, cache: dict) -> Fact:
    if sku not in cache:
        row = pricebook.where(sku=sku)
        cache[sku] = cell_fact(
            pricebook, row, "list_price",
            key="src:price:%s.list_price" % sku,
            label="%s list price" % sku,
            sku=sku,
            effective_date=row["effective_date"],
            modified_by=row["last_modified_by"],
            approval_ref=row["approval_ref"],
        )
    return cache[sku]


def _rate_fact(fx: SourceFile, currency: str, cache: dict) -> Fact:
    if currency not in cache:
        row = _rate_row_for(fx, currency, CLOSE_DATE)
        cache[currency] = cell_fact(
            fx, row, "usd_per_unit",
            key="src:fx:%s.%s" % (CLOSE_DATE, currency),
            label="%s/USD rate at %s close" % (currency, CLOSE_DATE),
            currency=currency, rate_date=CLOSE_DATE,
        )
    return cache[currency]


def _rate_row_for(fx: SourceFile, currency: str, on: str) -> dict:
    """The single place the FX policy lives. Change this line and the whole
    quarter gets new ids -- which is the correct amount of noise for a change
    in accounting policy."""
    return fx.where(rate_date=on, currency=currency)


def _adjustment_fact(src: SourceFile, row: Mapping[str, str]) -> Fact:
    """A manual journal entry.

    This one is `asserted`, not `source`, and the distinction is the point. The
    CSV is where the number was written down; it is not where the number came
    from. Its actual provenance is a person and an email, and no amount of
    machinery can turn that into something recomputable -- so Whence records it
    honestly and flags it forever instead of pretending otherwise.
    """
    fact = asserted_fact(row["amount_usd"], {
        "key": "adj:%s" % row["adj_id"],
        "label": row["reason"],
        "adj_id": row["adj_id"],
        "actor": row["author"],
        "reason": row["reason"],
        "evidence": row["source"],
        "entry_date": row["entry_date"],
    })
    store = current_store()
    store.put(fact)
    store.observe(fact.id, file=src.name, row=row["_line"],
                  column="amount_usd", digest=src.digest)
    return fact
