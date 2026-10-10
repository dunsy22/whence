# SPDX-License-Identifier: Apache-2.0
"""Why don't these two numbers match?

The structural answer -- "these two DAGs have 1,300 nodes and 412 of them
differ" -- is useless to the person asking. The useful answer names the handful
of things that actually changed, attributes an exact dollar amount to each, and
says that nothing else moved.

The method:

  1. Walk both graphs together, aligning nodes by their `key` metadata (the
     stable business identity: `region:EMEA`, `src:price:SKU-4471.list_price`).
     Equal ids mean an identical subgraph, so the walk stops there -- which is
     what makes this fast: content-addressing prunes the search.

  2. Keep descending until the difference is cornered at a leaf: a source cell
     that changed, or an input that is present on one side only. That frontier
     is the minimal causal set.

  3. Attribute money to each cause exactly. Under an additive node a child's
     delta *is* its contribution. Under a non-additive node (a multiply, an FX
     conversion) it is not -- a $140 price change on 20 units moves the line by
     $2,800 -- so the whole node delta is attributed to the cause found beneath
     it. Contributions are then grouped by cause and summed.

  4. Check the arithmetic. The attributed deltas must add up to the difference
     between the two headline figures. If they do not, the report says so
     rather than quietly presenting a number that does not reconcile.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from .core import Fact, Store, dec
from .explain import usd

#: Nodes whose children's deltas sum to the node's own delta.
ADDITIVE = {"sum_by_region", "sum_regions", "apply_adjustments", "sum"}


@dataclass
class Cause:
    """One reason the two figures differ, with the money it accounts for."""
    kind: str                       # value | added | removed | structure
    key: str
    label: str
    before: Optional[str] = None
    after: Optional[str] = None
    delta: Optional[Decimal] = None
    detail: Dict[str, str] = field(default_factory=dict)


@dataclass
class CauseGroup:
    """Causes of the same kind, collapsed into one line of the report."""
    kind: str
    bucket: str
    causes: List[Cause]
    delta: Optional[Decimal]

    @property
    def count(self) -> int:
        return len({c.key for c in self.causes})


@dataclass
class DiffReport:
    a: Fact
    b: Fact
    delta: Decimal
    groups: List[CauseGroup] = field(default_factory=list)
    shared_facts: int = 0
    total_facts: int = 0
    residual: Decimal = Decimal("0")
    approximate: bool = False

    @property
    def identical(self) -> bool:
        return self.a.id == self.b.id

    @property
    def reconciles(self) -> bool:
        return self.residual == 0 and not self.approximate


# ---------------------------------------------------------------------------

def diff(store: Store, a_id: str, b_id: str) -> DiffReport:
    a, b = store.get(a_id), store.get(b_id)
    report = DiffReport(a=a, b=b, delta=b.amount - a.amount)

    closure_a = set(store.closure(a_id))
    closure_b = set(store.closure(b_id))
    report.shared_facts = len(closure_a & closure_b)
    report.total_facts = len(closure_a | closure_b)

    if a.id == b.id:
        return report

    causes: List[Cause] = []
    state = {"approximate": False}
    _walk(store, a_id, b_id, causes, state)
    report.approximate = state["approximate"]
    report.groups = _group(causes)

    attributed = sum((g.delta for g in report.groups if g.delta is not None),
                     Decimal("0"))
    report.residual = report.delta - attributed
    return report


def _walk(store: Store, a_id: str, b_id: str,
          out: List[Cause], state: dict) -> None:
    if a_id == b_id:
        return                       # identical subgraph; nothing below differs

    a, b = store.get(a_id), store.get(b_id)
    delta = b.amount - a.amount

    # Structurally different: the two sides were not even produced the same way.
    if a.transform != b.transform or a.kind != b.kind:
        out.append(Cause(
            kind="structure", key=a.key or b.key,
            label=a.label or b.label,
            before="%s via %s" % (a.value, a.transform),
            after="%s via %s" % (b.value, b.transform),
            delta=delta,
            detail={"before transform": a.transform, "after transform": b.transform},
        ))
        return

    # A leaf: a number that was read or asserted, and now reads differently.
    if not a.inputs and not b.inputs:
        out.append(Cause(
            kind="value", key=a.key or b.key, label=a.label,
            before=a.value, after=b.value, delta=delta,
            detail=_leaf_detail(a, b),
        ))
        return

    by_key_a = {store.get(i).key: i for i in a.inputs}
    by_key_b = {store.get(i).key: i for i in b.inputs}

    additive = a.transform in ADDITIVE
    sub: List[Cause] = []

    for key in sorted(set(by_key_b) - set(by_key_a)):
        child = store.get(by_key_b[key])
        sub.append(Cause(
            kind="added", key=key, label=child.label,
            after=child.value, delta=child.amount,
            detail=_presence_detail(child),
        ))

    for key in sorted(set(by_key_a) - set(by_key_b)):
        child = store.get(by_key_a[key])
        sub.append(Cause(
            kind="removed", key=key, label=child.label,
            before=child.value, delta=-child.amount,
            detail=_presence_detail(child),
        ))

    for key in sorted(set(by_key_a) & set(by_key_b)):
        _walk(store, by_key_a[key], by_key_b[key], sub, state)

    if not additive:
        # This node's arithmetic does not distribute over its children, so the
        # child's own delta is not its contribution -- the node's delta is.
        # Assigning on the way back up means the outermost non-additive node
        # wins, which is the one whose value actually reaches a subtotal.
        real = [c for c in sub if c.kind != "structure"]
        if len(real) == 1:
            real[0].delta = delta
        elif len(real) > 1:
            # Two independent changes under one multiplication cannot be split
            # into exact dollar contributions. Say so instead of guessing.
            state["approximate"] = True

    out.extend(sub)


def _leaf_detail(a: Fact, b: Fact) -> Dict[str, str]:
    detail = {}
    m = b.metadata
    if b.kind == "asserted":
        detail["asserted by"] = m.get("actor", "")
        detail["reason"] = m.get("reason", "")
        detail["evidence"] = m.get("evidence", "")
        return detail
    for src, dst in (("modified_by", "changed by"),
                     ("effective_date", "effective"),
                     ("approval_ref", "approval"),
                     ("sku", "sku"), ("order_id", "order"),
                     ("region", "region"), ("currency", "currency")):
        if src in m:
            detail[dst] = m[src] or "(none)"
    return detail


def _presence_detail(fact: Fact) -> Dict[str, str]:
    m = fact.metadata
    return {k: m[k] for k in ("order_id", "region", "sku", "order_date")
            if k in m}


# ---------------------------------------------------------------------------
# Grouping: many line-level contributions, a few human-sized reasons.
# ---------------------------------------------------------------------------

def _bucket(cause: Cause) -> Tuple[str, str]:
    """Which human-sized reason this contribution belongs to."""
    d = cause.detail
    if cause.kind in ("added", "removed"):
        return (cause.kind, d.get("region", ""))
    if cause.kind == "structure":
        return ("structure", cause.key)
    if "sku" in d and "list price" in cause.label:
        return ("price", d["sku"])
    if "asserted by" in d:
        return ("adjustment", cause.key)
    if "order" in d:
        return ("quantity", d["order"])
    return ("other", cause.key)


def _group(causes: List[Cause]) -> List[CauseGroup]:
    buckets: Dict[Tuple[str, str], List[Cause]] = {}
    for c in causes:
        buckets.setdefault(_bucket(c), []).append(c)

    groups = []
    for (kind, bucket), items in buckets.items():
        deltas = [c.delta for c in items]
        total = (sum(deltas, Decimal("0")) if all(d is not None for d in deltas)
                 else None)
        groups.append(CauseGroup(kind=kind, bucket=bucket, causes=items,
                                 delta=total))

    # Biggest absolute effect first; that is the order a reviewer wants.
    groups.sort(key=lambda g: (-abs(g.delta) if g.delta is not None else 0,
                               g.kind, g.bucket))
    return groups


# ---------------------------------------------------------------------------
# Rendering. Grouped by cause type, because that is how people describe them.
# ---------------------------------------------------------------------------

def headline_groups(report: DiffReport) -> List[Tuple[str, List[CauseGroup]]]:
    """Collapse groups into the two or three sentences a person would say."""
    order = ["price", "quantity", "adjustment", "added", "removed",
             "structure", "other"]
    by_kind: Dict[str, List[CauseGroup]] = {}
    for g in report.groups:
        by_kind.setdefault(g.kind, []).append(g)
    return [(k, by_kind[k]) for k in order if k in by_kind]


def describe(kind: str, groups: List[CauseGroup]) -> str:
    """The sentence that heads a cause block."""
    n = sum(g.count for g in groups)
    if kind == "price":
        return ("%d SKU%s changed list price between the two runs"
                % (n, "" if n == 1 else "s"))
    if kind == "quantity":
        return ("%d order%s changed quantity between the two runs"
                % (n, "" if n == 1 else "s"))
    if kind == "adjustment":
        return ("%d manual adjustment%s changed"
                % (n, "" if n == 1 else "s"))
    if kind == "added":
        where = ", ".join(sorted(g.bucket for g in groups if g.bucket))
        return ("%d order%s appear%s in the second figure that %s not in the "
                "first%s" % (n, "" if n == 1 else "s", "s" if n == 1 else "",
                             "was" if n == 1 else "were",
                             " (%s)" % where if where else ""))
    if kind == "removed":
        where = ", ".join(sorted(g.bucket for g in groups if g.bucket))
        return ("%d order%s in the first figure %s gone from the second%s"
                % (n, "" if n == 1 else "s", "is" if n == 1 else "are",
                   " (%s)" % where if where else ""))
    if kind == "structure":
        return "%d figure%s %s computed a different way" % (
            n, "" if n == 1 else "s", "was" if n == 1 else "were")
    return "%d other change%s" % (n, "" if n == 1 else "s")


def detail_line(kind: str, group: CauseGroup) -> str:
    """The headline line of evidence under a cause block.

    Kept short enough to sit beside a right-aligned amount on an 80-column
    terminal. The attribution -- who changed it, when, under what approval --
    goes on the second line, from detail_note.
    """
    c = group.causes[0]
    if kind == "price":
        return "%-10s %s -> %s" % (group.bucket, usd(c.before), usd(c.after))
    if kind == "adjustment":
        return "%s %s -> %s" % (group.bucket.replace("adj:", ""),
                                usd(c.before), usd(c.after))
    if kind in ("added", "removed"):
        orders = sorted(x.detail.get("order_id", x.key) for x in group.causes)
        shown = ", ".join(orders[:3])
        if len(orders) > 3:
            shown += " and %d more" % (len(orders) - 3)
        return "%s: %s" % (group.bucket or "unassigned", shown)
    return "%s %s -> %s" % (group.bucket, c.before, c.after)


def detail_note(kind: str, group: CauseGroup) -> str:
    """The attribution, on its own line. Empty when there is nothing to say."""
    c = group.causes[0]
    if kind == "price":
        when, who = c.detail.get("effective"), c.detail.get("changed by")
        bits = []
        if when and who:
            bits.append("changed %s by %s" % (when, who))
        elif when:
            bits.append("changed %s" % when)
        elif who:
            bits.append("changed by %s" % who)
        approval = c.detail.get("approval", "")
        bits.append("no approval ref" if approval in ("", "(none)")
                    else "approval %s" % approval)
        return ", ".join(bits)
    if kind == "adjustment":
        return "asserted by %s" % c.detail.get("asserted by", "?")
    return ""
