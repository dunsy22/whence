# SPDX-License-Identifier: Apache-2.0
"""@tracked -- the decorator that makes computing and recording the same act.

This file is short on purpose. It is the entire technical argument.

A conventional lineage system has two moving parts: the code that computes the
number, and the code that logs what the number was computed from. They start
in agreement and drift apart, because nothing forces them to stay together.
Six months later the lineage graph is a description of a pipeline that no
longer exists.

Whence has one moving part. A tracked transform cannot return a bare number.
It takes Facts, and it returns a Fact -- the arithmetic and the provenance are
produced by the same call, from the same values, and there is no code path that
yields one without the other. If you want to know whether the lineage matches
the computation, the answer is structural: they are the same object.

    @tracked("multiply", commutative=True)
    def line_revenue(qty, unit_price):
        return qty * unit_price

    rev = line_revenue(qty_fact, price_fact, meta={"key": "line:ORD-1001"})
    rev.value   # '18450.00'
    rev.inputs  # (<qty id>, <price id>)

The inner function sees plain Decimals and knows nothing about hashing. The
wrapper does four things and then gets out of the way:

    1. pulls Decimals out of the input Facts
    2. calls the real arithmetic
    3. quantizes the result to cents (see core.money -- rounding is recorded)
    4. mints the Fact and appends it to the active store

Step 2 is also what `whence verify` re-runs, from the registry below, against
the stored input values. Verification therefore exercises the same function the
pipeline did, not a reimplementation of it.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import wraps
from typing import Callable, Dict, Iterable, Mapping

from .core import Fact, Store, current_store, dec, money

__all__ = ["tracked", "REGISTRY", "Transform", "replay"]


@dataclass(frozen=True)
class Transform:
    """A named operation, in the form `verify` needs to re-run it later."""
    name: str
    fn: Callable
    commutative: bool
    variadic: bool
    doc: str = ""


#: Every transform Whence knows how to perform -- and therefore every transform
#: it knows how to check. A bundle naming a transform that is not in here is
#: reported as unverifiable rather than quietly passed.
REGISTRY: Dict[str, Transform] = {}


def tracked(name: str, *, commutative: bool = False, variadic: bool = False):
    """Register `fn` as the transform `name` and fuse it to provenance.

    commutative=True says the operands are interchangeable: `a + b` and `b + a`
    are the same claim and must land on the same id. That is the default
    behaviour of the hash, which sorts inputs (see core.canonical_bytes), and
    it is what lets two analysts who iterate their spreadsheet rows in
    different orders arrive at byte-identical figures.

    commutative=False (the default, because it is the safe one) says order
    carries meaning -- a rate applied to an amount is not an amount applied to
    a rate. For those, the wrapper writes the operand ids into metadata under
    `operands`, in the caller's order. Since metadata is hashed, two different
    orderings of the same parents get two different ids, and `verify` can
    replay the operands the right way round.
    """

    def decorate(fn: Callable) -> Callable:
        REGISTRY[name] = Transform(
            name=name, fn=fn, commutative=commutative,
            variadic=variadic, doc=(fn.__doc__ or "").strip(),
        )

        @wraps(fn)
        def wrapper(*args, meta: Mapping[str, str] | None = None,
                    store: Store | None = None) -> Fact:
            facts = _collect(args, variadic)
            if not facts:
                raise ValueError("%s() needs at least one input fact" % name)
            for f in facts:
                if not isinstance(f, Fact):
                    raise TypeError(
                        "%s() takes Facts, not %s -- a tracked transform cannot "
                        "consume a number whose origin is unknown"
                        % (name, type(f).__name__)
                    )

            values = [dec(f.value) for f in facts]
            result = fn(values) if variadic else fn(*values)

            metadata = dict(meta or {})
            metadata.setdefault("label", name)
            if not commutative:
                metadata["operands"] = ",".join(f.id for f in facts)

            fact = Fact(
                value=money(result),
                kind="derived",
                transform=name,
                inputs=tuple(f.id for f in facts),
                metadata=metadata,
            )
            (store or current_store()).put(fact)
            return fact

        wrapper.transform_name = name
        return wrapper

    return decorate


def _collect(args, variadic: bool):
    """Variadic transforms may be called with a list or with loose arguments;
    both spellings are common in the pipeline and neither changes the result."""
    if variadic and len(args) == 1 and isinstance(args[0], Iterable) \
            and not isinstance(args[0], Fact):
        return list(args[0])
    return list(args)


def replay(fact: Fact, store: Store) -> str | None:
    """Recompute a derived Fact's value from its stored parents.

    Returns the recomputed money text, or None if this build does not know the
    transform (in which case the caller must report "cannot verify", never
    "verified"). This is the honest half of `whence verify`: re-hashing proves
    a record has not been edited, but only replay proves the number is the
    number that logic actually produces from those inputs.
    """
    t = REGISTRY.get(fact.transform)
    if t is None:
        return None
    ids = _operand_order(fact)
    values = [dec(store.get(i).value) for i in ids]
    result = t.fn(values) if t.variadic else t.fn(*values)
    return money(result)


def _operand_order(fact: Fact) -> list:
    """The order to feed parents back into the transform.

    For order-sensitive transforms this comes from the hashed `operands`
    metadata, not from the record's `inputs` list -- so replay is correct even
    if the list were reordered on disk, and a reordering that changed meaning
    would already have changed the id.
    """
    operands = fact.metadata.get("operands")
    if operands:
        return operands.split(",")
    return list(fact.inputs)
