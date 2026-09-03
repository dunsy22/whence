# SPDX-License-Identifier: Apache-2.0
"""Turning a subgraph into an answer a person would give.

`whence explain` is the command a finance team will judge this on, so the
default output is a paragraph and a short list, not a graph. The graph is still
there behind `--tree`, and the machine-readable form behind `--json`; but the
first thing anyone sees is prose.

Nothing in here is hardcoded copy. Every sentence is assembled from what is
actually in the facts -- if a figure has no adjustments, no adjustment line is
printed, because there is nothing to print it from.

The same Explanation object renders the terminal narrative and the HTML report
side panel, so those two can never drift apart.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import Dict, List, Optional

from .core import Fact, Store, dec

MONTHS = {"01": "Jan", "02": "Feb", "03": "Mar", "04": "Apr", "05": "May",
          "06": "Jun", "07": "Jul", "08": "Aug", "09": "Sep", "10": "Oct",
          "11": "Nov", "12": "Dec"}


def usd(value) -> str:
    """$1,234.56 / -$1,234.56 -- accounting-legible, never scientific."""
    d = dec(value)
    sign = "-" if d < 0 else ""
    return "%s$%s" % (sign, "{:,.2f}".format(abs(d)))


def pretty_date(iso: str) -> str:
    parts = iso.split("-")
    if len(parts) != 3:
        return iso
    return "%s %s" % (MONTHS.get(parts[1], parts[1]), parts[2].lstrip("0"))


# ---------------------------------------------------------------------------

@dataclass
class Component:
    """One direct contributor to a figure, as it should be read aloud."""
    id: str
    label: str
    value: str
    display: str
    note: str = ""
    kind: str = "derived"
    warning: str = ""

    @property
    def unverifiable(self) -> bool:
        return self.kind == "asserted"


@dataclass
class Explanation:
    id: str
    label: str
    value: str
    display: str
    kind: str
    transform: str
    summary: str
    components: List[Component] = field(default_factory=list)
    findings: List[str] = field(default_factory=list)
    provenance: Dict[str, str] = field(default_factory=dict)
    read_at: str = ""
    closure_size: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------

def explain(store: Store, fid: str) -> Explanation:
    fact = store.get(fid)
    closure = store.closure(fid)

    exp = Explanation(
        id=fact.id, label=fact.label, value=fact.value,
        display=usd(fact.value), kind=fact.kind, transform=fact.transform,
        summary=_summary(store, fact, closure),
        closure_size=len(closure),
    )

    if fact.kind == "source":
        exp.provenance = _source_provenance(store, fact)
        exp.read_at = _read_time(store, fact)
    elif fact.kind == "asserted":
        exp.provenance = _asserted_provenance(store, fact)
        exp.read_at = _read_time(store, fact)
    else:
        exp.components = [_component(store, f) for f in _display_inputs(store, fact)]
        exp.components.sort(key=_component_order)

    exp.findings = findings(store, closure)
    return exp


def _component_order(c: Component):
    """Derived subtotals first, human assertions last -- the way a reviewer
    reads a schedule: the arithmetic, then the things someone typed."""
    return (c.kind == "asserted", c.label)


def _component(store: Store, fact: Fact) -> Component:
    return Component(
        id=fact.id, label=_component_label(fact), value=fact.value,
        display=usd(fact.value), kind=fact.kind,
        note=_component_note(store, fact),
        warning=_component_warning(fact),
    )


def _component_label(fact: Fact) -> str:
    if fact.kind == "asserted":
        return "Adjustment"
    if fact.transform == "sum_by_region":
        return fact.metadata.get("region", fact.label)
    if fact.transform == "sum_regions":
        return "Gross revenue"
    return fact.label


def _component_note(store: Store, fact: Fact) -> str:
    """The clause after the number. Assembled, never canned."""
    if fact.kind == "asserted":
        who = fact.metadata.get("actor", "unknown")
        why = fact.metadata.get("reason", "")
        return 'asserted by %s, "%s"' % (who, why) if why else "asserted by %s" % who

    if fact.transform == "sum_by_region":
        n = fact.metadata.get("order_count", str(len(fact.inputs)))
        note = "from %s orders" % n
        currencies = _currencies_under(store, fact)
        if any(c != "USD" for c in currencies):
            note += ", FX-converted at %s rates" % pretty_date(
                _rate_date_under(store, fact))
        return note

    if fact.transform == "sum_regions":
        return "%s regional subtotals" % fact.metadata.get(
            "region_count", str(len(fact.inputs)))

    if fact.transform == "fx_convert":
        cur = fact.metadata.get("currency", "")
        if cur and cur != "USD":
            return "%s converted to USD at %s close" % (
                cur, pretty_date(fact.metadata.get("rate_date", "")))
        return "already USD"

    if fact.transform == "multiply":
        sku = fact.metadata.get("sku", "")
        recognized = fact.metadata.get("price_recognized", "")
        return "%s at %s list price" % (sku, usd(recognized)) if recognized else ""

    if fact.kind == "source":
        obs = sighting(store, fact)
        return "%s row %s" % (obs.get("file", "?"), obs.get("row", "?"))
    return ""


def _component_warning(fact: Fact) -> str:
    if fact.kind == "asserted":
        return "no upstream system - this number cannot be verified"
    return ""


def _summary(store: Store, fact: Fact, closure: Dict[str, Fact]) -> str:
    """The one-line answer to 'where does this come from?'"""
    if fact.kind == "source":
        obs = sighting(store, fact)
        return "Read directly from %s, row %s, column %s." % (
            _seen_in(store, fact) or "?", obs.get("row", "?"),
            obs.get("column", "?"))

    if fact.kind == "asserted":
        return (
            "Asserted by %s on %s. Its stated basis is: %s. There is no "
            "upstream system behind it, so Whence can confirm that nobody has "
            "altered it since - but not that it is right."
            % (fact.metadata.get("actor", "unknown"),
               fact.metadata.get("entry_date", "an unrecorded date"),
               fact.metadata.get("evidence", "not recorded"))
        )

    shown = _display_inputs(store, fact)
    parts = []
    regions = [f for f in shown if f.transform == "sum_by_region"]
    asserted = [f for f in shown if f.kind == "asserted"]
    other = [f for f in shown if f not in regions and f not in asserted]

    if regions:
        parts.append(_plural(len(regions), "regional subtotal"))
    for f in other:
        parts.append(_component_label(f).lower())
    if asserted:
        parts.append(_plural(len(asserted), "manual adjustment"))

    if not parts:
        parts.append(_plural(len(fact.inputs), "input"))

    orders = len({f.metadata["order_id"] for f in closure.values()
                  if "order_id" in f.metadata})
    tail = " tracing back to %s across %s." % (
        _plural(orders, "order"), _plural(len(closure), "recorded fact")
    ) if orders else " drawn from %s." % _plural(len(closure), "recorded fact")

    return "Built from " + _join(parts) + tail


def _plural(n: int, noun: str) -> str:
    return "%d %s%s" % (n, noun, "" if n == 1 else "s")


def _join(parts: List[str]) -> str:
    if len(parts) == 1:
        return parts[0]
    return ", ".join(parts[:-1]) + " and " + parts[-1]


# ---------------------------------------------------------------------------
# Findings: the soft spots, surfaced without anyone going looking.
# ---------------------------------------------------------------------------

def findings(store: Store, closure: Dict[str, Fact]) -> List[str]:
    out = []

    asserted = [f for f in closure.values() if f.kind == "asserted"]
    if asserted:
        net = sum((dec(f.value) for f in asserted), Decimal("0"))
        gross = sum((abs(dec(f.value)) for f in asserted), Decimal("0"))
        # Report the gross exposure, not just the net. Two manual entries that
        # happen to offset are still two numbers nobody can recompute.
        one = len(asserted) == 1
        count = "1 manual entry" if one else "%d manual entries" % len(asserted)
        amount = (usd(gross) if gross == abs(net)
                  else "%s of movement, %s net" % (usd(gross), usd(net)))
        out.append(
            "%s (%s) %s asserted by %s with no upstream system and cannot be "
            "recomputed: %s."
            % (count, amount, "was" if one else "were",
               "a person" if one else "people",
               ", ".join(sorted(f.metadata.get("adj_id", f.short)
                                for f in asserted)))
        )

    prices = [f for f in closure.values()
              if f.kind == "source" and f.key.startswith("src:price:")]
    unapproved = sorted({f.metadata.get("sku", "?") for f in prices
                         if not f.metadata.get("approval_ref", "").strip()})
    if unapproved:
        out.append(
            "%d of %d SKUs in this figure %s priced from pricebook rows with "
            "no approval reference (%s)."
            % (len(unapproved), len(prices),
               "was" if len(unapproved) == 1 else "were",
               ", ".join(unapproved))
        )

    off_list = [f for f in closure.values()
                if f.transform == "multiply"
                and f.metadata.get("price_at_sale")
                and f.metadata["price_at_sale"] != f.metadata.get("price_recognized")]
    if off_list:
        out.append(
            "%d %s invoiced at a unit price different from the approved list "
            "price; revenue is recognized at list (%s)."
            % (len(off_list), "order was" if len(off_list) == 1 else "orders were",
               ", ".join(sorted(f.metadata.get("order_id", f.short)
                                for f in off_list)[:6]))
        )

    stale = sorted({f.metadata["modified_by"] for f in prices
                    if f.metadata.get("effective_date", "") > "2026-07-01"
                    and f.metadata.get("modified_by")})
    if stale:
        changed = sorted({f.metadata.get("sku", "?") for f in prices
                          if f.metadata.get("effective_date", "") > "2026-07-01"})
        out.append(
            "%d SKUs were repriced mid-quarter by %s (%s)."
            % (len(changed), ", ".join(stale), ", ".join(changed))
        )

    return out


# ---------------------------------------------------------------------------

def _inputs(store: Store, fact: Fact) -> List[Fact]:
    return [store.get(i) for i in fact.inputs]


#: Nodes that exist to hold a sum together rather than to mean anything on
#: their own. The narrative reads straight through them, so a reader sees the
#: four regions under the headline instead of one node called "gross revenue".
#: This is a presentation choice and only a presentation choice -- `--tree`
#: and `--json` show the real shape of the graph, and the hashes are computed
#: over the real shape either way.
FLATTEN_THROUGH = {"sum_regions"}


def _display_inputs(store: Store, fact: Fact) -> List[Fact]:
    out: List[Fact] = []
    for i in fact.inputs:
        child = store.get(i)
        if child.transform in FLATTEN_THROUGH:
            out.extend(store.get(j) for j in child.inputs)
        else:
            out.append(child)
    return out


def _currencies_under(store: Store, fact: Fact) -> set:
    return {store.get(i).metadata.get("currency", "")
            for i in fact.inputs} - {""}


def _rate_date_under(store: Store, fact: Fact) -> str:
    for i in fact.inputs:
        d = store.get(i).metadata.get("rate_date")
        if d:
            return d
    return ""


def sighting(store: Store, fact: Fact) -> Dict[str, str]:
    """The first recorded observation of this fact, or an empty dict."""
    obs = store.observations(fact.id)
    return obs[0] if obs else {}


def _seen_in(store: Store, fact: Fact) -> str:
    """Every file this exact claim has been read out of.

    More than one is normal and informative: it means the same number survived
    a re-export unchanged, which is precisely what you want to know during a
    restatement.
    """
    files = sorted({o.get("file", "?") for o in store.observations(fact.id)})
    return ", ".join(files)


def _source_provenance(store: Store, fact: Fact) -> Dict[str, str]:
    m = fact.metadata
    obs = sighting(store, fact)
    out = {
        "file": _seen_in(store, fact) or "(not recorded)",
        "row": obs.get("row", ""),
        "column": obs.get("column", ""),
        "file digest": obs.get("digest", ""),
    }
    for extra in ("sku", "order_id", "region", "currency", "effective_date",
                  "modified_by", "approval_ref", "rate_date", "order_date"):
        if extra in m:
            out[extra.replace("_", " ")] = m[extra] or "(blank)"
    return out


def _asserted_provenance(store: Store, fact: Fact) -> Dict[str, str]:
    m = fact.metadata
    obs = sighting(store, fact)
    return {
        "asserted by": m.get("actor", ""),
        "entry date": m.get("entry_date", ""),
        "stated reason": m.get("reason", ""),
        "stated evidence": m.get("evidence", ""),
        "transcribed in": "%s row %s" % (obs.get("file", "?"),
                                         obs.get("row", "?")),
        "upstream system": "none - this figure cannot be recomputed",
    }


def _read_time(store: Store, fact: Fact) -> str:
    """Wall-clock time is not in the hash (see core.py). It lives in the run
    journal, and this is where it gets joined back in for display."""
    digests = {o.get("digest") for o in store.observations(fact.id)}
    times = [r["ran_at"] for r in store.all_runs()
             if digests & set((r.get("sources") or {}).values())]
    return min(times) if times else ""


# ---------------------------------------------------------------------------
# The power-user view.
# ---------------------------------------------------------------------------

def build_tree(store: Store, fid: str, max_depth: int = 3):
    """A rich.Tree of the DAG, depth-limited because the full graph under a
    quarter's revenue is a few thousand nodes and nobody reads that."""
    from rich.tree import Tree

    def style(f: Fact) -> str:
        if f.kind == "asserted":
            return "yellow"
        if f.kind == "source":
            return "cyan"
        return "white"

    def line(f: Fact) -> str:
        mark = " [yellow]ASSERTED[/yellow]" if f.kind == "asserted" else ""
        return "[%s]%s[/%s]  [dim]%s[/dim]  [dim]%s[/dim]%s" % (
            style(f), usd(f.value), style(f), _component_label(f), f.short, mark)

    root = store.get(fid)
    tree = Tree(line(root))

    def walk(node, fact: Fact, depth: int):
        if depth >= max_depth:
            hidden = len(store.closure(fact.id)) - 1
            if hidden > 0:
                node.add("[dim]... %d more facts below (raise --depth)[/dim]" % hidden)
            return
        for i in fact.inputs:
            child = store.get(i)
            walk(node.add(line(child)), child, depth + 1)

    walk(tree, root, 0)
    return tree
