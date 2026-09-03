# SPDX-License-Identifier: Apache-2.0
"""SPEC.md is executable.

The conformance vectors in section 3.5 are parsed out of the document itself
and checked against this implementation. A spec that drifts from the code is
worse than no spec, because people write verifiers against it -- so the
document cannot rot silently. If someone edits a vector, or changes the hash
construction without updating the vector, this fails.
"""

import hashlib
import json
import re
from pathlib import Path

import pytest

from whence.core import Fact, canonical_bytes

SPEC = Path(__file__).resolve().parent.parent / "SPEC.md"


def parse_vectors():
    """Pull out every (canonical json, expected id) pair the spec publishes.

    Each vector is written as a fenced block holding the canonical document,
    followed by a fenced block holding `id = <hex>`.
    """
    # Anchor both fences to the start of a line and allow an info string
    # (```json), so that a closing fence is never mistaken for an opening one.
    blocks = [b.strip() for b in
              re.findall(r"^```[a-z]*\n(.*?)^```", SPEC.read_text(encoding="utf-8"),
                         re.S | re.M)]
    vectors, pending = [], None
    for block in blocks:
        if block.startswith('{"_form"'):
            pending = block
        elif pending is not None and block.startswith("id = "):
            vectors.append((pending, block[len("id = "):].strip()))
            pending = None
    return vectors


VECTORS = parse_vectors()


def test_the_spec_publishes_the_vectors_it_claims_to():
    assert len(VECTORS) == 4, "expected four conformance vectors, got %d" % len(
        VECTORS)


@pytest.mark.parametrize("document,expected", VECTORS,
                         ids=[v[1][:12] for v in VECTORS])
def test_published_vector_hashes_to_its_published_id(document, expected):
    assert hashlib.sha256(document.encode("utf-8")).hexdigest() == expected


@pytest.mark.parametrize("document,expected", VECTORS,
                         ids=[v[1][:12] for v in VECTORS])
def test_this_implementation_reproduces_the_published_bytes(document, expected):
    """Not just the digest -- the exact serialization. If our separators, key
    order or escaping differed, we would still agree on nothing."""
    doc = json.loads(document)
    produced = canonical_bytes(doc["kind"], doc["value"], doc["transform"],
                               doc["inputs"], doc["metadata"])
    assert produced.decode("utf-8") == document

    fact = Fact(value=doc["value"], kind=doc["kind"],
                transform=doc["transform"], inputs=tuple(doc["inputs"]),
                metadata=doc["metadata"])
    assert fact.id == expected


def test_the_spec_names_the_fact_form_this_build_speaks():
    from whence import BUNDLE_PREDICATE, FACT_FORM
    text = SPEC.read_text(encoding="utf-8")
    assert FACT_FORM in text
    assert BUNDLE_PREDICATE in text


def test_the_spec_documents_every_transform_the_pipeline_uses():
    import whence.pipeline  # noqa: F401 - registers the transforms
    from whence.tracked import REGISTRY

    text = SPEC.read_text(encoding="utf-8")
    pipeline_transforms = {"multiply", "fx_convert", "sum_by_region",
                           "sum_regions", "apply_adjustments"}
    assert pipeline_transforms <= set(REGISTRY)
    for name in pipeline_transforms:
        assert "`%s`" % name in text, "%s is undocumented" % name
