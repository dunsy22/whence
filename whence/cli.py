# SPDX-License-Identifier: Apache-2.0
"""The command line. Eight commands, plus the demo.

Every command's output is written to be read by someone who has never seen the
tool and does not want to learn what a Merkle DAG is. Ids are shown truncated
and prefixed `whence:` so they read as references rather than as noise; any
command that takes an id will accept an unambiguous prefix.

Output is deliberately ASCII. A Windows console falls back to cp1252 the moment
output is piped to a file, and a demo that prints mojibake in front of a
finance team has lost the room before it starts.

The two shared renderers -- render_narrative and render_diff -- live here and
are also what `whence demo` calls, so the walkthrough cannot drift away from
what the real commands print.
"""

from __future__ import annotations

import json
import sys
from decimal import Decimal
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table
from rich.text import Text

from . import __version__
from .attest import export_bundle, read_statement, verify_bundle, verify_local
from .core import Store
from .diff import (describe, detail_line, detail_note, diff,
                   headline_groups)
from .explain import build_tree, explain, usd

console = Console(highlight=False)

OK = "[green]OK[/green]"
WARN = "[yellow]![/yellow]"
BAD = "[red]X[/red]"


def _store(ctx) -> Store:
    return Store(ctx.obj["store"]).init()


def _resolve(store: Store, ref: str) -> str:
    try:
        return store.resolve(ref)
    except KeyError as exc:
        raise click.ClickException(str(exc).strip("'"))


def short(fid: str) -> str:
    return "whence:%s" % fid[:12]


def prog() -> str:
    """However this program was actually invoked.

    `whence` when run from the console script, `python -m whence` when run as a
    module. Anything this tool prints as a suggested next command has to be
    something the reader can paste and have work -- and the bare name does not
    always work: `whence` is a shell builtin in zsh and ksh (it is a `which`
    lookup), and builtins win over programs on PATH. A macOS user typing
    `whence explain ...` gets silence, not an error.

    So the tool echoes back the invocation that just worked for this reader
    rather than guessing on their behalf.
    """
    try:
        return click.get_current_context().find_root().info_name or "whence"
    except RuntimeError:          # called outside a click context
        return "whence"


def _signed(amount) -> str:
    return ("+" if amount >= 0 else "-") + usd(abs(amount))


def _plain(markup: str) -> str:
    return Text.from_markup(markup).plain


def _wrap(text: str, indent: int = 4, bullet: str = "") -> str:
    """Hard-wrap a paragraph at the console width, minus a safety margin so
    rich does not then wrap it a second time and strand a word in column 0."""
    import textwrap

    prefix = " " * indent
    # The margin must cover the bullet, or a three-character "OK " bullet
    # pushes each line one column past the edge.
    body = textwrap.fill(text, width=max(40, min(console.width, 96) - indent
                                         - max(2, len(_plain(bullet)))))
    lines = body.splitlines()
    if not lines:
        return ""
    out = [prefix + bullet + lines[0]]
    pad = prefix + (" " * len(_plain(bullet)))
    out.extend(pad + line for line in lines[1:])
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Shared renderers.
# ---------------------------------------------------------------------------

def _component_table(indent: int = 2) -> Table:
    """A borderless table. Columns beat manual %-padding here: the note column
    folds instead of spilling past the right edge of an 80-column terminal."""
    table = Table(box=None, show_header=False, pad_edge=False,
                  padding=(0, 1, 0, 0))
    table.add_column(width=indent + 2, no_wrap=True)     # the arrow
    table.add_column(width=11, no_wrap=True)             # label
    table.add_column(justify="right", width=14, no_wrap=True)
    table.add_column(width=1, no_wrap=True)              # warning flag
    table.add_column(style="dim", overflow="fold")       # the clause
    return table


def render_narrative(console, store, fid, show_findings: bool = True,
                     indent: int = 2) -> None:
    """The default `explain` view: a headline, a sentence, and a short list."""
    exp = explain(store, fid)
    pad = " " * indent

    console.print("%s[bold]%s[/bold] = [bold cyan]%s[/bold cyan]   [dim]%s[/dim]"
                  % (pad, exp.label, exp.display, short(exp.id)))
    console.print()
    console.print(_wrap(exp.summary, indent=indent + 2))
    console.print()

    if exp.components:
        table = _component_table(indent)
        for c in exp.components:
            colour = "yellow" if c.unverifiable else "white"
            table.add_row(
                "%s[dim]->[/dim]" % pad,
                "[%s]%s[/%s]" % (colour, c.label, colour),
                "[%s]%s[/%s]" % (colour, c.display, colour),
                "[yellow]![/yellow]" if c.unverifiable else "",
                c.note,
            )
            if c.warning:
                table.add_row("", "", "", "", "[yellow]%s[/yellow]" % c.warning)
        console.print(table)
        console.print()

    if exp.provenance:
        # A table rather than %-padding: a sha256 digest is 64 characters and
        # would otherwise wrap back to column zero on a narrow terminal.
        detail = Table(box=None, show_header=False, pad_edge=False,
                       padding=(0, 1, 0, 0))
        detail.add_column(width=indent + 2, no_wrap=True)
        detail.add_column(style="dim", width=15, no_wrap=True)
        detail.add_column(overflow="fold")
        for key, value in exp.provenance.items():
            detail.add_row("", key, value)
        if exp.read_at:
            detail.add_row("", "first read", exp.read_at)
        console.print(detail)
        console.print()

    if show_findings:
        for finding in exp.findings:
            console.print(_wrap(finding, indent=indent + 2,
                                bullet="[yellow]![/yellow] "))
            console.print()
        # Green, and after the warnings. Corroboration is the opposite of a
        # finding and must not be printed as though it were one.
        for note in exp.corroboration:
            console.print(_wrap(note, indent=indent + 2,
                                bullet="[green]OK[/green] "))
            console.print()


def render_diff(console, report, indent: int = 2) -> None:
    """The `diff` view: causes in business language, with exact amounts."""
    pad = " " * indent

    if report.identical:
        console.print("%s[bold green]These are the same figure.[/bold green]"
                      % pad)
        console.print("%s[dim]%s - one id, so the same inputs and the same "
                      "logic.[/dim]" % (pad, short(report.a.id)))
        console.print()
        return

    console.print("%sThese two figures differ by [bold]%s[/bold]."
                  % (pad, usd(abs(report.delta))))
    console.print()
    console.print("%s  [dim]%-7s %15s   %s[/dim]"
                  % (pad, "first", usd(report.a.value), short(report.a.id)))
    console.print("%s  [dim]%-7s %15s   %s[/dim]"
                  % (pad, "second", usd(report.b.value), short(report.b.id)))
    console.print()

    blocks = headline_groups(report)
    if len(blocks) == 1:
        console.print("%s[dim]The entire difference traces to one cause:[/dim]"
                      % pad)
    else:
        console.print("%s[dim]The entire difference traces to %d independent "
                      "causes:[/dim]" % (pad, len(blocks)))
    console.print()

    for n, (kind, groups) in enumerate(blocks, start=1):
        total = sum((g.delta for g in groups if g.delta is not None),
                    Decimal("0"))
        console.print("%s[bold]%d[/bold]  %s" % (pad, n, describe(kind, groups)))

        table = Table(box=None, show_header=False, pad_edge=False,
                      padding=(0, 1, 0, 0))
        table.add_column(width=indent + 3, no_wrap=True)
        table.add_column(style="dim", overflow="fold")
        table.add_column(justify="right", width=13, no_wrap=True)
        for group in sorted(groups, key=lambda g: g.bucket)[:8]:
            amount = ("[dim]%s[/dim]" % _signed(group.delta)
                      if group.delta is not None else "[dim]?[/dim]")
            table.add_row("", detail_line(kind, group), amount)
            note = detail_note(kind, group)
            if note:
                table.add_row("", "  [dim]%s[/dim]" % note, "")
        if len(groups) > 8:
            table.add_row("", "... and %d more" % (len(groups) - 8), "")
        table.add_row("", "", "[bold]%s[/bold]" % _signed(total))
        console.print(table)
        console.print()

    if report.residual:
        console.print(_wrap(
            "%s of the difference could not be attributed to a single cause."
            % usd(report.residual), indent=indent,
            bullet="[yellow]![/yellow] "))
    else:
        console.print(_wrap(
            "Everything else is identical: %d of the %d facts behind these two "
            "figures are the same facts, and the causes above add up to the "
            "difference exactly."
            % (report.shared_facts, report.total_facts), indent=indent))
    console.print()


def render_workbook(console, store, result, indent: int = 2) -> None:
    """The `ingest-workbook` view: the figure, then what the workbook did."""
    pad = " " * indent
    head = result.headline

    console.print("%s[dim]%s  |  %s[/dim]" % (pad, result.book, result.cell))
    console.print("%s[bold]%s[/bold]" % (pad, head.label))
    console.print("%s[bold cyan]%s[/bold cyan]   [dim]%s[/dim]"
                  % (pad, usd(head.value), short(head.id)))
    console.print()
    console.print("%s[dim]%s formula cells evaluated | %s facts behind this "
                  "figure[/dim]" % (pad, "{:,}".format(result.formula_cells),
                                    "{:,}".format(len(store.closure(head.id)))))
    console.print()

    ref = result.reference
    if ref is not None:
        console.print(_wrap(
            "The Orders, Pricebook, FX and Adjustments tabs, run through the "
            "reference close:", indent=indent))
        console.print("%s  %s   [dim]%s[/dim]"
                      % (pad, usd(ref.net.value), short(ref.net.id)))
        twins = sorted({r.get("label", "") for r in store.all_runs()
                        if r.get("root") == ref.net.id
                        and r.get("kind") != "workbook"
                        and not r.get("label", "").startswith(
                            "reference close over")})
        if twins:
            console.print(_wrap(
                "The same id as the close run '%s' -- the pasted tabs hold "
                "exactly the claims the source files hold, so they land on the "
                "same facts." % twins[0], indent=indent + 2,
                bullet="[green]OK[/green] "))
        console.print()

    for f in result.overrides:
        d = f.detail
        gives = d.get("formula gives")
        text = ("%s holds a typed %s where every neighbouring row has a "
                "formula. The formula would give %s. Last saved by %s."
                % (f.cell, usd(d["typed"]),
                   usd(gives) if gives else "a value this build cannot "
                   "compute", d.get("author", "an unknown author")))
        console.print(_wrap(text, indent=indent, bullet="[yellow]![/yellow] "))
        console.print()

    one = len(result.overrides) == 1
    restored_matches = (ref is not None and result.restored_value is not None
                        and result.restored_value == ref.net.value)
    if result.override_effect is not None and not restored_matches:
        console.print(_wrap(
            "With the typed-over %s put back, the workbook reports %s: the "
            "typed %s moved the headline by %s."
            % ("formula" if one else "formulas", usd(result.restored_value),
               "number" if one else "numbers",
               _signed(result.override_effect)), indent=indent + 2))
        console.print()

    for f in result.cache_mismatches:
        console.print(_wrap(
            "%s. The saved value is stale or was edited; the figure is "
            "recorded as the workbook claims it, so verification will reject "
            "it." % f.message, indent=indent, bullet="[red]X[/red] "))
        console.print()

    for f in result.opaque:
        console.print(_wrap(
            "%s. Its lineage is kept, but it is never reported as verified."
            % f.message, indent=indent, bullet="[yellow]![/yellow] "))
        console.print()

    if ref is not None:
        delta = result.reference_delta
        if delta == 0:
            console.print(_wrap(
                "The workbook agrees with the reference close to the cent.",
                indent=indent, bullet="[green]OK[/green] "))
        elif restored_matches:
            console.print(_wrap(
                "Put the %s back and the workbook reports %s, agreeing with "
                "the reference close to the cent. The typed %s moved the "
                "headline by %s, and %s the whole difference."
                % ("formula" if one else "formulas",
                   usd(result.restored_value),
                   "number" if one else "numbers",
                   _signed(result.override_effect),
                   "is" if one else "are"),
                indent=indent, bullet="[green]OK[/green] "))
        else:
            console.print(_wrap(
                "The workbook differs from the reference close by %s, and the "
                "findings above do not account for all of it."
                % _signed(delta), indent=indent,
                bullet="[yellow]![/yellow] "))
        console.print()


def _ingest(store, path, cell=None, label=None):
    """ingest_workbook, with the errors a user can act on turned into messages."""
    try:
        from .workbook import ingest_workbook
        return ingest_workbook(store, path, cell=cell, label=label)
    except ModuleNotFoundError as exc:
        if exc.name == "openpyxl":
            raise click.ClickException(
                "reading workbooks needs openpyxl: "
                "pip install 'whence[workbook]'")
        raise
    except ValueError as exc:
        raise click.ClickException(str(exc))


def _is_workbook(ref: str) -> bool:
    return ref.lower().endswith((".xlsx", ".xlsm")) and Path(ref).is_file()


def render_verification(console, report) -> None:
    console.print()
    console.print("  Verifying [dim]%s[/dim]  %s = [bold]%s[/bold]"
                  % (short(report.root), report.label,
                     usd(report.value) if report.value else "?"))
    console.print("  [dim]from %s[/dim]" % report.origin)
    console.print()
    console.print("    %s  %4d records intact       [dim]every id re-derived "
                  "from its own contents[/dim]" % (OK, report.hashes_ok))
    console.print("    %s  %4d figures recomputed   [dim]transforms re-run "
                  "against their inputs[/dim]" % (OK, report.recomputed))
    console.print("    %s  %4d source values        [dim]read from files, "
                  "digests recorded[/dim]" % (OK, report.sources))
    if report.asserted:
        console.print("    %s  %4d asserted values      [dim]no upstream "
                      "system; cannot be recomputed[/dim]"
                      % (WARN, len(report.asserted)))
        for fact in report.asserted:
            console.print('         [yellow]%s  %s  %s - "%s"[/yellow]'
                          % (fact.metadata.get("adj_id", fact.short),
                             usd(fact.value), fact.metadata.get("actor", "?"),
                             fact.metadata.get("reason", "")))
    console.print()

    # A broken bundle can fail on hundreds of facts at once; the first handful
    # tell you what went wrong and the rest are noise.
    for failure in report.failures[:8]:
        console.print("    %s  %s" % (BAD, failure))
    if len(report.failures) > 8:
        console.print("    %s  ... and %d more"
                      % (BAD, len(report.failures) - 8))
    if report.failures:
        console.print()

    if report.ok:
        console.print("  [bold green]VERIFIED[/bold green]  [dim]every derived "
                      "figure was re-run and matched.[/dim]")
        if report.asserted:
            console.print("  [dim]The asserted values above are recorded and "
                          "unaltered, but nothing[/dim]")
            console.print("  [dim]can confirm they are right.[/dim]")
        if report.origin.startswith("bundle") and not report.signed:
            console.print("  [dim]This bundle is unsigned: the hashes prove "
                          "what it says and that[/dim]")
            console.print("  [dim]nothing has been altered, but not who "
                          "produced it.[/dim]")
    else:
        console.print("  [bold red]FAILED[/bold red]  [dim]%d problem(s) "
                      "above.[/dim]" % len(report.failures))
    console.print()


# ---------------------------------------------------------------------------

@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.option("--store", "store_path", default=".whence", show_default=True,
              help="Where the fact store lives.")
@click.version_option(__version__, prog_name="whence")
@click.pass_context
def main(ctx, store_path):
    """Whence -- content-addressed provenance for business figures.

    Every number this tool produces can name its sources, and any number it has
    produced can be re-derived from them by anyone holding the record.
    """
    ctx.ensure_object(dict)
    ctx.obj["store"] = store_path


# ---------------------------------------------------------------------------

@main.command()
@click.option("--orders", type=click.Path(exists=True, dir_okay=False),
              help="ERP order export to close from.")
@click.option("--pricebook", type=click.Path(exists=True, dir_okay=False),
              help="Pricebook to recognize revenue at.")
@click.option("--fx", type=click.Path(exists=True, dir_okay=False),
              help="Daily FX rate file.")
@click.option("--adjustments", type=click.Path(exists=True, dir_okay=False),
              help="Manual journal entries.")
@click.option("--label", default="Q3 close", help="A name for this run.")
@click.option("--report", "report_path", type=click.Path(dir_okay=False),
              help="Also write a self-contained HTML board page.")
@click.pass_context
def run(ctx, orders, pricebook, fx, adjustments, label, report_path):
    """Execute the quarter close and record every step."""
    from .pipeline import run_close

    store = _store(ctx)
    before = len(store.all_ids())
    result = run_close(store, orders_path=orders, pricebook_path=pricebook,
                       fx_path=fx, adjustments_path=adjustments, label=label)
    written = len(store.all_ids()) - before

    console.print()
    console.print("  [dim]Halloway Instruments  |  FY2026 Q3[/dim]")
    console.print("  [bold]%s[/bold]" % result.net.label)
    console.print("  [bold cyan]%s[/bold cyan]   [dim]%s[/dim]"
                  % (usd(result.net.value), short(result.net.id)))
    console.print()

    table = Table(box=None, pad_edge=False, show_header=False)
    table.add_column(style="dim", width=12)
    table.add_column(justify="right", width=15)
    table.add_column(style="dim", overflow="fold")
    for region in result.regions:
        table.add_row(region.metadata["region"], usd(region.value),
                      "%s orders" % region.metadata["order_count"])
    for adj in result.adjustments:
        table.add_row("[yellow]%s[/yellow]" % adj.metadata["adj_id"],
                      "[yellow]%s[/yellow]" % usd(adj.value),
                      "[yellow]asserted by %s[/yellow]" % adj.metadata["actor"])
    console.print(table)
    console.print()
    console.print("  [dim]%d order lines | %d SKUs | %d facts recorded "
                  "(%d new)[/dim]"
                  % (result.order_count, result.sku_count,
                     len(store.all_ids()), written))
    console.print()
    console.print("  [dim]Inputs[/dim]")
    for name, digest in sorted(result.sources.items()):
        console.print("    [dim]%-26s sha256 %s[/dim]" % (name, digest[:16]))
    console.print()
    console.print("  [dim]Ask it anything:[/dim]  %s explain %s"
                  % (prog(), result.net.id[:12]))
    console.print()

    if report_path:
        from .report import write_report
        path = write_report(store, result.net.id, report_path, result=result)
        console.print("  Board page written to [bold]%s[/bold]" % path)
        console.print("  [dim]Self-contained; open it with a double-click.[/dim]")
        console.print()


# ---------------------------------------------------------------------------

@main.command(name="explain")
@click.argument("ref")
@click.option("--tree", "as_tree", is_flag=True, help="Show the full DAG.")
@click.option("--json", "as_json", is_flag=True, help="Machine-readable form.")
@click.option("--depth", default=3, show_default=True,
              help="How deep --tree descends.")
@click.pass_context
def explain_cmd(ctx, ref, as_tree, as_json, depth):
    """Walk a figure back to the raw sources that produced it."""
    store = _store(ctx)
    fid = _resolve(store, ref)

    if as_json:
        click.echo(json.dumps(explain(store, fid).to_dict(), indent=2))
        return

    if as_tree:
        console.print()
        console.print(build_tree(store, fid, max_depth=depth))
        console.print()
        return

    console.print()
    render_narrative(console, store, fid)


# ---------------------------------------------------------------------------

@main.command()
@click.argument("ref")
@click.pass_context
def verify(ctx, ref):
    """Recompute a figure from its sources and confirm it still holds."""
    store = _store(ctx)
    report = verify_local(store, _resolve(store, ref))
    render_verification(console, report)
    if not report.ok:
        sys.exit(1)


# ---------------------------------------------------------------------------

@main.command(name="diff")
@click.argument("ref_a")
@click.argument("ref_b")
@click.option("--json", "as_json", is_flag=True, help="Machine-readable form.")
@click.pass_context
def diff_cmd(ctx, ref_a, ref_b, as_json):
    """Explain why two figures differ, in business terms.

    Each side is a figure id -- or an .xlsx file, in which case its headline
    is read in and compared.
    """
    store = _store(ctx)
    ids = []
    for ref in (ref_a, ref_b):
        if _is_workbook(ref):
            ids.append(_ingest(store, ref).headline.id)
        else:
            ids.append(_resolve(store, ref))
    report = diff(store, ids[0], ids[1])

    if as_json:
        click.echo(json.dumps(_diff_json(report), indent=2))
        return

    console.print()
    render_diff(console, report)


def _diff_json(report) -> dict:
    return {
        "a": report.a.id,
        "b": report.b.id,
        "delta": str(report.delta),
        "shared_facts": report.shared_facts,
        "total_facts": report.total_facts,
        "residual": str(report.residual),
        "reconciles": report.reconciles,
        "causes": [
            {
                "kind": g.kind,
                "bucket": g.bucket,
                "delta": str(g.delta) if g.delta is not None else None,
                "changes": [
                    {"key": c.key, "label": c.label, "before": c.before,
                     "after": c.after, "detail": c.detail}
                    for c in g.causes
                ],
            }
            for g in report.groups
        ],
    }


# ---------------------------------------------------------------------------

@main.command()
@click.argument("ref")
@click.option("-o", "--output", "out", default="bundle.wbundle",
              show_default=True, type=click.Path(dir_okay=False))
@click.pass_context
def attest(ctx, ref, out):
    """Export a portable bundle that verifies without this machine."""
    store = _store(ctx)
    info = export_bundle(store, _resolve(store, ref), out)

    console.print()
    console.print("  Attested [bold]%s[/bold]  [dim]%s[/dim]"
                  % (usd(info["value"]), short(info["root"])))
    console.print()
    console.print("    [dim]%-12s[/dim] %s" % ("bundle", info["path"]))
    console.print("    [dim]%-12s[/dim] %d (the complete closure)"
                  % ("facts", info["facts"]))
    console.print("    [dim]%-12s[/dim] %.0f KB" % ("size", info["bytes"] / 1024))
    console.print("    [dim]%-12s[/dim] DSSE envelope, in-toto Statement v1"
                  % "format")
    console.print()
    console.print("  [dim]Hand this to anyone. It verifies with no access to "
                  "the ERP, the[/dim]")
    console.print("  [dim]pricebook, or this machine:[/dim]")
    console.print("    %s verify-bundle %s" % (prog(), info["path"]))
    console.print()


@main.command(name="verify-bundle")
@click.argument("path", type=click.Path(exists=True, dir_okay=False))
@click.option("--dump", is_flag=True, help="Print the decoded statement.")
def verify_bundle_cmd(path, dump):
    """Verify a bundle with no access to source systems."""
    if dump:
        statement, _signatures = read_statement(path)
        click.echo(json.dumps(statement, indent=2))
        return

    report = verify_bundle(path)
    render_verification(console, report)

    # report.source_files was carried out of the statement parsed during
    # verification. DSSE is explicit that an implementation must not re-open an
    # envelope after verifying it to pull the payload back out, so we do not.
    if report.source_files and report.ok:
        console.print("  [dim]Built from files this machine has never "
                      "seen:[/dim]")
        for name, digest in sorted(report.source_files.items()):
            console.print("    [dim]%-26s sha256 %s[/dim]" % (name, digest[:16]))
        console.print()

    if not report.ok:
        sys.exit(1)


# ---------------------------------------------------------------------------

@main.command(name="reconcile")
@click.option("--mapping", "mapping_path", default="warehouse/mapping.json",
              show_default=True, type=click.Path(exists=True, dir_okay=False),
              help="Which tables hold the source rows, and which model "
                   "columns are reported figures.")
@click.option("--dbt-target", "dbt_target", default=None,
              type=click.Path(file_okay=False),
              help="A dbt target/ directory, to record which invocation "
                   "produced the figures.")
@click.option("--label", default="warehouse reconciliation",
              help="A name for this run.")
@click.pass_context
def reconcile_cmd(ctx, mapping_path, dbt_target, label):
    """Recompute a warehouse's figures and check they agree."""
    from .warehouse import connect, load_mapping, reconcile

    store = _store(ctx)
    mapping = load_mapping(mapping_path)
    conn = connect(mapping["warehouse"])
    report = reconcile(store, conn, mapping, label=label,
                       dbt_target=dbt_target)

    console.print()
    console.print("  Reconciling [dim]%s[/dim]" % report.warehouse)
    console.print("  [dim]%d source tables read, %d figures recomputed[/dim]"
                  % (len(report.sources), len(report.figures)))
    console.print()

    table = Table(box=None, pad_edge=False, show_header=True, header_style="dim")
    table.add_column("  ", width=4, no_wrap=True)
    table.add_column("figure")
    table.add_column("warehouse", justify="right")
    table.add_column("whence", justify="right")
    table.add_column("delta", justify="right")
    for figure in report.figures:
        delta = figure.delta
        table.add_row(
            "  " + (OK if figure.agrees else BAD),
            figure.label,
            usd(figure.warehouse_value) if figure.found else "[red]missing[/red]",
            usd(figure.whence_value) if figure.whence_value
            else "[red]not computed[/red]",
            "-" if figure.agrees else
            ("?" if delta is None else "[red]%s[/red]" % _signed(delta)),
        )
    console.print(table)
    console.print()

    for name, digest in sorted(report.sources.items()):
        console.print("    [dim]%-20s sha256 %s[/dim]" % (name, digest[:16]))
    if report.dbt.get("invocation_id"):
        console.print("    [dim]%-20s %s[/dim]"
                      % ("dbt invocation", report.dbt["invocation_id"]))
    console.print()

    if report.agrees:
        console.print("  [bold green]AGREED[/bold green]  [dim]every figure "
                      "recomputed from the warehouse's own rows[/dim]")
        console.print("  [dim]matched what the warehouse published.[/dim]")
        console.print()
        console.print("  %s  [dim]is the reconciled figure[/dim]"
                      % short(report.root))
        console.print()
        console.print("  [dim]Ask it anything:  %s explain %s[/dim]"
                      % (prog(), report.root[:12]))
    else:
        console.print("  [bold red]DIVERGED[/bold red]  [dim]%d of %d figures "
                      "do not match.[/dim]"
                      % (len(report.divergent), len(report.figures)))
        console.print("  [dim]The two systems were given the same rows and "
                      "produced different[/dim]")
        console.print("  [dim]answers. One of them is wrong and the "
                      "difference is above.[/dim]")
    console.print()

    if not report.agrees:
        sys.exit(1)


# ---------------------------------------------------------------------------

@main.command(name="ingest-workbook")
@click.argument("path", type=click.Path(exists=True, dir_okay=False))
@click.option("--cell", default=None,
              help="The figure to follow, e.g. 'Summary!B10'. Defaults to "
                   "the cell the workbook names Headline.")
@click.option("--label", default=None, help="A name for this run.")
@click.pass_context
def ingest_workbook_cmd(ctx, path, cell, label):
    """Record a spreadsheet's figure, with the lineage of its formulas."""
    store = _store(ctx)
    result = _ingest(store, path, cell=cell, label=label)

    console.print()
    render_workbook(console, store, result)
    console.print("  [dim]Ask it anything:[/dim]  %s explain %s"
                  % (prog(), result.headline.id[:12]))
    console.print()


# ---------------------------------------------------------------------------

@main.command()
@click.option("--no-pause", is_flag=True,
              help="Run straight through without waiting for a keypress.")
@click.pass_context
def demo(ctx, no_pause):
    """Walk the scripted ten-minute demo."""
    from .demo import run_demo
    run_demo(console, store_path=ctx.obj["store"], pause=not no_pause)


if __name__ == "__main__":  # pragma: no cover
    main()
