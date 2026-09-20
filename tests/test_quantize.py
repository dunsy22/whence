# SPDX-License-Identifier: Apache-2.0
"""Declared quantization: rounding as a property of the transform.

Whence rounds when a Fact is minted, so the rounding is inside the hash rather
than being a display choice made later. Until now it always rounded to cents,
because `money()` ran unconditionally inside the @tracked wrapper. That is
right for an amount that gets invoiced and posted to a ledger, and wrong for
anything that is not money -- a margin, a ratio, an average.

So the scale is declared per transform and recorded in metadata, which means it
is hashed, which means a verifier can replay it without having to know which
version of which transform produced the figure.

The property these tests defend hardest is the first one: the default is left
out of metadata entirely, so every id minted before this parameter existed is
byte-identical afterwards.
"""

from decimal import Decimal

import pytest

from whence.attest import verify_closure
from whence.core import (
    CanonicalizationError, Fact, Store, active_store, money,
    quant_name, quant_scale, quantize_text, source_fact,
)
from whence.tracked import REGISTRY, replay, tracked


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / ".whence").init()


@tracked("q_add", commutative=True, variadic=True)
def q_add(amounts):
    return sum(amounts, Decimal("0"))


@tracked("q_add_explicit_cents", commutative=True, variadic=True,
         quantize="2dp")
def q_add_explicit(amounts):
    return sum(amounts, Decimal("0"))


@tracked("q_ratio", quantize="6dp")
def q_ratio(revenue, units):
    """Average selling price. Not money that anyone posts, so cent-rounding it
    would be throwing away real precision for no reason."""
    return revenue / units


def src(store, value, key):
    fact = source_fact(value, {"key": key})
    store.put(fact)
    return fact


# ---------------------------------------------------------------------------
# Backward compatibility. If any of these fail, the format broke.
# ---------------------------------------------------------------------------

def test_the_default_scale_is_not_recorded_in_metadata(store):
    with active_store(store):
        total = q_add([src(store, "10.00", "a"), src(store, "5.005", "b")])
    assert "quantize" not in total.metadata
    assert total.value == "15.01"


def test_cents_and_2dp_are_the_same_declaration(store):
    """Two spellings of one intent must not mint two different ids."""
    assert quant_scale("cents") == quant_scale("2dp") == 2
    with active_store(store):
        a = q_add([src(store, "1.00", "a"), src(store, "2.00", "b")])
        b = q_add_explicit([src(store, "1.00", "a"), src(store, "2.00", "b")])
    assert "quantize" not in b.metadata
    assert a.value == b.value


def test_the_reference_pipeline_still_mints_its_published_id(store):
    """The whole quarter, checked against the id in the README."""
    from whence.pipeline import run_close
    assert run_close(store, label="q").net.short == "b0d8cc3b3885"


def test_money_is_unchanged(store):
    assert money("2.675") == "2.68" == quantize_text("2.675", 2)


# ---------------------------------------------------------------------------
# The new behaviour.
# ---------------------------------------------------------------------------

def test_a_non_default_scale_is_recorded_and_kept(store):
    with active_store(store):
        avg = q_ratio(src(store, "1000.00", "rev"), src(store, "3", "units"))
    assert avg.metadata["quantize"] == "6dp"
    assert avg.value == "333.333333"


def test_scale_is_part_of_the_id(store):
    """Metadata is hashed, so the same arithmetic at two scales is two claims."""
    with active_store(store):
        avg = q_ratio(src(store, "1000.00", "rev"), src(store, "3", "u"))
    twin = Fact(value=avg.value, kind=avg.kind, transform=avg.transform,
                inputs=avg.inputs,
                metadata={k: v for k, v in avg.metadata.items()
                          if k != "quantize"})
    assert twin.id != avg.id


def test_the_registry_remembers_the_declared_scale():
    assert REGISTRY["q_ratio"].scale == 6
    assert REGISTRY["q_add"].scale == 2


def test_replay_honours_the_declared_scale(store):
    with active_store(store):
        avg = q_ratio(src(store, "1000.00", "rev"), src(store, "3", "u"))
    assert replay(avg, store) == avg.value


def test_replay_reads_the_scale_off_the_fact_not_the_registry(store):
    """A Fact records what was actually done. Re-declaring a transform's scale
    later must not retroactively invalidate figures minted before the change."""
    with active_store(store):
        rev, units = src(store, "1000.00", "rev"), src(store, "3", "u")
        avg = q_ratio(rev, units)
    older = Fact(value="333.3333", kind="derived", transform="q_ratio",
                 inputs=avg.inputs,
                 metadata={**avg.metadata, "quantize": "4dp"})
    store.put(older)
    assert replay(older, store) == "333.3333"


def test_a_caller_cannot_forge_the_scale_through_meta(store):
    """A `quantize` passed in by hand is discarded, not honoured -- otherwise
    the record would advertise a scale the value was never computed at."""
    with active_store(store):
        total = q_add([src(store, "1.005", "a")], meta={"quantize": "6dp"})
    assert "quantize" not in total.metadata
    assert total.value == "1.01"


# ---------------------------------------------------------------------------
# Rejection.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("spec", ["13dp", "exact", "full", "", "2 dp", "-1dp",
                                  "2DP", None, 2])
def test_unusable_scales_are_refused(spec):
    """There is deliberately no 'exact' option: an unquantized Decimal has no
    single text form, which is the one thing SPEC.md section 3.1 forbids."""
    with pytest.raises(CanonicalizationError):
        quant_scale(spec)


def test_a_bad_declaration_fails_at_decoration_not_at_runtime():
    with pytest.raises(CanonicalizationError):
        @tracked("q_never_registered", quantize="exact")
        def _never(a):
            return a
    assert "q_never_registered" not in REGISTRY


def test_a_malformed_declaration_is_a_failure_not_a_pass(store):
    """The record hashes correctly but says nothing actionable. It must not be
    counted as verified, which is the same rule an unknown transform follows."""
    with active_store(store):
        a, b = src(store, "1.00", "a"), src(store, "2.00", "b")
    broken = Fact(value="3.00", kind="derived", transform="q_add",
                  inputs=(a.id, b.id),
                  metadata={"label": "q_add", "quantize": "nonsense"})
    store.put(broken)

    report = verify_closure(store, broken.id)
    assert not report.ok
    assert report.recomputed == 0
    assert any("quantization" in f for f in report.failures)


# ---------------------------------------------------------------------------
# End to end.
# ---------------------------------------------------------------------------

def test_a_mixed_scale_graph_verifies(store):
    with active_store(store):
        rev = q_add([src(store, "600.00", "a"), src(store, "400.00", "b")])
        avg = q_ratio(rev, src(store, "3", "units"))

    report = verify_closure(store, avg.id)
    assert report.ok and report.complete
    assert report.recomputed == 2


def test_quant_name_round_trips():
    for scale in range(0, 13):
        assert quant_scale(quant_name(scale)) == scale
