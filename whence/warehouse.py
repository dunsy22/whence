# SPDX-License-Identifier: Apache-2.0
"""Reconciling a warehouse: recompute the figure, then compare.

Whence's guarantee comes from tracked.py -- a transform takes Facts and returns
a Fact, so the arithmetic and the provenance are the same call and cannot drift
apart. SQL running inside a warehouse cannot be wrapped that way, and the
tempting shortcut is to read lineage back out of dbt's manifest instead. That
shortcut produces Facts asserting a computation nobody here performed, which is
an asserted value wearing a derived costume.

So this module does the honest version. It reads the same source rows the
warehouse read, runs them through the real transforms, and compares the answer
to what the warehouse published. The Whence DAG is genuinely computed by
Whence; agreement with the warehouse is recorded separately, as its own claim,
and it is checked on every run rather than assumed.

Three things are worth flagging to a reviewer:

  * **The close is not duplicated.** pipeline.close() runs unchanged over
    warehouse tables, because a table here is shaped like a SourceFile and says
    where its own cells came from. A second copy of the close would reintroduce
    one level up exactly the drift tracked.py exists to prevent.

  * **The digest is of the result set, not of a file.** A warehouse has no
    bytes to hash, so what gets pinned is the canonicalized rows actually read
    -- sorted, every cell a string, serialized by the section 3.3 rules. The
    table name, the warehouse, and the engine's own query id are coordinates
    and go in the observation, never in the hash, for the same reason a file's
    path does not (section 3.8).

  * **Nothing here is specific to DuckDB.** Every read goes through DB-API 2.0,
    which the Snowflake, BigQuery and Postgres drivers also speak, so pointing
    this at a real warehouse is a connection object, not a rewrite.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .core import CanonicalizationError, Store, dec
from .pipeline import close

__all__ = [
    "RESULT_FORM", "WarehouseTable", "Reconciliation", "ReconcileReport",
    "cell_text", "result_digest", "read_table", "read_figure",
    "load_mapping", "reconcile", "dbt_invocation", "connect",
]

#: Domain tag for a result-set digest. Distinct from FACT_FORM: this names a
#: set of rows that were read, not a claim that was made.
RESULT_FORM = "whence.result.v1"

_IDENT = re.compile(r"\A[A-Za-z_][A-Za-z0-9_]*\Z")


def _ident(name: str) -> str:
    """Identifiers are interpolated into SQL, so they are checked rather than
    trusted. Values never are -- those go through driver parameters."""
    if not isinstance(name, str) or not _IDENT.match(name):
        raise ValueError("not a usable SQL identifier: %r" % (name,))
    return name


# ---------------------------------------------------------------------------
# Cells.
# ---------------------------------------------------------------------------

def cell_text(value: Any) -> Optional[str]:
    """One warehouse cell as canonical text, or None for SQL NULL.

    NULL survives as None rather than becoming "". The two are different
    claims -- SPEC.md section 3.5 vector B turns on precisely that distinction
    -- and collapsing them here would let a missing value quietly mint a Fact
    asserting a blank one. A NULL that reaches a Fact should fail loudly.
    """
    if value is None:
        return None
    if isinstance(value, bool):          # before int: bool subclasses it
        return "true" if value else "false"
    if isinstance(value, float):
        raise CanonicalizationError(
            "a float reached the canonicalizer; cast the column to DECIMAL in "
            "the warehouse rather than rounding a double here"
        )
    if isinstance(value, Decimal):
        return format(value, "f")        # never scientific notation
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return value
    raise CanonicalizationError(
        "no canonical text form for %s" % type(value).__name__)


def result_digest(columns: Sequence[str],
                  rows: Sequence[Mapping[str, Any]]) -> str:
    """Pin the exact rows that were read.

    Sorted, because SQL without ORDER BY makes no promise about row order and
    a digest that changed with the engine's scheduling would pin nothing.
    Every cell as text, keys sorted, no insignificant whitespace -- the same
    rules as section 3.3, so another implementation can reproduce this.
    """
    cols = sorted(columns)

    def canon(row: Mapping[str, Any]) -> dict:
        return {c: cell_text(row[c]) for c in cols}

    def order(row: dict) -> str:
        return json.dumps(row, sort_keys=True, ensure_ascii=False,
                          separators=(",", ":"))

    doc = {
        "_form": RESULT_FORM,
        "columns": cols,
        "rows": sorted((canon(r) for r in rows), key=order),
    }
    return hashlib.sha256(
        json.dumps(doc, sort_keys=True, ensure_ascii=False,
                   separators=(",", ":")).encode("utf-8")
    ).hexdigest()


# ---------------------------------------------------------------------------
# Tables.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class WarehouseTable:
    """A result set, shaped like pipeline.SourceFile so the close cannot tell
    the difference."""

    name: str                   # the table it came from
    warehouse: str              # which warehouse, for the observation
    digest: str                 # sha256 of the rows actually read
    rows: List[dict]
    key: tuple = ()             # columns identifying a row, for coordinates
    query_id: str = ""          # the engine's own id for the read, if any

    def where(self, **match) -> dict:
        for row in self.rows:
            if all(row.get(k) == v for k, v in match.items()):
                return row
        raise KeyError("no row in %s matching %r" % (self.name, match))

    def row_key(self, row: Mapping[str, str]) -> str:
        """A row's identity. A warehouse has no line numbers, and a physical
        position would not be stable across a reload anyway -- the primary key
        is the thing that still means something tomorrow."""
        return ",".join("%s=%s" % (k, row.get(k, "")) for k in self.key)

    def coordinates(self, row: Mapping[str, str], column: str) -> dict:
        out = {
            "warehouse": self.warehouse,
            "table": self.name,
            "row": self.row_key(row),
            "column": column,
            "digest": self.digest,
        }
        if self.query_id:
            out["query_id"] = self.query_id
        return out


def read_table(conn, table: str, *, key: Sequence[str],
               warehouse: str = "warehouse",
               schema: Optional[str] = None) -> WarehouseTable:
    """Read a whole table through DB-API 2.0 and pin what came back."""
    qualified = ("%s.%s" % (_ident(schema), _ident(table)) if schema
                 else _ident(table))
    cursor = conn.cursor()
    cursor.execute("select * from %s" % qualified)
    columns = [d[0] for d in cursor.description]
    rows = [dict(zip(columns, values)) for values in cursor.fetchall()]
    digest = result_digest(columns, rows)

    # The close consumes text, the way the CSV reader hands it text. A NULL is
    # left as None so that minting a Fact from it raises rather than inventing
    # an empty string; see cell_text.
    as_text = [{c: cell_text(row[c]) for c in columns} for row in rows]

    return WarehouseTable(
        name=table, warehouse=warehouse, digest=digest, rows=as_text,
        key=tuple(key), query_id=_query_id(conn, cursor),
    )


def _query_id(conn, cursor) -> str:
    """The engine's own identifier for the read, when it has one.

    Snowflake exposes `sfqid`; most drivers expose nothing. It is
    corroboration, not proof -- the digest is what actually pins the rows --
    so its absence is not a problem worth raising.
    """
    for holder in (cursor, conn):
        found = getattr(holder, "sfqid", None) or getattr(holder, "query_id", None)
        if found:
            return str(found)
    return ""


def read_figure(conn, model: str, column: str,
                where: Optional[Mapping[str, Any]] = None) -> Optional[str]:
    """The warehouse's own answer for one reported figure."""
    sql = "select %s from %s" % (_ident(column), _ident(model))
    params: list = []
    if where:
        clauses = []
        for name, value in sorted(where.items()):
            clauses.append("%s = ?" % _ident(name))
            params.append(value)
        sql += " where " + " and ".join(clauses)

    cursor = conn.cursor()
    cursor.execute(sql, params)
    found = cursor.fetchall()
    if len(found) != 1:
        raise ValueError(
            "%s.%s matched %d rows, expected exactly 1 -- a reported figure "
            "must be a single number" % (model, column, len(found)))
    return cell_text(found[0][0])


# ---------------------------------------------------------------------------
# The mapping.
# ---------------------------------------------------------------------------

def load_mapping(path) -> dict:
    """Which tables hold the source rows, and which model columns are figures.

    JSON rather than YAML on purpose: the core package has three dependencies
    and a prototype adapter is not a good reason for a fourth.
    """
    mapping = json.loads(Path(path).read_text(encoding="utf-8"))
    for required in ("sources", "figures"):
        if required not in mapping:
            raise ValueError("mapping has no %r section" % required)
    for role in ("orders", "pricebook", "fx", "adjustments"):
        if role not in mapping["sources"]:
            raise ValueError("mapping has no source for %r" % role)
    return mapping


def connect(spec: str):
    """Open a DB-API connection from a mapping's `warehouse` string.

    Only `duckdb:<path>` is handled here, and that is the prototype's limit
    rather than the design's. reconcile() takes a connection and never asks
    what made it, so a real deployment builds its own with
    snowflake.connector, psycopg or the BigQuery client -- all of which speak
    DB-API 2.0 -- and hands it over.
    """
    scheme, _, rest = str(spec).partition(":")
    if scheme != "duckdb" or not rest:
        raise ValueError(
            "this build can only open a duckdb:<path> warehouse, not %r; "
            "build the connection yourself and pass it to reconcile()"
            % (spec,))
    import duckdb                                    # optional dependency
    return duckdb.connect(rest, read_only=True)


# ---------------------------------------------------------------------------
# Reconciliation.
# ---------------------------------------------------------------------------

@dataclass
class Reconciliation:
    """One reported figure, computed twice."""
    key: str
    label: str
    whence_value: str = ""
    whence_id: str = ""
    warehouse_value: Optional[str] = None
    source: str = ""

    @property
    def found(self) -> bool:
        return bool(self.whence_value) and self.warehouse_value is not None

    @property
    def agrees(self) -> bool:
        """Compared as decimals, not as text. A warehouse column's scale is a
        storage decision -- 4892582.47000000 and 4892582.47 are the same
        claim, and only a difference in the number should count as one."""
        return self.found and dec(self.whence_value) == dec(self.warehouse_value)

    @property
    def delta(self) -> Optional[Decimal]:
        if not self.found:
            return None
        return dec(self.warehouse_value) - dec(self.whence_value)

    def to_record(self) -> dict:
        return {
            "figure": self.key,
            "label": self.label,
            "whence": self.whence_value,
            "whence_id": self.whence_id,
            "warehouse": self.warehouse_value,
            "source": self.source,
            "agrees": self.agrees,
        }


@dataclass
class ReconcileReport:
    root: str = ""
    label: str = ""
    warehouse: str = ""
    figures: List[Reconciliation] = field(default_factory=list)
    sources: Dict[str, str] = field(default_factory=dict)
    dbt: Dict[str, str] = field(default_factory=dict)
    result: Any = None

    @property
    def agrees(self) -> bool:
        return bool(self.figures) and all(f.agrees for f in self.figures)

    @property
    def divergent(self) -> List[Reconciliation]:
        return [f for f in self.figures if not f.agrees]


def reconcile(store: Store, conn, mapping: Mapping[str, Any],
              label: str = "warehouse reconciliation",
              dbt_target=None) -> ReconcileReport:
    """Recompute the close from warehouse rows and compare it to the warehouse.

    The comparison is written to the run journal rather than to a Fact. It is a
    property of this run -- of two systems having been asked the same question
    at one moment -- and not a claim the figure itself makes, so it belongs
    where clocks live (section 3.8) and not in the hash.

    Because the record carries the figure's root id, `whence attest` sweeps it
    into the bundle: an auditor opening the .wbundle sees not only that the
    arithmetic holds, but that the warehouse independently produced the same
    number on the day it was exported.
    """
    warehouse = mapping.get("warehouse", "warehouse")
    src = mapping["sources"]

    def table(role: str) -> WarehouseTable:
        spec = src[role]
        return read_table(conn, spec["table"], key=spec.get("key", ()),
                          warehouse=warehouse, schema=spec.get("schema"))

    orders, pricebook = table("orders"), table("pricebook")
    fx, adjustments = table("fx"), table("adjustments")

    result = close(store, orders=orders, pricebook=pricebook, fx=fx,
                   adjustments=adjustments, label=label)

    by_key = {f.key: f for f in store.closure(result.net.id).values() if f.key}

    report = ReconcileReport(
        root=result.net.id, label=label, warehouse=warehouse, result=result,
        sources={t.name: t.digest
                 for t in (orders, pricebook, fx, adjustments)},
        dbt=dbt_invocation(dbt_target) if dbt_target else {},
    )

    for figure in mapping["figures"]:
        fact = by_key.get(figure["key"])
        report.figures.append(Reconciliation(
            key=figure["key"],
            label=figure.get("label", figure["key"]),
            source="%s.%s" % (figure["model"], figure["column"]),
            whence_value=fact.value if fact else "",
            whence_id=fact.id if fact else "",
            warehouse_value=read_figure(conn, figure["model"],
                                        figure["column"], figure.get("where")),
        ))

    store.record_run({
        "ran_at": result.ran_at,
        "label": label,
        "kind": "reconciliation",
        "root": report.root,
        "warehouse": warehouse,
        "sources": report.sources,
        "dbt": report.dbt,
        "agrees": report.agrees,
        "reconciliation": [f.to_record() for f in report.figures],
    })
    return report


def dbt_invocation(target_dir) -> Dict[str, str]:
    """Tie this reconciliation to the dbt run that produced the figures.

    Best effort by design: the artifacts are dbt's, they may be absent or from
    a version whose shape differs, and none of it is load-bearing. Whether the
    two systems agree is established by the digests and the recomputation
    above, not by anything read here.
    """
    path = Path(target_dir) / "run_results.json"
    if not path.exists():
        return {}
    try:
        artifact = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    meta = artifact.get("metadata", {}) or {}
    out = {k: str(v) for k, v in (
        ("invocation_id", meta.get("invocation_id")),
        ("dbt_version", meta.get("dbt_version")),
        ("generated_at", meta.get("generated_at")),
    ) if v}
    if artifact.get("results"):
        out["models_run"] = str(len(artifact["results"]))
    return out
