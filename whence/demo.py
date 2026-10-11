# SPDX-License-Identifier: Apache-2.0
"""The scripted walkthrough.

Eight beats, in the order that makes the argument. Nothing here is staged:
every number printed is computed during the run, every id is the real hash of
the real record, and the two analyst scripts in demo/ are executed as actual
subprocesses in actual separate directories. If the pipeline broke, this demo
would break with it -- which is the only way a demo of a provenance system is
worth watching.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

from .attest import export_bundle, verify_local
from .core import Store
from .diff import describe, detail_line, diff, headline_groups
from .explain import explain, usd
from .pipeline import DATA, run_close

REPO = Path(__file__).resolve().parent.parent


def run_demo(console, store_path: str = ".whence", pause: bool = True) -> None:
    from .cli import _wrap, prog, render_diff, render_narrative, short

    def beat(n: int, title: str, *framing: str) -> None:
        console.print()
        # characters="-" rather than rich's default box-drawing rule: a
        # Windows console falls back to cp1252 and raises on U+2500.
        console.rule("[dim]%d / 8[/dim]  [bold]%s[/bold]" % (n, title),
                     align="left", style="dim", characters="-")
        console.print()
        for line in framing:
            console.print(_wrap(line, indent=2))
        console.print()

    def hold() -> None:
        if pause:
            console.print()
            console.input("  [dim]press enter[/dim] ")

    store = Store(store_path).init()

    console.print()
    console.print("  [bold]Whence[/bold] [dim]- where did this number come "
                  "from?[/dim]")
    console.print("  [dim]Halloway Instruments is closing FY2026 Q3. Nothing "
                  "below is pre-recorded.[/dim]")

    # -- 1 -----------------------------------------------------------------
    beat(1, "Run the close",
         "Four files go in: an ERP order export, the pricebook, daily FX "
         "rates, and a handful of manual journal entries. One number comes "
         "out - the one that goes on slide 4.")

    first = run_close(store, label="Q3 close (first pass)")

    console.print("  [bold]%s[/bold]" % first.net.label)
    console.print("  [bold cyan]%s[/bold cyan]   [dim]%s[/dim]"
                  % (usd(first.net.value), short(first.net.id)))
    console.print()
    console.print("  [dim]%d order lines | %d SKUs | %d facts recorded along "
                  "the way[/dim]"
                  % (first.order_count, first.sku_count,
                     len(store.closure(first.net.id))))
    console.print()
    console.print("  [dim]That id is not a database key. It is the SHA-256 of "
                  "this figure's[/dim]")
    console.print("  [dim]entire derivation. Change anything upstream and it "
                  "becomes a different id.[/dim]")
    hold()

    # -- 2 -----------------------------------------------------------------
    beat(2, "Ask the question everyone asks",
         "Someone on the board asks where the number came from. Today that is "
         "a week of email. Here it is one command, and the answer is a "
         "sentence, not a graph.")

    render_narrative(console, store, first.net.id,
                     show_findings=False)
    hold()

    # -- 3 -----------------------------------------------------------------
    beat(3, "The uncomfortable part",
         "Nobody went looking for these. They are what falls out of asking a "
         "figure to account for itself.")

    exp = explain(store, first.net.id)
    for finding in exp.findings:
        console.print(_wrap(finding, indent=2, bullet="[yellow]![/yellow] "))
        console.print()
    console.print(_wrap(
        "The $80,000 accrual is not an error and Whence does not treat it as "
        "one. It is a number a person typed on the strength of an email. It is "
        "recorded, it is unaltered, and it is marked - permanently - as "
        "something no amount of recomputation can confirm.", indent=2))
    hold()

    # -- 4 -----------------------------------------------------------------
    beat(4, "The restatement",
         "APAC's export was a day late, so the first close was missing 14 "
         "orders. Meanwhile someone edited the pricebook. Close it again.")

    second = run_close(store,
                       orders_path=DATA / "erp_orders_restated.csv",
                       pricebook_path=DATA / "pricebook_v4.csv",
                       label="Q3 close (restated)")

    console.print("  [dim]%-22s[/dim] %16s   [dim]%s[/dim]"
                  % ("first pass", usd(first.net.value), short(first.net.id)))
    console.print("  [dim]%-22s[/dim] %16s   [dim]%s[/dim]"
                  % ("restated", usd(second.net.value), short(second.net.id)))
    console.print()
    console.print("  [dim]Both figures still exist. Nothing was overwritten - "
                  "facts are append-only,[/dim]")
    console.print("  [dim]so a restatement adds a number rather than "
                  "replacing one.[/dim]")
    hold()

    # -- 5 -----------------------------------------------------------------
    beat(5, "The reconciliation that used to take a week",
         "Two numbers, both called Q3 net revenue, $%s apart. The usual next "
         "step is two analysts and five days of spreadsheet archaeology."
         % "{:,.2f}".format(abs(second.net.amount - first.net.amount)))

    report = diff(store, first.net.id, second.net.id)
    render_diff(console, report)
    console.print("  [dim]Named actor, dated change, exact "
                  "amount, and the assurance that[/dim]")
    console.print("  [dim]nothing else moved.[/dim]")
    hold()

    # -- 6 -----------------------------------------------------------------
    beat(6, "Two analysts, one truth",
         "Dana and Priya were both asked for Q3 EMEA revenue. They wrote "
         "separate scripts - different structure, different iteration order, "
         "separate fact stores, no contact. Read demo/analyst_a.py and "
         "demo/analyst_b.py; they share no code.")

    with tempfile.TemporaryDirectory() as tmp:
        dana = _run_analyst(console, "demo/analyst_a.py", "Dana",
                            Path(tmp) / "dana")
        priya = _run_analyst(console, "demo/analyst_b.py", "Priya",
                             Path(tmp) / "priya")

        console.print()
        if dana == priya:
            console.print("  [bold green]Identical.[/bold green] [dim]Not "
                          "'the totals matched' - the same 64 characters."
                          "[/dim]")
            console.print(_wrap(
                "Neither of them had to send the other a spreadsheet. Two "
                "people who start from the same inputs and apply the same "
                "logic land on the same id, and that agreement is checkable "
                "by a third person who saw neither calculation.", indent=2))
        else:  # pragma: no cover - would mean the primitive is broken
            console.print("  [bold red]Divergent - that should not "
                          "happen.[/bold red]")

        console.print()
        console.print("  [dim]Now give Priya one edited input - the pricebook "
                      "with three changed rows.[/dim]")
        console.print()
        priya2 = _run_analyst(console, "demo/analyst_b.py", "Priya (v4)",
                              Path(tmp) / "priya2", "--pricebook",
                              "pricebook_v4.csv")
        console.print()
        console.print("  [bold yellow]Different, instantly.[/bold yellow] "
                      "[dim]No comparison of numbers was needed to know "
                      "that.[/dim]")
    hold()

    # -- 7 -----------------------------------------------------------------
    beat(7, "The same close, in a spreadsheet",
         "In real life the number on slide 4 usually comes out of a workbook: "
         "exports pasted into tabs, formulas written over them. Here is one an "
         "analyst built for this quarter. Whence reads the .xlsx file itself - "
         "no Excel, no add-in - and follows the headline cell's formulas all "
         "the way down.")

    _workbook_beat(console, store)
    hold()

    # -- 8 -----------------------------------------------------------------
    beat(8, "Hand it to the auditor",
         "An auditor wants to check the restated figure. They do not get a "
         "login to the ERP, and they should not need one.")

    with tempfile.TemporaryDirectory() as tmp:
        scratch = Path(tmp) / "auditor"
        scratch.mkdir()
        bundle = scratch / "q3-net-revenue.wbundle"
        info = export_bundle(store, second.net.id, bundle)

        console.print("  [dim]%-16s[/dim] %s" % ("bundle", bundle.name))
        console.print("  [dim]%-16s[/dim] %d facts, %.0f KB"
                      % ("contents", info["facts"], info["bytes"] / 1024))
        console.print("  [dim]%-16s[/dim] DSSE envelope, in-toto Statement v1"
                      % "format")
        console.print()
        console.print("  [dim]The auditor's machine, in full:[/dim]")
        for item in sorted(scratch.iterdir()):
            console.print("    [dim]%s[/dim]" % item.name)
        console.print()
        console.print("  [dim]No ERP. No pricebook. No .whence directory. No "
                      "network.[/dim]")
        console.print()

        proc = subprocess.run(
            [sys.executable, "-m", "whence", "verify-bundle", bundle.name],
            cwd=scratch, capture_output=True, text=True, env=_child_env(),
        )
        console.print(proc.stdout.rstrip("\n"))
        if proc.returncode != 0:  # pragma: no cover
            console.print("[red]%s[/red]" % proc.stderr)

    console.print()
    console.rule(style="dim", characters="-")
    console.print()
    console.print(_wrap(
        "Every figure in this demo can still be interrogated. With "
        "`%s`, try:" % prog(), indent=2))
    console.print()
    for suggestion in ("explain %s" % second.net.id[:12],
                       "verify %s" % first.net.id[:12],
                       "run --report board-deck.html"):
        console.print("    [dim]%s[/dim] %s" % (prog(), suggestion))
    console.print()


# ---------------------------------------------------------------------------

def _workbook_beat(console, store) -> None:
    """Beat 7. Kept separate so that a checkout without the optional
    spreadsheet dependency still runs every other beat."""
    from .cli import _wrap, render_workbook

    try:
        from .workbook import ingest_workbook
        result = ingest_workbook(store, DATA / "q3_close.xlsx",
                                 label="analyst workbook")
    except ModuleNotFoundError as exc:
        if exc.name != "openpyxl":
            raise
        # \[ so rich prints the extra's name instead of reading it as markup.
        console.print(_wrap("Skipped: reading workbooks needs openpyxl "
                            "(pip install 'whence\\[workbook]').", indent=2))
        return

    render_workbook(console, store, result)
    console.print(_wrap(
        "Nobody told it which cell was wrong. A typed number in a column of "
        "copied-down formulas is what an overwritten formula looks like, and "
        "putting the formula back is how you measure what it cost.",
        indent=2))

# ---------------------------------------------------------------------------

def _run_analyst(console, script: str, who: str, store_dir: Path,
                 *extra: str) -> str:
    """Run one analyst's script as a genuinely separate process."""
    cmd = [sys.executable, str(REPO / script),
           "--data", str(DATA), "--store", str(store_dir), *extra]
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(REPO),
                          env=_child_env())
    if proc.returncode != 0:  # pragma: no cover
        console.print("[red]%s[/red]" % proc.stderr)
        return ""
    fid = proc.stdout.strip().splitlines()[-1]
    console.print("  [dim]%-12s[/dim] %s" % (who, fid))
    return fid


def _child_env() -> dict:
    """Subprocesses need to import whence; if it was installed with -e that is
    automatic, but a reviewer running from a source checkout may not have."""
    import os
    env = dict(os.environ)
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(REPO) + (os.pathsep + existing if existing else "")
    return env
