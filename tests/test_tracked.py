# SPDX-License-Identifier: Apache-2.0
"""The @tracked decorator: computing and recording must be one act.

The claim these tests defend is that you cannot get a number out of a tracked
transform without also getting its provenance -- not by convention, but because
there is no code path that produces one without the other.
"""

from decimal import Decimal

import pytest

from whence.core import Fact, Store, active_store, source_fact
from whence.tracked import REGISTRY, replay, tracked


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / ".whence").init()


@tracked("test_add", commutative=True, variadic=True)
def add(amounts):
    return sum(amounts, Decimal("0"))


@tracked("test_scale")
def scale(amount, factor):
    """Order matters here only in the story, not the arithmetic -- which is
    exactly the case the `operands` metadata exists to disambiguate."""
    return amount * factor


def src(store, value, key):
    fact = source_fact(value, {"key": key})
    store.put(fact)
    return fact


# ---------------------------------------------------------------------------

def test_a_transform_returns_a_fact_not_a_number(store):
    with active_store(store):
        result = add([src(store, "10.00", "a"), src(store, "5.00", "b")])
    assert isinstance(result, Fact)
    assert result.value == "15.00"
    assert result.kind == "derived"


def test_the_result_is_in_the_store_without_being_put_there(store):
    """The pipeline never calls store.put on a derived value. If provenance
    were a separate step, this is the test that would fail."""
    with active_store(store):
        result = add([src(store, "1.00", "a"), src(store, "2.00", "b")])
    assert store.get(result.id) == result


def test_a_transform_records_its_parents(store):
    with active_store(store):
        a, b = src(store, "10.00", "a"), src(store, "5.00", "b")
        result = add([a, b])
    assert set(result.inputs) == {a.id, b.id}


def test_a_transform_refuses_a_bare_number(store):
    """A number with no history cannot enter the graph by accident."""
    with active_store(store):
        with pytest.raises(TypeError, match="takes Facts"):
            add([src(store, "10.00", "a"), Decimal("5.00")])


def test_computing_outside_a_store_is_an_error(store):
    a = source_fact("1.00", {"key": "a"})
    with pytest.raises(RuntimeError, match="no active Whence store"):
        add([a, a])


def test_rounding_happens_when_the_fact_is_minted(store):
    """The rounding is in the record and therefore in the hash -- it is not a
    display choice made downstream."""
    with active_store(store):
        result = scale(src(store, "10.00", "a"), src(store, "0.3333", "r"))
    assert result.value == "3.33"


def test_commutative_transforms_ignore_argument_order(store):
    with active_store(store):
        a, b = src(store, "10.00", "a"), src(store, "5.00", "b")
        assert add([a, b]).id == add([b, a]).id


def test_order_sensitive_transforms_do_not(store):
    """`scale` is declared non-commutative, so the wrapper writes the operand
    order into hashed metadata and the two spellings separate."""
    with active_store(store):
        a, b = src(store, "10.00", "a"), src(store, "5.00", "b")
        forward, backward = scale(a, b), scale(b, a)
    assert forward.value == backward.value == "50.00"
    assert forward.id != backward.id
    assert forward.metadata["operands"] == "%s,%s" % (a.id, b.id)


def test_transforms_register_themselves_for_later_verification(store):
    assert "test_add" in REGISTRY
    assert REGISTRY["test_add"].commutative is True
    assert REGISTRY["test_scale"].commutative is False


def test_replay_reruns_the_same_function_the_pipeline_used(store):
    with active_store(store):
        result = add([src(store, "10.00", "a"), src(store, "5.00", "b")])
    assert replay(result, store) == "15.00"


def test_replay_respects_recorded_operand_order(store):
    with active_store(store):
        result = scale(src(store, "12.00", "a"), src(store, "0.25", "r"))
    assert replay(result, store) == "3.00"


def test_replay_declines_an_unknown_transform(store):
    """A transform this build does not know must read as "cannot verify",
    never as "verified"."""
    a = source_fact("1.00", {"key": "a"})
    store.put(a)
    alien = Fact(value="99.00", kind="derived", transform="from_the_future",
                 inputs=(a.id,), metadata={"key": "x"})
    store.put(alien)
    assert replay(alien, store) is None
