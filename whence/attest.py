# SPDX-License-Identifier: Apache-2.0
"""Bundles, and the verification path.

This is the file to read if you do not trust the rest of this repository.

Everything here uses only the Python standard library -- json, base64,
hashlib -- plus two functions from whence.core (the canonical serializer) and
one from whence.tracked (the transform registry). There is no clever machinery,
no network access, and nothing that can make the verifier say "green" other
than the checks below actually passing.

Verification answers four questions, in order, and stops being polite about any
of them that fails:

  1. Is each record intact?   Re-derive every id from the record's own contents.
                              A record that has been edited no longer hashes to
                              the name it is filed under.
  2. Is the graph complete?   Every id named as an input must be present. A
                              bundle missing a parent is not a smaller proof,
                              it is not a proof.
  3. Does the arithmetic hold? Re-run each derived fact's transform against its
                              parents' values and compare. Re-hashing alone
                              would only prove the file has not been edited --
                              not that the number follows from its inputs.
  4. What cannot be checked?  Asserted facts have no computational parent and
                              never will. They are listed, never counted as
                              verified, and never silently passed over.

The same function does all of this for a local store and for a bundle on a
machine with no source systems attached. There is no separate, weaker code path
for the offline case -- that would be exactly the place to hide a lie.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from . import BUNDLE_PREDICATE, FACT_FORM, __version__
from .core import (CanonicalizationError, Fact, IntegrityError, MemoryStore,
                   Store, compute_id, dec)
from .tracked import REGISTRY, replay

# ---------------------------------------------------------------------------
# Format constants.
#
# The envelope is DSSE and the payload is an in-toto Statement. Both are
# existing standards for exactly this shape of problem -- "here is an artifact,
# here is a verifiable claim about how it came to be" -- and reusing them means
# a bundle can be handled by tooling that has never heard of Whence. SPEC.md
# section 6 explains the mapping and where it stretches.
# ---------------------------------------------------------------------------

STATEMENT_TYPE = "https://in-toto.io/Statement/v1"
PAYLOAD_TYPE = "application/vnd.in-toto+json"


def pae(payload_type: str, body: bytes) -> bytes:
    """DSSE Pre-Authentication Encoding -- the exact bytes a signer signs.

        PAE(type, body) = "DSSEv1" SP LEN(type) SP type SP LEN(body) SP body

    where LEN is the ASCII decimal *byte* length and SP is a single 0x20. Note
    that it is the raw serialized body that is signed, never its base64 form.

    Whence does not sign anything today (see the note in export_bundle), so
    nothing in this module calls this function. It is here, and tested against
    the vector in the DSSE specification, because the format is only worth
    adopting if the next person can add signing without having to re-derive
    the construction or guess at what we would have meant.
    """
    type_bytes = payload_type.encode("utf-8")
    return b" ".join([
        b"DSSEv1",
        str(len(type_bytes)).encode("ascii"), type_bytes,
        str(len(body)).encode("ascii"), body,
    ])


@dataclass
class VerifyReport:
    """What a verification run found. Deliberately not a boolean."""
    root: str = ""
    label: str = ""
    value: str = ""
    facts_checked: int = 0
    hashes_ok: int = 0
    recomputed: int = 0
    sources: int = 0
    asserted: List[Fact] = field(default_factory=list)
    unknown_transforms: List[str] = field(default_factory=list)
    failures: List[str] = field(default_factory=list)
    origin: str = ""
    #: Input files the figure was built from, name -> sha256. Populated from
    #: the statement parsed during verification, never by re-reading the file:
    #: DSSE is explicit that an implementation must not re-parse an envelope
    #: after verifying it to pull the payload back out.
    signed: bool = False
    source_files: Dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.failures

    @property
    def complete(self) -> bool:
        """True when every derived figure was re-run, not merely re-hashed."""
        return self.ok and not self.unknown_transforms


# ---------------------------------------------------------------------------
# The verification engine. Both `whence verify` and `whence verify-bundle`
# call this and nothing else.
# ---------------------------------------------------------------------------

def _reachable(store: Store, root: str) -> Dict[str, Fact]:
    """Store.closure, but tolerant of a parent that is not there.

    A gap is exactly what a stripped-down bundle looks like, and it is more
    useful to report "this figure is missing two of its parents" than to fall
    over on the first lookup. Missing ids are left out here and picked up by
    the completeness check, which knows which figure was depending on them.

    Records are loaded without the store's own integrity gate for the same
    reason: verify_closure re-derives every id itself a moment later, and it
    can name the altered figure and keep checking the remaining few hundred,
    where an exception here would abort the whole report at the first one.
    """
    out: Dict[str, Fact] = {}
    stack = [root]
    while stack:
        current = stack.pop()
        if current in out:
            continue
        try:
            fact = store.get(current, verify=False)
        except KeyError:
            continue
        out[current] = fact
        stack.extend(fact.inputs)
    return out


def _load_transforms() -> None:
    """Make sure the transform registry is populated before verifying.

    Transforms register themselves as a side effect of being defined, so a
    process that imports only the verifier starts with an empty registry --
    and that is precisely the situation `whence verify-bundle` is in on an
    auditor's machine, where nothing ever touches the pipeline. Without this,
    verification would report every derived figure as "unknown transform":
    honest, but useless, and alarming in exactly the wrong way.
    """
    from . import arith, pipeline  # noqa: F401  (importing is the registration)


def verify_closure(store: Store, root: str) -> VerifyReport:
    _load_transforms()
    report = VerifyReport(root=root)

    # Both lookups skip the store's own integrity gate on purpose; the
    # per-fact check below re-derives every id and reports failures with the
    # figure's name attached, instead of aborting on the first bad record.
    try:
        closure = _reachable(store, root)
        top = store.get(root, verify=False)
    except (KeyError, IntegrityError) as exc:
        report.failures.append(str(exc))
        return report
    report.label, report.value = top.label, top.value

    for fid, fact in sorted(closure.items()):
        report.facts_checked += 1

        # (1) Intact: does this record still hash to its own name?
        recomputed_id = compute_id(fact.kind, fact.value, fact.transform,
                                   fact.inputs, fact.metadata)
        if recomputed_id != fid:
            report.failures.append(
                "%s: contents hash to %s -- this record has been altered"
                % (fid[:12], recomputed_id[:12]))
            continue
        report.hashes_ok += 1

        # (2) Complete: is every parent actually here?
        missing = [i for i in fact.inputs if not store.has(i)]
        if missing:
            report.failures.append(
                "%s (%s): %d parent fact(s) missing, so this figure cannot be "
                "checked" % (fid[:12], fact.label, len(missing)))
            continue

        # (3) Sound: does the arithmetic still produce this number?
        if fact.kind == "derived":
            if fact.transform not in REGISTRY:
                report.unknown_transforms.append(fact.transform)
                report.failures.append(
                    "%s: unknown transform %r -- this build cannot check it"
                    % (fid[:12], fact.transform))
                continue
            try:
                got = replay(fact, store)
            except CanonicalizationError as exc:
                # A malformed `quantize` declaration. The record is intact --
                # it hashed correctly a moment ago -- but it does not say
                # anything this build can act on, so it is not verified.
                report.failures.append(
                    "%s (%s): %s" % (fid[:12], fact.label, exc))
                continue
            if got != fact.value:
                report.failures.append(
                    "%s (%s): stored %s but its inputs produce %s"
                    % (fid[:12], fact.label, fact.value, got))
                continue
            report.recomputed += 1

        # (4) Honest: say what cannot be checked, rather than passing it.
        elif fact.kind == "asserted":
            report.asserted.append(fact)
        else:
            report.sources += 1

    return report


def verify_local(store: Store, root: str) -> VerifyReport:
    report = verify_closure(store, root)
    report.origin = "local store at %s" % store.root
    return report


# ---------------------------------------------------------------------------
# Export.
# ---------------------------------------------------------------------------

def build_statement(store: Store, root: str) -> dict:
    """The in-toto Statement that a bundle carries.

    The subject is the figure itself. in-toto subjects are normally files
    identified by the digest of their bytes; here the digest IS the fact id,
    which is the SHA-256 of the fact's canonical serialization. So the
    convention holds exactly -- the artifact being attested to is a record, and
    it is named by the hash of its own content.
    """
    closure = store.closure(root)
    top = store.get(root)

    observations = []
    for fid in sorted(closure):
        observations.extend(store.observations(fid))

    # Only the runs that actually produced something in this closure.
    runs = [r for r in store.all_runs() if r.get("root") in closure
            or r.get("gross") in closure]

    return {
        "_type": STATEMENT_TYPE,
        "subject": [{
            "name": top.label,
            "digest": {"sha256": root},
        }],
        "predicateType": BUNDLE_PREDICATE,
        "predicate": {
            "whenceVersion": __version__,
            "factForm": FACT_FORM,
            "root": root,
            "value": top.value,
            # Sorted by id so the same closure always serializes identically:
            # two people exporting the same figure produce the same bytes.
            "facts": [closure[f].to_record() for f in sorted(closure)],
            "observations": observations,
            "runs": runs,
        },
    }


def export_bundle(store: Store, root: str, path: Path | str) -> dict:
    """Write a portable, self-contained .wbundle.

    Self-contained means what it sounds like: every fact in the closure travels
    with it, so the recipient needs no ERP, no pricebook, no `.whence/`, and no
    contact with whoever produced it.
    """
    statement = build_statement(store, root)
    payload = json.dumps(statement, sort_keys=True, ensure_ascii=False,
                         separators=(",", ":")).encode("utf-8")

    envelope = {
        "payloadType": PAYLOAD_TYPE,
        "payload": base64.standard_b64encode(payload).decode("ascii"),
        # Unsigned, and explicitly so.
        #
        # DSSE requires the signatures array to be non-empty, and requires
        # `sig` to be present "even if empty" -- so an unsigned envelope is
        # spelled as exactly one entry with an empty signature, rather than as
        # an empty list. A verifier must read this as "nobody has vouched for
        # the origin of this bundle", which is the truth.
        #
        # The hashes prove *what* the bundle says and that nothing in it has
        # been altered. A signature would prove *who* produced it, and that is
        # a key-custody problem this proof of concept does not pretend to have
        # solved. pae() above is the missing half, ready for whoever does.
        "signatures": [{"keyid": "", "sig": ""}],
    }

    path = Path(path)
    path.write_text(json.dumps(envelope, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")

    return {
        "path": str(path),
        "root": root,
        "value": statement["predicate"]["value"],
        "facts": len(statement["predicate"]["facts"]),
        "bytes": path.stat().st_size,
    }


# ---------------------------------------------------------------------------
# Import and verify. This half must work on a machine that has nothing.
# ---------------------------------------------------------------------------

def read_statement(path: Path | str) -> tuple:
    """Unwrap the DSSE envelope; return (statement, signatures)."""
    envelope = json.loads(Path(path).read_text(encoding="utf-8"))

    if envelope.get("payloadType") != PAYLOAD_TYPE:
        raise ValueError("unexpected payloadType %r (want %r)"
                         % (envelope.get("payloadType"), PAYLOAD_TYPE))

    signatures = envelope.get("signatures")
    if not signatures:
        # DSSE requires at least one entry. An unsigned bundle says so with a
        # single empty signature; no entries at all is a malformed envelope.
        raise ValueError("envelope has no signatures array (DSSE requires "
                         "at least one entry, empty if unsigned)")

    # Base64: DSSE permits standard or URL-safe, and verifiers must accept
    # either, so try both rather than assuming our own producer wrote it.
    raw = envelope["payload"]
    try:
        payload = base64.standard_b64decode(raw)
    except Exception:
        payload = base64.urlsafe_b64decode(raw)
    statement = json.loads(payload)

    if statement.get("_type") != STATEMENT_TYPE:
        raise ValueError("unexpected statement type %r" % statement.get("_type"))
    if statement.get("predicateType") != BUNDLE_PREDICATE:
        raise ValueError("unexpected predicateType %r"
                         % statement.get("predicateType"))
    return statement, signatures


def load_bundle(path: Path | str) -> tuple:
    """Return (MemoryStore, statement, signatures). Nothing touches the
    filesystem after this point, which is what makes offline verification
    meaningful."""
    statement, signatures = read_statement(path)
    predicate = statement["predicate"]

    if predicate.get("factForm") != FACT_FORM:
        raise ValueError("bundle uses fact form %r; this build speaks %r"
                         % (predicate.get("factForm"), FACT_FORM))

    facts: Dict[str, Fact] = {}
    for record in predicate["facts"]:
        # verify=False here on purpose: Fact.from_record would raise on the
        # first altered record, and we would rather collect and report every
        # problem than die on the earliest one. verify_closure re-derives all
        # of these ids a moment later.
        fact = Fact.from_record(record, verify=False)
        facts[record["id"]] = fact

    observations: Dict[str, list] = {}
    for obs in predicate.get("observations", []):
        observations.setdefault(obs["fact"], []).append(obs)

    store = MemoryStore(facts, observations)
    for run in predicate.get("runs", []):
        store.record_run(run)
    return store, statement, signatures


def verify_bundle(path: Path | str) -> VerifyReport:
    """Check a bundle with no access to any source system."""
    try:
        store, statement, signatures = load_bundle(path)
    except (ValueError, KeyError, json.JSONDecodeError) as exc:
        report = VerifyReport()
        report.failures.append("bundle is unreadable: %s" % exc)
        return report

    predicate = statement["predicate"]
    root = predicate["root"]

    report = verify_closure(store, root)
    report.origin = "bundle %s" % Path(path).name

    # The subject is the point of the whole envelope, so check it agrees with
    # the predicate rather than assuming the producer was consistent.
    subjects = statement.get("subject") or []
    digests = [s.get("digest", {}).get("sha256") for s in subjects]
    if root not in digests:
        report.failures.append(
            "the envelope's subject (%s) is not the figure the predicate "
            "describes (%s)" % (", ".join(d[:12] for d in digests if d) or "none",
                                root[:12]))

    stated = predicate.get("value")
    if stated is not None and report.value and stated != report.value:
        report.failures.append(
            "the bundle advertises %s but its own facts produce %s"
            % (stated, report.value))

    # Carried out of the already-parsed statement so no caller has to open the
    # envelope a second time.
    for run_rec in predicate.get("runs", []):
        report.source_files.update(run_rec.get("sources") or {})
    report.signed = any(sig.get("sig") for sig in signatures)

    return report



