# SPDX-License-Identifier: Apache-2.0
"""The spreadsheet adapter.

The fixture workbooks in data/ are built by data/_workbook.py from the same
CSVs the close reads. Small purpose-built workbooks are written here with
XlsxWriter for the cases the fixtures deliberately do not contain.
"""

import re
import zipfile
from datetime import datetime
from decimal import Decimal

import pytest

openpyxl = pytest.importorskip("openpyxl")
xlsxwriter = pytest.importorskip("xlsxwriter")

from whence.attest import export_bundle, verify_bundle, verify_local  # noqa: E402
from whence.core import Store  # noqa: E402
from whence.diff import diff  # noqa: E402
from whence.formula import Unsupported, normalize, parse  # noqa: E402
from whence.pipeline import DATA, load, run_close  # noqa: E402
from whence.workbook import (Book, cell_text, ingest_workbook,  # noqa: E402
                             number_scale, number_text, read_table)


@pytest.fixture(scope="module")
def wb(tmp_path_factory):
    store = Store(tmp_path_factory.mktemp("wb") / ".whence").init()
    csv_close = run_close(store, label="Q3 close")
    first = ingest_workbook(store, DATA / "q3_close.xlsx")
    second = ingest_workbook(store, DATA / "q3_close_v2.xlsx")
    return store, csv_close, first, second


def _tiny(path, cells, formats=None, date_1904=False):
    """Write a one-sheet workbook. `cells` maps A1 -> value, or -> (formula,
    cached result)."""
    book = xlsxwriter.Workbook(str(path), {"date_1904": date_1904})
    ws = book.add_worksheet("Sheet1")
    fmts = {k: book.add_format({"num_format": v})
            for k, v in (formats or {}).items()}
    for ref, value in cells.items():
        fmt = fmts.get(ref)
        if isinstance(value, tuple):
            ws.write_formula(ref, value[0], fmt, value[1])
        elif isinstance(value, datetime):
            ws.write_datetime(ref, value, fmt)
        else:
            ws.write(ref, value, fmt)
    book.close()
    return path


# ---------------------------------------------------------------------------
# Values out of cells.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fmt, scale", [
    ("General", None), (None, None), ("@", None), ("0", 0), ("#,##0", 0),
    ("0.00", 2), ("#,##0.00", 2), ("0.0000", 4), ("0.00%", 4),
    ('"$"#,##0.00;[Red]-"$"#,##0.00', 2), ("0.00E+00", None),
])
def test_number_format_declares_the_scale(fmt, scale):
    assert number_scale(fmt) == scale


@pytest.mark.parametrize("value, fmt, text", [
    (1200.0, "#,##0.00", "1200.00"),        # the CSV's spelling, from a double
    (1.0, "0.0000", "1.0000"),
    (13, "0", "13"),
    (0.1, "General", "0.1"),                # shortest round-trip, never 0.1000...
    (1234.5, "General", "1234.5"),
    (12.0, "General", "12"),
    (-0.004, "0.00", "0.00"),               # no signed zero
    (2.675, "0.00", "2.68"),                # the decimal 2.675, rounded half-up
])
def test_number_text(value, fmt, text):
    assert number_text(value, fmt) == text


def test_cell_text_dates_and_blanks():
    assert cell_text(datetime(2026, 9, 30)) == "2026-09-30"
    assert cell_text(datetime(2026, 9, 30, 14, 5, 7)) == "2026-09-30T14:05:07"
    assert cell_text(None) == ""
    assert cell_text(True) == "TRUE"


def test_1904_date_system(tmp_path):
    """A workbook from the old Mac epoch stores 2026-09-30 as a different
    serial number. The date that comes out must be the same."""
    for system in (False, True):
        path = _tiny(tmp_path / ("d%d.xlsx" % system),
                     {"A1": "rate_date", "A2": datetime(2026, 9, 30)},
                     formats={"A2": "yyyy-mm-dd"}, date_1904=system)
        table = read_table(Book(path), "Sheet1")
        assert table.rows[0]["rate_date"] == "2026-09-30"


# ---------------------------------------------------------------------------
# Formulas.
# ---------------------------------------------------------------------------

def test_copied_down_formulas_normalize_identically():
    a = normalize("=ROUND(D2*E2,2)", 2, 6)
    b = normalize("=ROUND(D57*E57,2)", 57, 6)
    assert a == b == "=ROUND(R[0]C[-2]*R[0]C[-1],2)"
    assert normalize("=$B$2*C2", 2, 4) != normalize("=$B$3*C3", 3, 4)


def test_unsupported_formulas_are_named():
    for formula in ("=A1^2", '=A1&"x"', "=SUM(A:A)", "=[1]Sheet1!A1"):
        with pytest.raises(Unsupported):
            parse(formula)


# ---------------------------------------------------------------------------
# The fixture workbook.
# ---------------------------------------------------------------------------

def test_pricebook_tab_mints_the_same_facts_as_the_csv(wb):
    """The headline of the phase: identity survives the trip into Excel."""
    store, csv_close, first, _ = wb
    prices_csv = load(DATA / "pricebook_v3.csv")
    csv_ids = {f.key: f.id for f in store.closure(csv_close.net.id).values()
               if f.key.startswith("src:price:")}
    xl_ids = {f.key: f.id for f in store.closure(first.reference.net.id).values()
              if f.key.startswith("src:price:")}
    assert csv_ids and csv_ids == xl_ids
    assert len(prices_csv.rows) == 47


def test_reference_close_over_the_tabs_is_the_csv_close(wb):
    store, csv_close, first, _ = wb
    assert first.reference.net.id == csv_close.net.id


def test_copied_down_column_carries_one_formula(wb):
    store, _, first, _ = wb
    lines = [f for f in store.closure(first.headline.id).values()
             if f.key.startswith("xl:Revenue:") and f.key.endswith(".line_usd")]
    assert len(lines) == 200
    assert len({f.metadata["xl.formula"] for f in lines}) == 1


def test_typed_over_formula_is_found_and_costed(wb):
    store, csv_close, first, _ = wb
    assert [f.cell for f in first.overrides] == ["Revenue!D12"]
    found = first.overrides[0].detail
    assert (found["typed"], found["formula gives"]) == ("32175.00", "31800.00")
    assert first.override_effect == Decimal("375.00")
    # With the formula restored, the workbook is the close, to the cent.
    assert first.restored_value == csv_close.net.value
    assert first.reference_delta == Decimal("375.00")


def test_typed_number_is_asserted_not_derived(wb):
    store, _, first, _ = wb
    typed = [f for f in store.closure(first.headline.id).values()
             if f.kind == "asserted" and f.key.startswith("xl:")]
    assert len(typed) == 1
    assert typed[0].value == "32175.00"
    assert typed[0].metadata["actor"] == "d.fairbanks"


def test_no_other_findings_on_a_clean_save(wb):
    _, _, first, second = wb
    assert {f.kind for f in first.findings} == {"override"}
    assert {f.kind for f in second.findings} == {"override"}


def test_workbook_figure_verifies(wb):
    store, _, first, _ = wb
    report = verify_local(store, first.headline.id)
    assert report.ok and report.complete
    assert len(report.asserted) == 3          # two journal entries, one typed cell


def test_workbook_bundle_verifies_offline(wb, tmp_path):
    store, _, first, _ = wb
    path = tmp_path / "workbook.wbundle"
    export_bundle(store, first.headline.id, path)
    report = verify_bundle(path)
    assert report.ok and report.complete
    assert report.source_files.get("q3_close.xlsx") == first.digest


def test_workbook_diff_names_the_repriced_skus(wb):
    store, _, first, second = wb
    report = diff(store, first.headline.id, second.headline.id)
    assert report.delta == Decimal("43840.00")
    assert report.reconciles
    assert {g.bucket for g in report.groups if g.kind == "price"} == {
        "SKU-4471", "SKU-4472", "SKU-5013"}


# ---------------------------------------------------------------------------
# Variants of the fixture.
# ---------------------------------------------------------------------------

def test_restoring_the_formula_removes_the_finding(tmp_path):
    """Put the formula back with openpyxl. openpyxl does not keep cached
    results when it saves, so this also exercises a workbook Excel never
    calculated: Whence computes every value itself and says so."""
    path = tmp_path / "fixed.xlsx"
    book = openpyxl.load_workbook(DATA / "q3_close.xlsx")
    book["Revenue"]["D12"] = \
        "=Orders!D12*VLOOKUP(Orders!C12,Pricebook!$A$2:$B$48,2,FALSE)"
    book.save(path)

    store = Store(tmp_path / ".whence").init()
    result = ingest_workbook(store, path)
    assert not result.overrides
    assert {f.kind for f in result.findings} == {"uncached"}
    assert result.reference_delta == 0
    assert verify_local(store, result.headline.id).ok


def test_edited_cached_value_is_caught(tmp_path):
    """Change the saved result of Revenue!F2 inside the file, leaving its
    formula alone -- what a stale save or a hand-edited XML looks like."""
    src, path = DATA / "q3_close.xlsx", tmp_path / "edited.xlsx"
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(path, "w") as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == "xl/worksheets/sheet2.xml":       # Revenue
                text = data.decode("utf-8")
                cell = re.search(r'<c r="F2"[^>]*>.*?</c>', text).group(0)
                edited = re.sub(r"<v>[^<]*</v>", "<v>9954.95</v>", cell)
                data = text.replace(cell, edited).encode("utf-8")
            zout.writestr(item, data)

    store = Store(tmp_path / ".whence").init()
    result = ingest_workbook(store, path)
    assert "Revenue!F2" in {f.cell for f in result.cache_mismatches}

    report = verify_local(store, result.headline.id)
    assert not report.ok
    assert any("9954.95" in failure for failure in report.failures)


def test_unsupported_function_becomes_opaque(tmp_path):
    path = _tiny(tmp_path / "avg.xlsx", {
        "A1": "amount", "A2": 100, "A3": 300,
        "B1": "avg", "B2": ("=AVERAGE(A2:A3)", 200),
    })
    store = Store(tmp_path / ".whence").init()
    result = ingest_workbook(store, path, cell="Sheet1!B2")
    assert [f.kind for f in result.findings] == ["opaque"]
    head = result.headline
    assert head.transform == "xl.opaque" and len(head.inputs) == 2

    report = verify_local(store, head.id)
    assert not report.complete
    assert report.unknown_transforms == ["xl.opaque"]


def test_constant_in_a_formula_is_asserted(tmp_path):
    path = _tiny(tmp_path / "uplift.xlsx", {
        "A1": "base", "A2": 1000, "B1": "uplifted", "B2": ("=A2*1.08", 1080),
    }, formats={"A2": "0.00", "B2": "0.00"})
    store = Store(tmp_path / ".whence").init()
    result = ingest_workbook(store, path, cell="Sheet1!B2")
    assert result.headline.value == "1080.00"
    parents = [store.get(i) for i in result.headline.inputs]
    assert sorted(p.kind for p in parents) == ["asserted", "source"]
    assert verify_local(store, result.headline.id).ok


def test_unrounded_fraction_of_a_cent_is_not_rounded_silently(tmp_path):
    path = _tiny(tmp_path / "third.xlsx", {
        "A1": "x", "A2": 1, "B1": "y", "B2": ("=A2*0.333", 0.333),
    })
    store = Store(tmp_path / ".whence").init()
    result = ingest_workbook(store, path, cell="Sheet1!B2")
    assert [f.kind for f in result.findings] == ["opaque"]
    assert "not exact to the cent" in result.findings[0].message


def test_missing_headline_name_is_a_clear_error(tmp_path):
    path = _tiny(tmp_path / "plain.xlsx", {"A1": 1})
    store = Store(tmp_path / ".whence").init()
    with pytest.raises(ValueError, match="Headline"):
        ingest_workbook(store, path)
