# SPDX-License-Identifier: Apache-2.0
"""The command line. Six commands, plus the demo.

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


def _signed(amount) -> str:
    return ("+" if amount >= 0 else "-") + usd(abs(amount))


def _plain(markup: str) -> str:
    return Text.from_markup(markup).plain


def _wrap(text: str, indent: int = 4, bullet: str = "") -> str:
    """Hard-wrap a paragraph at the console width, minus a safety margin so
    rich does not then wrap it a second time and strand a word in column 0."""
    import textwrap

    prefix = " " * indent
    body = textwrap.fill(text, width=max(40, min(console.width, 96) - indent - 2))
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
    console.print("  [dim]Ask it anything:[/dim]  whence explain %s"
                  % result.net.id[:12])
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
    """Explain why two figures differ, in business terms."""
    store = _store(ctx)
    report = diff(store, _resolve(store, ref_a), _resolve(store, ref_b))

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
    console.print("    whence verify-bundle %s" % info["path"])
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
