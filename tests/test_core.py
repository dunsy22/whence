# SPDX-License-Identifier: Apache-2.0
"""Tests for the primitive everything else depends on.

If these pass, ids mean what SPEC.md says they mean. If any of them fails,
nothing further in this repository is trustworthy, so these run first.
"""

import json
import subprocess
import sys
from decimal import Decimal

import pytest

from whence.core import (
    CanonicalizationError, Fact, IntegrityError, Store,
    asserted_fact, canonical_bytes, compute_id, dec, money, source_fact,
)

# A frozen conformance vector. This exact byte string and this exact digest are
# reproduced in SPEC.md section 3.4; an independent implementation in another
# language is correct when it reproduces them. Changing either is a format
# break and must come with a bump to FACT_FORM.
VECTOR_FACT = Fact(
    value="1234.50",
    kind="source",
    transform="read",
    metadata={"key": "src:demo", "source.file": "data/x.csv", "source.row": "7"},
)
VECTOR_BYTES = (
    b'{"_form":"whence.fact.v1","inputs":[],"kind":"source","metadata":'
    b'{"key":"src:demo","source.file":"data/x.csv","source.row":"7"},'
    b'"transform":"read","value":"1234.50"}'
)
VECTOR_ID = "dea4dddac67098f0addee32682b8dda025560dc69905cfaa76598be8cd861401"


def mk(value="100.00", kind="derived", transform="sum", inputs=("a" * 64,), **meta):
    return Fact(value=value, kind=kind, transform=transform,
                inputs=tuple(inputs), metadata=meta)


# ---------------------------------------------------------------------------
# Rule 1: the id is a pure function of the content.
# ---------------------------------------------------------------------------

def test_canonical_bytes_match_the_published_vector():
    got = canonical_bytes(VECTOR_FACT.kind, VECTOR_FACT.value,
                          VECTOR_FACT.transform, VECTOR_FACT.inputs,
                          VECTOR_FACT.metadata)
    assert got == VECTOR_BYTES
    assert VECTOR_FACT.id == VECTOR_ID


def test_id_is_stable_across_processes():
    """Determinism that survives a fresh interpreter -- no ambient state, no
    hash randomization, no dict-ordering luck."""
    code = (
        "from whence.core import Fact;"
        "print(Fact(value='1234.50',kind='source',transform='read',"
        "metadata={'key':'src:demo','source.file':'data/x.csv',"
        "'source.row':'7'}).id)"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, check=True)
    assert out.stdout.strip() == VECTOR_ID


def test_same_content_built_twice_is_the_same_id():
    assert mk() .id == mk().id


@pytest.mark.parametrize("change", [
    {"value": "100.01"},
    {"kind": "asserted"},
    {"transform": "product"},
])
def test_changing_any_hashed_field_changes_the_id(change):
    base = mk()
    other = Fact(value=change.get("value", base.value),
                 kind=change.get("kind", base.kind),
                 transform=change.get("transform", base.transform),
                 inputs=() if change.get("kind") == "asserted" else base.inputs,
                 metadata=dict(base.metadata))
    assert other.id != base.id


def test_changing_metadata_changes_the_id():
    assert mk(actor="a.rivera").id != mk(actor="j.moreau").id


def test_adding_a_metadata_key_changes_the_id():
    assert mk(note="reviewed").id != mk().id


def test_changing_a_parent_changes_the_id():
    assert mk(inputs=("a" * 64,)).id != mk(inputs=("b" * 64,)).id


# ---------------------------------------------------------------------------
# Input ordering: the property that makes two analysts agree.
# ---------------------------------------------------------------------------

def test_parent_order_does_not_affect_identity():
    """Two people who sum the same set of lines in different orders are making
    the same claim, and get the same id. This is beat 6 of the demo."""
    a, b, c = ("1" * 64), ("2" * 64), ("3" * 64)
    assert mk(inputs=(a, b, c)).id == mk(inputs=(c, a, b)).id


def test_order_sensitive_transforms_distinguish_themselves_via_metadata():
    """Sorting inputs would make `a - b` and `b - a` collide, so transforms
    where order matters carry it in hashed metadata instead (tracked.py does
    this automatically for commutative=False)."""
    a, b = ("1" * 64), ("2" * 64)
    fwd = mk(inputs=(a, b), transform="fx_convert", operands=a + "," + b)
    rev = mk(inputs=(a, b), transform="fx_convert", operands=b + "," + a)
    assert fwd.id != rev.id


# ---------------------------------------------------------------------------
# Rules 3 and 4: what each kind of fact is allowed to be.
# ---------------------------------------------------------------------------

def test_source_facts_cannot_have_parents():
    with pytest.raises(CanonicalizationError):
        Fact(value="1.00", kind="source", transform="read",
             inputs=("a" * 64,)).id


def test_derived_facts_must_have_parents():
    with pytest.raises(CanonicalizationError):
        Fact(value="1.00", kind="derived", transform="sum").id


def test_asserted_facts_are_valid_and_permanently_marked():
    f = asserted_fact("80000.00", {
        "key": "adj:ADJ-001", "actor": "s.patel",
        "reason": "Q3 rebate accrual per CFO email",
    })
    assert f.kind == "asserted"
    assert f.inputs == ()
    # kind is hashed, so an asserted number cannot be relabelled as derived
    # without becoming a different number with a different id.
    assert b'"kind":"asserted"' in canonical_bytes(
        f.kind, f.value, f.transform, f.inputs, f.metadata)


def test_unknown_kinds_are_refused():
    with pytest.raises(CanonicalizationError):
        Fact(value="1.00", kind="estimated", transform="guess").id


# ---------------------------------------------------------------------------
# There are no floats in the hash. Ever.
# ---------------------------------------------------------------------------

def test_floats_are_refused_outright():
    with pytest.raises(CanonicalizationError):
        dec(0.1)
    with pytest.raises(CanonicalizationError):
        Fact(value=100.0, kind="asserted", transform="assert").id


def test_metadata_values_must_be_text():
    with pytest.raises(CanonicalizationError):
        mk(row=7).id


def test_metadata_keys_are_restricted_to_a_safe_ascii_shape():
    with pytest.raises(CanonicalizationError):
        Fact(value="1.00", kind="asserted", transform="assert",
             metadata={"Source File": "x"}).id


def test_money_rounds_half_up_on_exact_decimals():
    # 2.675 is the classic float trap: as a float it rounds to 2.67.
    assert money("2.675") == "2.68"
    assert money("1.005") == "1.01"
    assert money(Decimal("-0.001")) == "0.00"   # no signed zero in the record
    assert money("1000") == "1000.00"


# ---------------------------------------------------------------------------
# The store: append-only, deduplicating, tamper-evident on read.
# ---------------------------------------------------------------------------

def test_roundtrip_through_the_store(tmp_path):
    store = Store(tmp_path / ".whence").init()
    f = source_fact("42.00", {"key": "src:demo"})
    fid = store.put(f)
    assert store.get(fid) == f


def test_storing_the_same_fact_twice_writes_nothing_new(tmp_path):
    store = Store(tmp_path / ".whence").init()
    f = source_fact("42.00", {"key": "src:demo"})
    store.put(f)
    first = store.journal.read_text(encoding="utf-8")
    store.put(f)
    store.put(Fact(value="42.00", kind="source", transform="read",
                   metadata={"key": "src:demo"}))
    assert store.journal.read_text(encoding="utf-8") == first


def test_a_tampered_value_is_caught_on_read(tmp_path):
    """Edit a stored number by hand; the record no longer hashes to the name
    it is filed under, and the store refuses to hand it back."""
    store = Store(tmp_path / ".whence").init()
    fid = store.put(source_fact("42.00", {"key": "src:demo"}))
    path = store._path(fid)
    rec = json.loads(path.read_text(encoding="utf-8"))
    rec["value"] = "4200.00"
    path.write_text(json.dumps(rec), encoding="utf-8")

    fresh = Store(store.root)          # bypass the in-memory cache
    with pytest.raises(IntegrityError):
        fresh.get(fid)


def test_a_tampered_metadata_field_is_caught_on_read(tmp_path):
    store = Store(tmp_path / ".whence").init()
    fid = store.put(source_fact("42.00", {"key": "src:demo", "actor": "m.jensen"}))
    path = store._path(fid)
    rec = json.loads(path.read_text(encoding="utf-8"))
    rec["metadata"]["actor"] = "someone.else"
    path.write_text(json.dumps(rec), encoding="utf-8")
    with pytest.raises(IntegrityError):
        Store(store.root).get(fid)


def test_relabelling_the_id_does_not_help_the_forger(tmp_path):
    """Rewriting the id to match the edited contents just moves the problem:
    the record is now filed under the wrong name."""
    store = Store(tmp_path / ".whence").init()
    fid = store.put(source_fact("42.00", {"key": "src:demo"}))
    path = store._path(fid)
    forged = Fact(value="4200.00", kind="source", transform="read",
                  metadata={"key": "src:demo"})
    path.write_text(json.dumps(forged.to_record()), encoding="utf-8")
    with pytest.raises(IntegrityError):
        Store(store.root).get(fid)


def test_closure_collects_the_whole_subgraph(tmp_path):
    store = Store(tmp_path / ".whence").init()
    a = source_fact("10.00", {"key": "src:a"})
    b = source_fact("20.00", {"key": "src:b"})
    store.put(a)
    store.put(b)
    total = Fact(value="30.00", kind="derived", transform="sum",
                 inputs=(a.id, b.id), metadata={"key": "total"})
    store.put(total)
    assert set(store.closure(total.id)) == {a.id, b.id, total.id}


def test_ids_can_be_resolved_from_a_short_prefix(tmp_path):
    store = Store(tmp_path / ".whence").init()
    fid = store.put(source_fact("42.00", {"key": "src:demo"}))
    assert store.resolve(fid[:12]) == fid
    assert store.resolve("whence:" + fid[:12] + "…") == fid
    with pytest.raises(KeyError):
        store.resolve("ffff")
