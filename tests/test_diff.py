# SPDX-License-Identifier: Apache-2.0
"""Minimal causal difference.

The bar is not "the diff produced output". It is that the diff finds the three
rows that actually changed, out of the ~1,300 facts behind the two figures, and
that the dollar amounts it attributes to them add up to the difference exactly.
"""

from decimal import Decimal

from whence.diff import describe, diff, headline_groups


def test_a_figure_does_not_differ_from_itself(store, first):
    report = diff(store, first.net.id, first.net.id)
    assert report.identical
    assert report.delta == 0
    assert report.groups == []


def test_the_difference_is_the_arithmetic_difference(store, first, restated):
    report = diff(store, first.net.id, restated.net.id)
    assert report.delta == restated.net.amount - first.net.amount


def test_the_attributed_causes_reconcile_to_the_penny(store, first, restated):
    """The whole point. If the causes do not add up to the delta, the report is
    a story rather than an explanation."""
    report = diff(store, first.net.id, restated.net.id)
    assert report.residual == Decimal("0")
    assert report.reconciles
    attributed = sum((g.delta for g in report.groups if g.delta is not None),
                     Decimal("0"))
    assert attributed == report.delta


def test_exactly_two_independent_causes_are_found(store, first, restated):
    report = diff(store, first.net.id, restated.net.id)
    kinds = [kind for kind, _ in headline_groups(report)]
    assert kinds == ["price", "added"]


def test_the_three_repriced_skus_are_named_with_actor_and_date(store, first,
                                                               restated):
    report = diff(store, first.net.id, restated.net.id)
    price_groups = {g.bucket: g for g in report.groups if g.kind == "price"}
    assert set(price_groups) == {"SKU-4471", "SKU-4472", "SKU-5013"}

    cause = price_groups["SKU-4471"].causes[0]
    assert cause.before == "1200.00"
    assert cause.after == "1340.00"
    assert cause.detail["changed by"] == "m.jensen"
    assert cause.detail["effective"] == "2026-08-14"
    assert cause.detail["approval"] == "(none)"


def test_the_late_orders_are_reported_as_appearing_not_as_changing(store, first,
                                                                   restated):
    report = diff(store, first.net.id, restated.net.id)
    added = [g for g in report.groups if g.kind == "added"]
    assert len(added) == 1
    assert added[0].bucket == "APAC"
    assert added[0].count == 14
    assert added[0].delta > 0


def test_unchanged_facts_are_not_reported_as_changed(store, first, restated):
    """The 44 untouched prices and 200 unchanged orders must stay silent, and
    the two figures must be shown to share most of their history."""
    report = diff(store, first.net.id, restated.net.id)
    changed_keys = {c.key for g in report.groups for c in g.causes}
    assert len(changed_keys) == 17          # 3 prices + 14 added orders
    assert report.shared_facts > report.total_facts // 2


def test_the_description_reads_as_business_language(store, first, restated):
    report = diff(store, first.net.id, restated.net.id)
    blocks = dict(headline_groups(report))
    assert describe("price", blocks["price"]) == \
        "3 SKUs changed list price between the two runs"
    assert "14 orders appear in the second figure" in \
        describe("added", blocks["added"])


def test_diff_survives_comparing_two_unrelated_subtotals(store, first):
    """A diff between two different regions is structurally odd but must not
    crash -- it should simply report that everything about them differs."""
    amer = next(r for r in first.regions if r.metadata["region"] == "AMER")
    emea = next(r for r in first.regions if r.metadata["region"] == "EMEA")
    report = diff(store, amer.id, emea.id)
    assert not report.identical
    assert report.groups
