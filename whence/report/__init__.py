# SPDX-License-Identifier: Apache-2.0
"""Render the board page: one HTML file, no external anything.

The page is not a viewer for Whence. It is a board packet page that happens to
be inspectable -- the figures are the point, and the provenance is there when
somebody reaches for it. That ordering is deliberate: the moment a financial
document looks like a developer tool, the audience it was built for stops
reading it.

Everything embedded here is computed. The commentary paragraph is assembled
from the same Explanation the terminal prints, so the page and `whence explain`
cannot tell two different stories.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ..core import Store
from ..explain import explain, usd

HERE = Path(__file__).resolve().parent

ORG = "Halloway Instruments"
PERIOD = "FY2026 Q3"


def write_report(store: Store, root: str, path: Path | str,
                 result=None) -> Path:
    path = Path(path)
    path.write_text(render_report(store, root, result=result), encoding="utf-8")
    return path


def render_report(store: Store, root: str, result=None) -> str:
    from jinja2 import Template

    top = explain(store, root)
    closure = store.closure(root)

    regions, adjustments, gross = [], [], None
    for component in top.components:
        fact = store.get(component.id)
        if fact.transform == "sum_by_region":
            regions.append({
                "name": fact.metadata.get("region", component.label),
                "orders": fact.metadata.get("order_count",
                                            str(len(fact.inputs))),
                "display": component.display,
                "id": fact.id,
            })
        elif fact.kind == "asserted":
            adjustments.append({
                "id": fact.id,
                "display": component.display,
                "actor": fact.metadata.get("actor", ""),
                "reason": fact.metadata.get("reason", component.label),
            })

    # The narrative flattens the structural `sum_regions` node away, so find it
    # on the record rather than in the components list.
    for fid in store.get(root).inputs:
        candidate = store.get(fid)
        if candidate.transform == "sum_regions":
            gross = {"id": candidate.id, "display": usd(candidate.value)}
    if gross is None:                       # a figure with no regional rollup
        gross = {"id": root, "display": top.display}

    # Explanations for the figures a reader can click straight from the page.
    # Deeper facts get a record view rendered client-side; precomputing all of
    # them would multiply the page size for pages nobody opens.
    interesting = [root, gross["id"]] + [r["id"] for r in regions] \
        + [a["id"] for a in adjustments]
    explanations = {}
    for fid in interesting:
        if fid in closure:
            explanations[fid] = explain(store, fid).to_dict()

    observations = {fid: store.observations(fid) for fid in closure
                    if store.observations(fid)}

    data = {
        "root": root,
        "facts": {fid: fact.to_record() for fid, fact in closure.items()},
        "explanations": explanations,
        "observations": observations,
    }

    sources = sorted((result.sources if result else _sources(store, closure)).items())

    template = Template((HERE / "template.html").read_text(encoding="utf-8"),
                        autoescape=True)
    return template.render(
        org=ORG,
        period=PERIOD,
        headline={"label": top.label, "display": top.display, "id": top.id},
        regions=regions,
        adjustments=adjustments,
        gross=gross,
        findings=top.findings,
        commentary=_commentary(top, regions, adjustments),
        order_count=sum(int(r["orders"]) for r in regions),
        fact_count=len(closure),
        generated=datetime.now(timezone.utc).replace(
            microsecond=0).isoformat().replace("+00:00", "Z"),
        sources=sources,
        css=(HERE / "report.css").read_text(encoding="utf-8"),
        js=(HERE / "verify.js").read_text(encoding="utf-8"),
        # Escaping "<" as < keeps the payload from ever terminating the
        # surrounding <script> block, whatever ends up in a label or a reason.
        # It is a legal JSON escape, so the parse on the other side is
        # unaffected. The three inlined blobs are marked | safe in the
        # template; everything else on the page is autoescaped.
        data_json=json.dumps(data, separators=(",", ":")).replace("<", "\\u003c"),
    )


def _commentary(top, regions, adjustments) -> str:
    """The paragraph a controller would write, assembled from the facts."""
    biggest = max(regions, key=lambda r: len(r["display"])) if regions else None
    parts = [
        "%s for %s is %s." % (top.label, PERIOD, top.display),
        "It consolidates %d regional subtotals covering %d orders"
        % (len(regions), sum(int(r["orders"]) for r in regions)),
    ]
    if biggest:
        parts[-1] += ", translated to USD at the quarter-end closing rate"
    parts[-1] += "."
    if adjustments:
        parts.append(
            "%d manual journal %s %s applied after consolidation and %s "
            "marked below as asserted: %s recorded and unaltered, but %s no "
            "upstream system to recompute %s from."
            % (len(adjustments),
               "entry" if len(adjustments) == 1 else "entries",
               "was" if len(adjustments) == 1 else "were",
               "is" if len(adjustments) == 1 else "are",
               "it is" if len(adjustments) == 1 else "they are",
               "it has" if len(adjustments) == 1 else "they have",
               "it" if len(adjustments) == 1 else "them"))
    return " ".join(parts)


def _sources(store: Store, closure) -> dict:
    out = {}
    for run in store.all_runs():
        if run.get("root") in closure:
            out.update(run.get("sources") or {})
    return out
