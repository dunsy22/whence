# SPDX-License-Identifier: Apache-2.0
"""Whence core -- the Fact record, its identifier, and the append-only store.

Read this file first. Everything else in Whence is downstream of the handful of
lines below that turn "a value and where it came from" into a single id.

The trust model, in three sentences:

  1. A Fact's id is the SHA-256 of a canonical, unambiguous serialization of
     everything the Fact asserts: its value, how it was produced, and which
     other Facts it was produced from.
  2. So two people who start from the same sources and apply the same logic
     arrive at the same id without coordinating -- and anyone who alters any
     part of a Fact gets a different id, so the alteration cannot hide.
  3. Nothing here is secret and nothing here is signed. Verification is just
     recomputation: read the bytes, hash them, compare. You do not have to
     trust this program. You have to trust SHA-256 and be able to read SPEC.md.

Two design decisions are worth flagging up front, because they are the ones a
reviewer should push on:

  * Every hashed value is a *string*. No floats, no JSON numbers, anywhere.
    Money is decimal text quantized to cents at the moment a Fact is minted.
    This removes the entire class of "your machine hashed 0.1 differently than
    mine" failures, at the cost of making the caller state its rounding.

  * Wall-clock timestamps are NOT part of a Fact. If they were, rule 1 would be
    false: the same computation on the same inputs would get a new id every
    run, and content-addressing would buy nothing. When a file was read is a
    property of the *run*, recorded in runs.jsonl (see Store.record_run) and
    joined back in by `whence explain`. What a source Fact commits to is the
    thing that is actually stable: file path, row, column, and file digest.
"""

from __future__ import annotations

import contextvars
import hashlib
import json
import re
from contextlib import contextmanager
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, Dict, Iterator, Mapping, Sequence

from . import FACT_FORM

__all__ = [
    "Fact", "Store", "MemoryStore", "IntegrityError", "CanonicalizationError",
    "canonical_bytes", "compute_id", "money", "dec",
    "source_fact", "asserted_fact", "active_store", "current_store",
]

KINDS = frozenset({"source", "derived", "asserted"})

_HEX64 = re.compile(r"\A[0-9a-f]{64}\Z")
_META_KEY = re.compile(r"\A[a-z][a-z0-9_.]*\Z")


class IntegrityError(Exception):
    """A stored record does not hash to the id it is filed under."""


class CanonicalizationError(Exception):
    """A Fact contains something with no unambiguous serialization."""


# ---------------------------------------------------------------------------
# Decimal helpers.  All arithmetic in Whence is decimal; none of it is float.
# ---------------------------------------------------------------------------

CENTS = Decimal("0.01")


def dec(x: Any) -> Decimal:
    """Parse decimal text exactly.  Never routes through float."""
    if isinstance(x, Decimal):
        return x
    if isinstance(x, float):
        raise CanonicalizationError(
            "floats are not accepted; pass decimal text instead"
        )
    return Decimal(str(x).strip())


def money(x: Any) -> str:
    """Canonical money text: fixed-point, exactly two decimal places.

    ROUND_HALF_UP, applied at the moment a Fact is minted -- so the rounding is
    part of the record and part of the hash, not a display choice made later.
    """
    q = dec(x).quantize(CENTS, rounding=ROUND_HALF_UP)
    if q == 0:  # collapse the signed zero Decimal is entitled to hand back
        q = Decimal("0.00")
    return format(q, "f")


# ---------------------------------------------------------------------------
# Canonical serialization -- the only bytes that are ever hashed.
# ---------------------------------------------------------------------------

def _validate(kind: str, value: str, transform: str,
              inputs: Sequence[str], metadata: Mapping[str, str]) -> None:
    if kind not in KINDS:
        raise CanonicalizationError("unknown kind %r" % (kind,))
    if not isinstance(value, str):
        raise CanonicalizationError(
            "value must be text, got %s; use money() or str() and decide your "
            "rounding explicitly" % type(value).__name__
        )
    if not isinstance(transform, str) or not transform:
        raise CanonicalizationError("transform must be a non-empty string")
    for i in inputs:
        if not isinstance(i, str) or not _HEX64.match(i):
            raise CanonicalizationError("input is not a fact id: %r" % (i,))
    if kind in ("source", "asserted") and inputs:
        raise CanonicalizationError("a %s fact cannot have inputs" % kind)
    if kind == "derived" and not inputs:
        raise CanonicalizationError("a derived fact must have at least one input")
    for k, v in metadata.items():
        if not isinstance(k, str) or not _META_KEY.match(k):
            raise CanonicalizationError(
                "metadata key %r must match %s (ASCII, lowercase)"
                % (k, _META_KEY.pattern)
            )
        if not isinstance(v, str):
            raise CanonicalizationError(
                "metadata value for %r must be text, got %s"
                % (k, type(v).__name__)
            )


def canonical_bytes(kind: str, value: str, transform: str,
                    inputs: Sequence[str], metadata: Mapping[str, str]) -> bytes:
    """Serialize the hashable content of a Fact.  See SPEC.md section 3.

    Rules, all of which exist so an independent implementation can match us:

      * The document has exactly six keys and no others.
      * `_form` is a domain tag: it stops a Whence id from ever colliding with
        a hash of some other kind of document that happens to look similar.
      * `inputs` is SORTED here. The Fact record on disk keeps the caller's
        semantic order, but the *identity* of a Fact does not depend on the
        order its parents were listed in. Transforms where operand order
        carries meaning must say so in metadata -- see tracked.py, which
        injects an `operands` key for exactly this reason.
      * Object keys are sorted; no insignificant whitespace; UTF-8; non-ASCII
        characters are emitted literally, not backslash-u escaped. That is
        precisely what JavaScript's JSON.stringify does with sorted keys, which
        is how the HTML report can re-verify these ids in a browser.
      * Every leaf is a string. There are no numbers in the hashed document.
    """
    _validate(kind, value, transform, inputs, metadata)
    doc = {
        "_form": FACT_FORM,
        "inputs": sorted(inputs),
        "kind": kind,
        "metadata": dict(metadata),
        "transform": transform,
        "value": value,
    }
    return json.dumps(
        doc, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")


def compute_id(kind: str, value: str, transform: str,
               inputs: Sequence[str], metadata: Mapping[str, str]) -> str:
    return hashlib.sha256(
        canonical_bytes(kind, value, transform, inputs, metadata)
    ).hexdigest()


# ---------------------------------------------------------------------------
# The Fact.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Fact:
    """An immutable claim: this value, produced this way, from these parents.

    Facts are never mutated and never deleted. A "correction" is a new Fact
    with a new id; the old one remains, which is the whole point -- a
    restatement you can see is worth more than a number you can edit.
    """

    value: str
    kind: str
    transform: str
    inputs: tuple = ()
    metadata: Mapping[str, str] = field(default_factory=dict)

    @property
    def id(self) -> str:
        return compute_id(self.kind, self.value, self.transform,
                          self.inputs, self.metadata)

    @property
    def short(self) -> str:
        return self.id[:12]

    # -- convenience accessors used all over the presentation layer ---------
    @property
    def key(self) -> str:
        """Stable business identity, e.g. 'region:EMEA'. How `diff` aligns two
        DAGs that describe the same thing computed at different times."""
        return self.metadata.get("key", "")

    @property
    def label(self) -> str:
        return self.metadata.get("label", self.transform)

    @property
    def amount(self) -> Decimal:
        return dec(self.value)

    def to_record(self) -> dict:
        """The on-disk form: canonical content, plus the id, plus the caller's
        original input order (which the hash deliberately ignores)."""
        return {
            "_form": FACT_FORM,
            "id": self.id,
            "kind": self.kind,
            "value": self.value,
            "transform": self.transform,
            "inputs": list(self.inputs),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_record(cls, rec: Mapping[str, Any], *, verify: bool = True) -> "Fact":
        """Rebuild a Fact from its stored record, re-deriving the id from the
        parts rather than believing the `id` field. This is where tampering is
        caught on the way in."""
        if rec.get("_form") != FACT_FORM:
            raise IntegrityError("unknown record form %r" % (rec.get("_form"),))
        fact = cls(
            value=rec["value"],
            kind=rec["kind"],
            transform=rec["transform"],
            inputs=tuple(rec.get("inputs", ())),
            metadata=dict(rec.get("metadata", {})),
        )
        if verify and fact.id != rec.get("id"):
            raise IntegrityError(
                "record claims id %s but its contents hash to %s"
                % (rec.get("id"), fact.id)
            )
        return fact


def source_fact(value: str, metadata: Mapping[str, str]) -> Fact:
    """A number read out of a file. No parents, but it says exactly where it
    came from -- including the digest of the file it was read from, so a later
    edit to that file is detectable even if the row still reads the same."""
    return Fact(value=value, kind="source", transform="read",
                metadata=dict(metadata))


def asserted_fact(value: str, metadata: Mapping[str, str]) -> Fact:
    """A number a human typed, with no computational parent.

    This is legal and it is recorded. It is also permanently, visibly flagged:
    nothing downstream can launder it into something that looks derived,
    because `kind` is part of the hash.
    """
    return Fact(value=value, kind="asserted", transform="assert",
                metadata=dict(metadata))


# ---------------------------------------------------------------------------
# The store: an append-only journal plus a content-addressed object directory.
# ---------------------------------------------------------------------------

class Store:
    """`.whence/` on disk.

        facts.jsonl        append-only journal, one line per newly-seen fact
        objects/ab/cdef... the same record, filed under its own id
        runs.jsonl         one line per pipeline run (this is where clocks live)

    The journal and the object store hold the same records; the journal gives
    you history and something to grep, the object store gives you O(1) lookup.
    Writing an id that already exists is a no-op -- recomputing something you
    already computed produces no new state, which is what content-addressing
    is for.
    """

    def __init__(self, root: Path | str = ".whence"):
        self.root = Path(root)
        self.objects = self.root / "objects"
        self.journal = self.root / "facts.jsonl"
        self.runs = self.root / "runs.jsonl"
        self.sightings = self.root / "observations.jsonl"
        self._cache: Dict[str, Fact] = {}
        self._obs: Dict[str, list] | None = None
        self._obs_seen: set = set()

    # -- lifecycle ---------------------------------------------------------
    def init(self) -> "Store":
        self.objects.mkdir(parents=True, exist_ok=True)
        self.journal.touch()
        self.runs.touch()
        self.sightings.touch()
        return self

    def _path(self, fid: str) -> Path:
        if not _HEX64.match(fid):
            raise ValueError("not a fact id: %r" % (fid,))
        return self.objects / fid[:2] / (fid[2:] + ".json")

    # -- writes ------------------------------------------------------------
    def put(self, fact: Fact) -> str:
        fid = fact.id
        self._cache[fid] = fact
        path = self._path(fid)
        if path.exists():
            return fid
        text = json.dumps(fact.to_record(), sort_keys=True,
                          ensure_ascii=False, separators=(",", ":"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        with self.journal.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(text + "\n")
        return fid

    def record_run(self, record: Mapping[str, Any]) -> None:
        """Runs are where wall-clock time lives -- see the module docstring."""
        with self.runs.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n")

    # -- observations ------------------------------------------------------
    #
    # Where a number was *seen*, as opposed to what it *says*.
    #
    # A Fact's id covers its claim: "SKU-4471 lists at 1200.00, effective
    # 2026-07-01, approved by nobody". It deliberately does NOT cover the file
    # that claim was read out of, the row number, the digest of that file, or
    # when we looked. Those describe an act of observation, not the thing
    # observed -- and folding them into the hash would be actively harmful:
    # every price in the book would get a new id the moment any unrelated row
    # in the same CSV changed, and `diff` could no longer tell you that 44 of
    # 47 prices are untouched.
    #
    # So observations live here instead: append-only, never mutated, many per
    # fact. Two sightings of one claim in two different files is not a
    # conflict; it is corroboration, and it is worth being able to see.

    def observe(self, fact_id: str, **fields: Any) -> None:
        obs = self._load_observations()
        rec = {"fact": fact_id}
        rec.update({k: str(v) for k, v in fields.items()})
        line = json.dumps(rec, sort_keys=True, ensure_ascii=False)
        if line in self._obs_seen:
            return
        self._obs_seen.add(line)
        obs.setdefault(fact_id, []).append(rec)
        with self.sightings.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(line + "\n")

    def observations(self, fact_id: str) -> list:
        return list(self._load_observations().get(fact_id, ()))

    def _load_observations(self) -> Dict[str, list]:
        if self._obs is None:
            self._obs = {}
            self._obs_seen = set()
            if self.sightings.exists():
                for line in self.sightings.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    rec = json.loads(line)
                    self._obs.setdefault(rec["fact"], []).append(rec)
                    self._obs_seen.add(json.dumps(rec, sort_keys=True,
                                                  ensure_ascii=False))
        return self._obs

    def all_runs(self) -> list:
        if not self.runs.exists():
            return []
        return [json.loads(line) for line in
                self.runs.read_text(encoding="utf-8").splitlines() if line.strip()]

    # -- reads -------------------------------------------------------------
    def has(self, fid: str) -> bool:
        return fid in self._cache or self._path(fid).exists()

    def get(self, fid: str, verify: bool = True) -> Fact:
        """Read a fact, re-hashing it on the way in.

        `verify=False` is for the verifier itself, which performs the same
        check per-fact and reports it far better than an exception can: it can
        say which figure was altered and carry on checking the rest of the
        graph, rather than stopping at the first bad record.
        """
        if fid in self._cache:
            return self._cache[fid]
        path = self._path(fid)
        if not path.exists():
            raise KeyError("no such fact: %s" % fid)
        rec = json.loads(path.read_text(encoding="utf-8"))
        fact = Fact.from_record(rec, verify=verify)
        if verify and fact.id != fid:
            raise IntegrityError("fact filed under %s hashes to %s"
                                 % (fid, fact.id))
        self._cache[fid] = fact
        return fact

    def resolve(self, prefix: str) -> str:
        """Accept an unambiguous id prefix, the way git does."""
        prefix = prefix.strip().lower()
        if prefix.startswith("whence:"):
            prefix = prefix[len("whence:"):]
        prefix = prefix.rstrip("\u2026.")   # tolerate a pasted ellipsis
        if _HEX64.match(prefix):
            return prefix
        if len(prefix) < 4:
            raise KeyError("id prefix %r is too short" % (prefix,))
        hits = [f for f in self.all_ids() if f.startswith(prefix)]
        if not hits:
            raise KeyError("no fact starts with %r" % (prefix,))
        if len(hits) > 1:
            raise KeyError("%r is ambiguous (%d facts match)" % (prefix, len(hits)))
        return hits[0]

    def all_ids(self) -> list:
        if not self.objects.exists():
            return []
        return sorted(p.parent.name + p.stem
                      for p in self.objects.glob("*/*.json"))

    def closure(self, fid: str) -> Dict[str, Fact]:
        """Every fact reachable from `fid`, including itself. The subgraph a
        bundle has to carry for the figure to stand up on its own."""
        out: Dict[str, Fact] = {}
        stack = [fid]
        while stack:
            cur = stack.pop()
            if cur in out:
                continue
            fact = self.get(cur)
            out[cur] = fact
            stack.extend(fact.inputs)
        return out


class MemoryStore(Store):
    """A Store that never touches disk. Used by the bundle verifier, which must
    be able to check a figure on a machine that has no `.whence/` at all."""

    def __init__(self, facts: Mapping[str, Fact] | None = None,
                 observations: Mapping[str, list] | None = None):
        self.root = Path(".")
        self._cache = dict(facts or {})
        self._runs: list = []
        self._obs = {k: list(v) for k, v in (observations or {}).items()}
        self._obs_seen = set()

    def init(self):
        return self

    def observe(self, fact_id: str, **fields: Any) -> None:
        rec = {"fact": fact_id}
        rec.update({k: str(v) for k, v in fields.items()})
        bucket = self._obs.setdefault(fact_id, [])
        if rec not in bucket:
            bucket.append(rec)

    def observations(self, fact_id: str) -> list:
        return list(self._obs.get(fact_id, ()))

    def _load_observations(self):
        return self._obs

    def put(self, fact: Fact) -> str:
        self._cache[fact.id] = fact
        return fact.id

    def has(self, fid: str) -> bool:
        return fid in self._cache

    def get(self, fid: str, verify: bool = True) -> Fact:
        # Facts in memory were built from records, not read back off disk, so
        # there is nothing to re-read; verify_closure re-derives every id.
        try:
            return self._cache[fid]
        except KeyError:
            raise KeyError("no such fact: %s" % fid) from None

    def all_ids(self) -> list:
        return sorted(self._cache)

    def record_run(self, record):
        self._runs.append(dict(record))

    def all_runs(self) -> list:
        return list(self._runs)


# ---------------------------------------------------------------------------
# The ambient store.
#
# pipeline.py reads as ordinary arithmetic -- `line_revenue(qty, price)` -- and
# that is deliberate. The store it writes to is ambient rather than threaded
# through every call, so that recording provenance never looks like an extra
# argument someone could forget to pass.
# ---------------------------------------------------------------------------

_ACTIVE: contextvars.ContextVar = contextvars.ContextVar(
    "whence_active_store", default=None
)


@contextmanager
def active_store(store: Store) -> Iterator[Store]:
    token = _ACTIVE.set(store)
    try:
        yield store
    finally:
        _ACTIVE.reset(token)


def current_store() -> Store:
    store = _ACTIVE.get()
    if store is None:
        raise RuntimeError(
            "no active Whence store; wrap the computation in "
            "`with active_store(store):`"
        )
    return store
