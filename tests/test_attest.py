# SPDX-License-Identifier: Apache-2.0
"""Bundles: the format, and whether the verifier can be fooled.

Half of these tests try to make verification pass when it should not. A
verifier that only ever sees valid input has not been tested.
"""

import base64
import re
from pathlib import Path
import json

import pytest

from whence import BUNDLE_PREDICATE
from whence.attest import (PAYLOAD_TYPE, STATEMENT_TYPE, export_bundle,
                           load_bundle, pae, read_statement, verify_bundle,
                           verify_local)
from whence.core import Fact, Store


@pytest.fixture(scope="module")
def bundle(tmp_path_factory, store, first):
    path = tmp_path_factory.mktemp("bundles") / "q3.wbundle"
    export_bundle(store, first.net.id, path)
    return path


def rewrite(path, mutate):
    """Copy a bundle, letting `mutate` alter the decoded statement first."""
    envelope = json.loads(path.read_text(encoding="utf-8"))
    statement = json.loads(base64.standard_b64decode(envelope["payload"]))
    mutate(statement)
    envelope["payload"] = base64.standard_b64encode(
        json.dumps(statement).encode("utf-8")).decode("ascii")
    out = path.parent / "tampered.wbundle"
    out.write_text(json.dumps(envelope), encoding="utf-8")
    return out


# ---------------------------------------------------------------------------
# Format conformance.
# ---------------------------------------------------------------------------

def test_the_envelope_is_dsse_shaped(bundle):
    envelope = json.loads(bundle.read_text(encoding="utf-8"))
    assert set(envelope) == {"payload", "payloadType", "signatures"}
    assert envelope["payloadType"] == PAYLOAD_TYPE
    # DSSE requires at least one signature entry; an unsigned bundle says so
    # with an empty sig rather than an empty list.
    assert len(envelope["signatures"]) == 1
    assert envelope["signatures"][0]["sig"] == ""


def test_the_payload_is_an_in_toto_statement(bundle):
    statement, signatures = read_statement(bundle)
    assert statement["_type"] == STATEMENT_TYPE
    assert statement["predicateType"] == BUNDLE_PREDICATE
    assert len(statement["subject"]) == 1
    assert set(statement["subject"][0]["digest"]) == {"sha256"}
    assert len(signatures) == 1


def test_the_subject_digest_is_the_figures_own_id(bundle, first):
    statement, _ = read_statement(bundle)
    assert statement["subject"][0]["digest"]["sha256"] == first.net.id
    assert statement["predicate"]["root"] == first.net.id


def test_pae_matches_the_published_dsse_vector():
    """From the DSSE specification. Nothing signs anything yet, but if the
    construction is wrong the first person to add signing inherits the bug."""
    assert pae("http://example.com/HelloWorld", b"hello world") == \
        b"DSSEv1 29 http://example.com/HelloWorld 11 hello world"


def test_exporting_the_same_figure_twice_produces_the_same_bytes(
        tmp_path, store, first):
    a, b = tmp_path / "a.wbundle", tmp_path / "b.wbundle"
    export_bundle(store, first.net.id, a)
    export_bundle(store, first.net.id, b)
    assert a.read_bytes() == b.read_bytes()


# ---------------------------------------------------------------------------
# Verification, offline.
# ---------------------------------------------------------------------------

def test_a_bundle_verifies_with_no_store_and_no_source_files(bundle, first):
    report = verify_bundle(bundle)
    assert report.ok
    assert report.complete
    assert report.value == first.net.value
    assert report.recomputed > 100
    assert not report.failures


def test_verification_carries_the_source_digests_it_could_not_see(bundle, first):
    report = verify_bundle(bundle)
    assert set(report.source_files) == set(first.sources)
    for name, digest in first.sources.items():
        assert report.source_files[name] == digest


def test_the_bundle_holds_the_complete_closure(bundle, store, first):
    _memory, statement, _sigs = load_bundle(bundle)
    assert len(statement["predicate"]["facts"]) == \
        len(store.closure(first.net.id))


def test_offline_verification_matches_local_verification(bundle, store, first):
    local = verify_local(store, first.net.id)
    offline = verify_bundle(bundle)
    assert local.ok and offline.ok
    assert (local.hashes_ok, local.recomputed, len(local.asserted)) == \
           (offline.hashes_ok, offline.recomputed, len(offline.asserted))


def test_asserted_figures_are_reported_and_never_counted_as_verified(bundle):
    report = verify_bundle(bundle)
    assert len(report.asserted) == 2
    assert {f.metadata["adj_id"] for f in report.asserted} == {"ADJ-001",
                                                               "ADJ-002"}
    assert report.recomputed + report.sources + len(report.asserted) == \
        report.facts_checked


# ---------------------------------------------------------------------------
# Attempts to fool it.
# ---------------------------------------------------------------------------

def test_editing_a_value_in_the_bundle_is_caught(bundle):
    def bump(statement):
        for record in statement["predicate"]["facts"]:
            if record["id"] == statement["predicate"]["root"]:
                record["value"] = "9999999.99"
    report = verify_bundle(rewrite(bundle, bump))
    assert not report.ok
    assert any("altered" in f for f in report.failures)


def test_editing_a_value_and_its_id_together_is_still_caught(bundle):
    """Re-hashing the edited record fixes the record but orphans it: the
    figure's parent still names the id the original had."""
    def forge(statement):
        root = statement["predicate"]["root"]
        for record in statement["predicate"]["facts"]:
            if record["id"] == root:
                record["value"] = "9999999.99"
                record["id"] = Fact.from_record(record, verify=False).id
                statement["predicate"]["root"] = record["id"]
                statement["subject"][0]["digest"]["sha256"] = record["id"]
                statement["predicate"]["value"] = "9999999.99"
    report = verify_bundle(rewrite(bundle, forge))
    assert not report.ok
    # The arithmetic no longer holds: its inputs do not add to the new total.
    assert any("produce" in f for f in report.failures)


def test_removing_a_parent_fact_is_caught(bundle):
    def drop(statement):
        facts = statement["predicate"]["facts"]
        root = statement["predicate"]["root"]
        victim = next(r for r in facts
                      if r["kind"] == "source" and r["id"] != root)
        facts.remove(victim)
    report = verify_bundle(rewrite(bundle, drop))
    assert not report.ok
    assert any("missing" in f for f in report.failures)


def test_a_subject_that_disagrees_with_the_predicate_is_caught(bundle):
    def swap(statement):
        statement["subject"][0]["digest"]["sha256"] = "0" * 64
    report = verify_bundle(rewrite(bundle, swap))
    assert not report.ok
    assert any("subject" in f for f in report.failures)


def test_an_advertised_value_that_the_facts_do_not_support_is_caught(bundle):
    def lie(statement):
        statement["predicate"]["value"] = "8000000.00"
    report = verify_bundle(rewrite(bundle, lie))
    assert not report.ok
    assert any("advertises" in f for f in report.failures)


def test_a_bundle_naming_an_unknown_transform_is_not_passed(bundle):
    def alien(statement):
        for record in statement["predicate"]["facts"]:
            if record["kind"] == "derived":
                record["transform"] = "definitely_not_a_real_transform"
                record["id"] = Fact.from_record(record, verify=False).id
                break
    report = verify_bundle(rewrite(bundle, alien))
    assert not report.ok


def test_an_envelope_with_no_signature_entry_is_rejected(bundle, tmp_path):
    envelope = json.loads(bundle.read_text(encoding="utf-8"))
    envelope["signatures"] = []
    bad = tmp_path / "nosig.wbundle"
    bad.write_text(json.dumps(envelope), encoding="utf-8")
    report = verify_bundle(bad)
    assert not report.ok
    assert any("signatures" in f for f in report.failures)


def test_a_bundle_of_the_wrong_predicate_type_is_rejected(bundle, tmp_path):
    def hijack(statement):
        statement["predicateType"] = "https://example.invalid/something/v1"
    report = verify_bundle(rewrite(bundle, hijack))
    assert not report.ok
    assert any("unreadable" in f for f in report.failures)


def test_garbage_is_reported_not_raised(tmp_path):
    junk = tmp_path / "junk.wbundle"
    junk.write_text("this is not a bundle", encoding="utf-8")
    report = verify_bundle(junk)
    assert not report.ok


def test_tampering_with_the_local_store_fails_verification(tmp_path, store,
                                                           first):
    """The same check, against a disk store whose object file has been edited
    by hand."""
    victim = Store(tmp_path / ".whence").init()
    from whence.pipeline import run_close
    result = run_close(victim, label="to be tampered with")

    region = result.regions[0]
    path = victim._path(region.id)
    record = json.loads(path.read_text(encoding="utf-8"))
    record["value"] = "1.00"
    path.write_text(json.dumps(record), encoding="utf-8")

    report = verify_local(Store(victim.root), result.net.id)
    assert not report.ok

    # The report should show the blast radius, not just stop at the first bad
    # record: the altered fact AND the subtotal that no longer adds up.
    joined = " ".join(report.failures)
    assert "has been altered" in joined
    assert "but its inputs produce" in joined
    # ...and it must still have checked the rest of the graph.
    assert report.hashes_ok > 500


# ---------------------------------------------------------------------------
# The auditor's machine.
# ---------------------------------------------------------------------------

def test_verify_bundle_works_in_a_fresh_process_with_nothing_else(
        tmp_path, bundle):
    """The real offline case, and a regression test.

    Transforms register themselves when the pipeline module is imported. A
    process that only ever runs the verifier never imports it -- so this once
    reported every derived figure as an unknown transform, while the in-process
    tests passed because the test session had imported the pipeline already.
    Only a subprocess in an empty directory catches that.
    """
    import os
    import shutil
    import subprocess
    import sys

    scratch = tmp_path / "auditor"
    scratch.mkdir()
    shutil.copy(bundle, scratch / "figure.wbundle")
    assert [p.name for p in scratch.iterdir()] == ["figure.wbundle"]

    env = dict(os.environ)
    repo = str(Path(__file__).resolve().parent.parent)
    env["PYTHONPATH"] = repo + os.pathsep + env.get("PYTHONPATH", "")

    proc = subprocess.run(
        [sys.executable, "-m", "whence", "verify-bundle", "figure.wbundle"],
        cwd=scratch, capture_output=True, text=True, env=env,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "VERIFIED" in proc.stdout
    assert "unknown transform" not in proc.stdout
    assert "figures recomputed" in proc.stdout
    # It must have actually re-run the arithmetic, not just re-hashed records.
    recomputed = int(re.search(r"(\d+) figures recomputed", proc.stdout).group(1))
    assert recomputed > 100
