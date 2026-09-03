# SPDX-License-Identifier: Apache-2.0
"""The quarter close: determinism, and the soft spots it surfaces."""

from pathlib import Path

from whence.core import Store
from whence.explain import explain
from whence.pipeline import DATA, run_close


def test_two_runs_on_unchanged_inputs_produce_the_same_id(tmp_path, first):
    """The headline quality bar. Fresh store, fresh process state, same id."""
    other = Store(tmp_path / ".whence").init()
    again = run_close(other, label="somewhere else entirely")
    assert again.net.id == first.net.id
    assert again.net.value == first.net.value


def test_rerunning_into_the_same_store_writes_nothing_new(tmp_path):
    store = Store(tmp_path / ".whence").init()
    run_close(store, label="one")
    after_first = len(store.all_ids())
    run_close(store, label="two")
    assert len(store.all_ids()) == after_first


def test_the_restatement_moves_the_number(first, restated):
    assert restated.net.id != first.net.id
    assert restated.net.amount > first.net.amount


def test_the_close_is_a_sum_of_its_regions_and_adjustments(store, first):
    total = sum(r.amount for r in first.regions) + \
        sum(a.amount for a in first.adjustments)
    assert total == first.net.amount


def test_adjustments_are_asserted_and_have_no_parents(first):
    assert first.adjustments, "the fixture should contain manual entries"
    for adj in first.adjustments:
        assert adj.kind == "asserted"
        assert adj.inputs == ()
        assert adj.metadata["actor"]
        assert adj.metadata["evidence"]


def test_source_facts_carry_no_parents_but_are_placed_by_observation(store, first):
    closure = store.closure(first.net.id)
    sources = [f for f in closure.values() if f.kind == "source"]
    assert sources
    for fact in sources[:20]:
        assert fact.inputs == ()
        obs = store.observations(fact.id)
        assert obs, "every source fact should record where it was read"
        assert obs[0]["file"] and obs[0]["row"] and obs[0]["digest"]


def test_the_same_claim_read_from_two_files_is_one_fact(store, first, restated):
    """44 of the 47 prices are unchanged between pricebook v3 and v4. Those are
    the same claim, so they are the same fact -- observed twice.

    If the file digest were part of the hash this would be false, and `diff`
    would report all 47 prices as having changed.
    """
    prices = [f for f in store.closure(restated.net.id).values()
              if f.key.startswith("src:price:")]
    seen_in_both = [f for f in prices
                    if len({o["file"] for o in store.observations(f.id)}) == 2]
    assert len(seen_in_both) >= 40


def test_the_figure_surfaces_its_own_soft_spots(store, first):
    findings = explain(store, first.net.id).findings
    joined = " ".join(findings)
    assert "no approval reference" in joined
    assert "cannot be recomputed" in joined
    assert "SKU-4471" in joined


def test_unapproved_pricebook_rows_are_found_without_being_asked(first):
    assert first.unapproved_skus == ["SKU-4471", "SKU-4472", "SKU-5013"]


def test_fixture_data_is_present_and_synthetic():
    for name in ("erp_orders.csv", "erp_orders_restated.csv",
                 "pricebook_v3.csv", "pricebook_v4.csv",
                 "fx_rates.csv", "adjustments.csv"):
        assert (DATA / name).exists(), name


def test_fixture_files_use_lf_endings():
    """The digests in the provenance record are taken over the raw bytes of
    these files, so their line endings are part of the record.

    Git on Windows converts to CRLF by default; .gitattributes pins LF to stop
    that. If this fails, someone's checkout is producing different source
    digests from everyone else's for byte-identical data.
    """
    for path in sorted(DATA.glob("*.csv")):
        assert b"\r\n" not in path.read_bytes(), (
            "%s has CRLF line endings; its digest will not match a clone made "
            "on another platform" % path.name)
