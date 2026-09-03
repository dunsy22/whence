<!-- SPDX-License-Identifier: Apache-2.0 -->

# Contributing

Whence is a proof of concept. Most of it is deliberately provisional; a small
part of it is not. Knowing which is which before you start will save you time.

## What is stable

Changes to these require a strong argument, and a change that alters any
existing identifier requires a new `_form` string (see `SPEC.md` §8).

- **`SPEC.md` itself.** The document is the artifact; the Python is one
  implementation of it. If the code and the spec disagree, the spec is right
  and the code is broken.
- **The hash construction** — canonical serialization, the sorted-inputs rule,
  the strings-only rule. `whence/core.py`.
- **The `.wbundle` format** — the DSSE envelope, the in-toto Statement, and the
  predicate shape.
- **The verification algorithm** in `SPEC.md` §6.5, and its implementation in
  `whence/attest.py`.

The four conformance vectors in `SPEC.md` §3.5 are parsed out of the document
and checked by `tests/test_spec.py`. If you change the hash construction, that
test will fail, and it is supposed to.

## What is explicitly experimental

Everything else. Change it freely:

- the pipeline and its transforms (`whence/pipeline.py`)
- narrative rendering and the `diff` attribution heuristics
- the CLI, its output, and the demo script
- the HTML report
- the store layout on disk — `SPEC.md` §5 is informative, not normative

## Ground rules

**Comprehension over completeness.** A reviewer should be able to read the
whole codebase in about half an hour. A feature that makes the system more
capable and the argument harder to follow is a bad trade here.

**Nothing printed may be hardcoded.** Every figure, count and identifier in the
demo, the terminal output and the report is computed at the moment it is shown.
A demo of a provenance system that fakes its own output has argued against
itself.

**Say what you did not check.** An asserted value is never reported as
verified. An unknown transform is never passed. A partial check says it is
partial. Overstating what verification proved is the one failure mode this
project cannot afford.

**No placeholder tiers.** Nothing is stubbed out or gated behind a future
commercial offering. If it is in the repository, it works.

## Working on it

```bash
pip install -e ".[dev]"
python -m pytest            # 99 tests, about 9 seconds
python -m whence demo --no-pause
```

New code needs tests. For anything touching the hash, the store, or the bundle
format, that means a test that tries to *break* it — the existing suite has
about a dozen that attempt to make verification pass when it should fail, and
that ratio is the right one.

If you regenerate the fixtures with `python data/_generate.py`, every
identifier in the README transcript and in `SPEC.md` §3.5 changes. Regenerate
those too, or don't regenerate the fixtures.

## Licensing

Apache License 2.0. Put the SPDX identifier at the top of every new source
file:

```
# SPDX-License-Identifier: Apache-2.0
```

By contributing you agree your contributions are licensed under the same terms.
