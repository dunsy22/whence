# SPDX-License-Identifier: Apache-2.0
"""Reconciling a warehouse.

The claim under test is narrow and worth stating exactly: Whence does not read
the warehouse's answer and believe it. It reads the warehouse's *source rows*,
recomputes the figure through the same transforms the CSV close uses, and then
compares. Agreement is a result, not an assumption.

The sharpest test in here is test_the_warehouse_close_mints_the_published_id:
the same quarter, computed from a database rather than from files, lands on the
identical fact id. That is the whole premise of content addressing holding
across a system boundary rather than only within one program.
"""

from datetime import date, datetime
from decimal import Decimal

import pytest

from whence.attest import build_statement, verify_closure
from whence.core import CanonicalizationError, Store
from whence.pipeline import DATA
from whence.warehouse import (
    Reconciliation, WarehouseTable, cell_text, load_mapping, read_figure,
    read_table, reconcile, result_digest,
)

duckdb = pytest.importorskip("duckdb", reason="warehouse extra not installed")


# ---------------------------------------------------------------------------
# A warehouse, built the way the dbt staging models build one.
#
# Reproduced here rather than invoking dbt: the adapter is what is under test,
# and a test that needed a dbt run to say anything would be testing dbt. The
# casts below are the staging models' casts, and they matter -- see
# test_the_warehouse_close_mints_the_published_id for why.
# ---------------------------------------------------------------------------

def _csv(name):
    return "read_csv('%s', header = true, all_varchar = true)" % (
        (DATA / name).as_posix())


@pytest.fixture(scope="module")
def conn():
    db = duckdb.connect()
    db.execute("""
        create table stg_orders as select
            order_id, cast(order_date as date) as order_date, sku,
            cast(qty as integer) as qty, region, currency,
            cast(unit_price_at_sale as decimal(18,2)) as unit_price_at_sale
        from %s""" % _csv("erp_orders.csv"))
    db.execute("""
        create table stg_pricebook as select
            sku, cast(list_price as decimal(18,2)) as list_price, currency,
            cast(effective_date as date) as effective_date, last_modified_by,
            -- DuckDB reads an empty CSV field as NULL where Python's csv
            -- module yields ''. They are different claims (SPEC.md 3.5,
            -- vector B), so the staging model normalizes rather than letting
            -- the two systems disagree about a blank approval.
            coalesce(approval_ref, '') as approval_ref
        from %s""" % _csv("pricebook_v3.csv"))
    db.execute("""
        create table stg_fx_rates as select
            cast(rate_date as date) as rate_date, currency,
            -- Four places, as the rate file states them. A wider scale would
            -- be arithmetically identical and provenantially different.
            cast(usd_per_unit as decimal(18,4)) as usd_per_unit
        from %s""" % _csv("fx_rates.csv"))
    db.execute("""
        create table stg_adjustments as select
            adj_id, cast(entry_date as date) as entry_date,
            cast(amount_usd as decimal(18,2)) as amount_usd,
            reason, author, source
        from %s""" % _csv("adjustments.csv"))
    db.execute("""
        create table fct_revenue_summary as select
            cast('4825082.47' as decimal(24,2)) as gross_revenue_usd,
            cast('4892582.47' as decimal(24,2)) as net_revenue_usd""")
    db.execute("""
        create table fct_region_revenue as select * from (values
            ('AMER', cast('2436713.77' as decimal(24,2))),
            ('EMEA', cast('1424942.90' as decimal(24,2))),
            ('APAC', cast('459849.71'  as decimal(24,2))),
            ('LATAM',cast('503576.09'  as decimal(24,2))))
        as t(region, region_revenue_usd)""")
    return db


MAPPING = {
    "warehouse": "duckdb:memory",
    "sources": {
        "orders": {"table": "stg_orders", "key": ["order_id"]},
        "pricebook": {"table": "stg_pricebook", "key": ["sku"]},
        "fx": {"table": "stg_fx_rates", "key": ["rate_date", "currency"]},
        "adjustments": {"table": "stg_adjustments", "key": ["adj_id"]},
    },
    "figures": [
        {"key": "net:q3", "label": "Q3 Net Revenue",
         "model": "fct_revenue_summary", "column": "net_revenue_usd"},
        {"key": "region:AMER", "label": "AMER subtotal",
         "model": "fct_region_revenue", "column": "region_revenue_usd",
         "where": {"region": "AMER"}},
    ],
}


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / ".whence").init()


# ---------------------------------------------------------------------------
# Cells.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    (Decimal("1450.00"), "1450.00"),
    (Decimal("1.0934"), "1.0934"),
    (Decimal("1E+2"), "100"),                 # never scientific notation
    (date(2026, 9, 30), "2026-09-30"),
    (datetime(2026, 9, 30, 12, 0), "2026-09-30T12:00:00"),
    (13, "13"),
    (True, "true"),
    (False, "false"),
    ("", ""),
    ("SKU-4471", "SKU-4471"),
])
def test_cells_have_one_text_form(value, expected):
    assert cell_text(value) == expected


def test_null_is_not_an_empty_string():
    """Collapsing them would let a missing value mint a Fact asserting a blank
    one, and a blank approval reference is a claim (SPEC.md 3.5, vector B)."""
    assert cell_text(None) is None
    assert cell_text("") == ""


def test_a_float_is_refused():
    with pytest.raises(CanonicalizationError):
        cell_text(0.1)


# ---------------------------------------------------------------------------
# The result digest: what replaces hashing a file's bytes.
# ---------------------------------------------------------------------------

ROWS = [{"sku": "A", "price": Decimal("1.00")},
        {"sku": "B", "price": Decimal("2.00")}]


def test_row_order_does_not_change_the_digest():
    """SQL without ORDER BY promises nothing about order, so a digest that
    moved with the engine's scheduling would pin nothing at all."""
    assert result_digest(["sku", "price"], ROWS) == \
           result_digest(["sku", "price"], list(reversed(ROWS)))


def test_column_order_does_not_change_the_digest():
    assert result_digest(["sku", "price"], ROWS) == \
           result_digest(["price", "sku"], ROWS)


def test_a_changed_cell_changes_the_digest():
    moved = [dict(ROWS[0]), {"sku": "B", "price": Decimal("2.01")}]
    assert result_digest(["sku", "price"], ROWS) != \
           result_digest(["sku", "price"], moved)


def test_a_dropped_row_changes_the_digest():
    assert result_digest(["sku", "price"], ROWS) != \
           result_digest(["sku", "price"], ROWS[:1])


def test_a_narrower_projection_changes_the_digest():
    """The digest pins what was read, and reading fewer columns is reading
    something else."""
    assert result_digest(["sku", "price"], ROWS) != \
           result_digest(["sku"], ROWS)


# ---------------------------------------------------------------------------
# Reading.
# ---------------------------------------------------------------------------

def test_a_table_reads_as_text_with_its_coordinates(conn):
    table = read_table(conn, "stg_pricebook", key=["sku"], warehouse="duckdb:x")
    row = table.where(sku="SKU-4471")

    assert row["list_price"] == "1200.00"
    assert row["approval_ref"] == ""          # normalized, not NULL

    where = table.coordinates(row, "list_price")
    assert where["table"] == "stg_pricebook"
    assert where["warehouse"] == "duckdb:x"
    assert where["row"] == "sku=SKU-4471"     # the key, not a line number
    assert where["digest"] == table.digest


def test_coordinates_name_the_key_not_a_position():
    """A physical row number is not stable across a reload; the key is."""
    table = WarehouseTable(name="t", warehouse="w", digest="d", rows=[],
                           key=("rate_date", "currency"))
    assert table.row_key({"rate_date": "2026-09-30", "currency": "EUR"}) \
        == "rate_date=2026-09-30,currency=EUR"


def test_identifiers_are_checked_before_they_reach_sql(conn):
    with pytest.raises(ValueError):
        read_figure(conn, "fct_revenue_summary; drop table stg_orders",
                    "net_revenue_usd")
    with pytest.raises(ValueError):
        read_table(conn, "stg_orders where 1=1", key=["order_id"])
    # and the table is still there
    assert read_figure(conn, "fct_revenue_summary", "net_revenue_usd")


def test_a_figure_must_be_exactly_one_number(conn):
    with pytest.raises(ValueError):
        read_figure(conn, "fct_region_revenue", "region_revenue_usd")


# ---------------------------------------------------------------------------
# Reconciliation.
# ---------------------------------------------------------------------------

def test_the_warehouse_close_mints_the_published_id(store, conn):
    """The same quarter, out of a database instead of four files, lands on the
    same id. Content addressing is not a property of this program; it is a
    property of the claims, and this is where that stops being a slogan."""
    report = reconcile(store, conn, MAPPING)
    assert report.root.startswith("b0d8cc3b3885")


def test_agreement_is_computed_not_assumed(store, conn):
    report = reconcile(store, conn, MAPPING)
    assert report.agrees
    assert not report.divergent
    for figure in report.figures:
        assert figure.found and figure.delta == 0


def test_a_divergence_is_caught_and_attributed(store, conn):
    conn.execute("update fct_revenue_summary set net_revenue_usd = "
                 "cast('4892582.45' as decimal(24,2))")
    try:
        report = reconcile(store, conn, MAPPING)
        assert not report.agrees
        off = report.divergent
        assert [f.key for f in off] == ["net:q3"]
        assert off[0].delta == Decimal("-0.02")
        # The figure that did not move is still reported as sound.
        assert [f.key for f in report.figures if f.agrees] == ["region:AMER"]
    finally:
        conn.execute("update fct_revenue_summary set net_revenue_usd = "
                     "cast('4892582.47' as decimal(24,2))")


def test_scale_is_not_a_disagreement(store, conn):
    """A warehouse column's scale is a storage decision. Only a difference in
    the number is a difference."""
    entry = Reconciliation(key="k", label="l", whence_value="4892582.47",
                           warehouse_value="4892582.47000000")
    assert entry.agrees and entry.delta == 0


def test_the_recomputed_figure_verifies_on_its_own(store, conn):
    """Reconciliation does not replace verification. The DAG Whence built from
    warehouse rows is a real DAG and has to stand up by itself."""
    report = reconcile(store, conn, MAPPING)
    check = verify_closure(store, report.root)
    assert check.ok and check.complete
    assert check.recomputed > 200


def test_the_comparison_is_recorded_as_a_run_not_a_fact(store, conn):
    """Two systems agreeing at one moment is a property of a run, like a
    clock, and belongs in the journal rather than in a hash."""
    report = reconcile(store, conn, MAPPING)
    runs = [r for r in store.all_runs() if r.get("kind") == "reconciliation"]
    assert len(runs) == 1
    assert runs[0]["root"] == report.root
    assert runs[0]["agrees"] is True
    assert {e["figure"] for e in runs[0]["reconciliation"]} == {"net:q3",
                                                               "region:AMER"}
    # and nothing about it leaked into the figure itself
    assert "agrees" not in store.get(report.root).metadata


def test_a_bundle_carries_the_reconciliation(store, conn):
    """An auditor opening the .wbundle should see not only that the arithmetic
    holds but that a second system produced the same number."""
    report = reconcile(store, conn, MAPPING)
    statement = build_statement(store, report.root)
    runs = statement["predicate"]["runs"]
    assert any(r.get("kind") == "reconciliation" and r.get("agrees")
               for r in runs)


def test_source_digests_are_recorded_per_table(store, conn):
    report = reconcile(store, conn, MAPPING)
    assert set(report.sources) == {"stg_orders", "stg_pricebook",
                                   "stg_fx_rates", "stg_adjustments"}
    assert all(len(d) == 64 for d in report.sources.values())


def test_explain_reports_agreement_as_corroboration_not_as_a_finding(store, conn):
    from whence.explain import explain
    report = reconcile(store, conn, MAPPING)
    exp = explain(store, report.root)
    assert any("matched" in note for note in exp.corroboration)
    assert not any("matched" in f for f in exp.findings)


# ---------------------------------------------------------------------------
# The mapping.
# ---------------------------------------------------------------------------

def test_a_mapping_missing_a_source_is_refused(tmp_path):
    import json
    path = tmp_path / "m.json"
    broken = {"sources": {"orders": {"table": "t"}}, "figures": []}
    path.write_text(json.dumps(broken), encoding="utf-8")
    with pytest.raises(ValueError, match="pricebook"):
        load_mapping(path)


def test_the_shipped_mapping_is_usable():
    from pathlib import Path
    shipped = Path(__file__).resolve().parent.parent / "warehouse" / "mapping.json"
    if not shipped.exists():                  # the prototype is optional
        pytest.skip("warehouse/mapping.json not present")
    mapping = load_mapping(shipped)
    assert mapping["sources"]["orders"]["table"] == "stg_orders"
    assert any(f["key"] == "net:q3" for f in mapping["figures"])
