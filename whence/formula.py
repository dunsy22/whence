# SPDX-License-Identifier: Apache-2.0
"""Spreadsheet formulas: parsing, and the position-independent form.

Two jobs, both small:

  * **Normalize.** `=D2*E2` in row 2 and `=D3*E3` in row 3 are the same
    formula, copied down. Rewritten relative to the cell that holds them --
    `=R[0]C[-2]*R[0]C[-1]` -- they are the same text, so a column of ten
    thousand copied-down formulas carries one formula identity, not ten
    thousand. Absolute references (`$A$2`) stay absolute. This is the R1C1
    notation Excel itself uses internally, produced here from openpyxl's
    tokenizer rather than from Excel.

  * **Parse.** Turn a formula into a small tree that whence/workbook.py can
    evaluate. Only what the evaluator understands is accepted; anything else
    raises Unsupported, and the caller records the cell as opaque rather than
    guessing at it.

There is deliberately no formula *engine* here. The well-known Python ones are
copyleft (pycel is GPLv3, formulas is EUPL), which an Apache-2.0 project
cannot absorb, and a verifier you are meant to audit in an afternoon should not
contain one anyway. The evaluator covers a short, explicit list of functions
and says so about everything else.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Optional

__all__ = ["Ref", "Unsupported", "parse", "parse_ref", "normalize",
           "col_index", "col_letter", "a1"]


class Unsupported(Exception):
    """A formula, or part of one, outside what this build can evaluate."""


# ---------------------------------------------------------------------------
# References.
# ---------------------------------------------------------------------------

_REF = re.compile(r"""
    \A
    (?:(?:'(?P<qs>(?:[^']|'')+)'|(?P<s>[A-Za-z0-9_.]+))!)?
    (?P<c1a>\$?)(?P<c1>[A-Za-z]{1,3})(?P<r1a>\$?)(?P<r1>[0-9]+)
    (?::(?P<c2a>\$?)(?P<c2>[A-Za-z]{1,3})(?P<r2a>\$?)(?P<r2>[0-9]+))?
    \Z
""", re.X)


def col_index(letters: str) -> int:
    n = 0
    for ch in letters.upper():
        n = n * 26 + (ord(ch) - 64)
    return n


def col_letter(index: int) -> str:
    out = ""
    while index:
        index, rem = divmod(index - 1, 26)
        out = chr(65 + rem) + out
    return out


def a1(row: int, col: int) -> str:
    return "%s%d" % (col_letter(col), row)


@dataclass(frozen=True)
class Ref:
    """A cell or rectangular range, optionally on another sheet."""
    sheet: Optional[str]
    r1: int
    c1: int
    r2: int
    c2: int
    absolute: tuple = (False, False, False, False)  # c1, r1, c2, r2

    @property
    def is_cell(self) -> bool:
        return self.r1 == self.r2 and self.c1 == self.c2

    @property
    def shape(self) -> tuple:
        return (self.r2 - self.r1 + 1, self.c2 - self.c1 + 1)


def parse_ref(text: str) -> Ref:
    """A1-style reference -> Ref. Whole columns, whole rows, defined names and
    references into other workbooks are not accepted."""
    m = _REF.match(text.strip())
    if not m:
        if text.startswith("[") or "]" in text:
            raise Unsupported("a link to another workbook (%s)" % text)
        raise Unsupported("a reference this build cannot read (%s)" % text)
    sheet = m.group("qs").replace("''", "'") if m.group("qs") else m.group("s")
    c1, r1 = col_index(m.group("c1")), int(m.group("r1"))
    c2 = col_index(m.group("c2")) if m.group("c2") else c1
    r2 = int(m.group("r2")) if m.group("r2") else r1
    absolute = (bool(m.group("c1a")), bool(m.group("r1a")),
                bool(m.group("c2a") if m.group("c2") else m.group("c1a")),
                bool(m.group("r2a") if m.group("r2") else m.group("r1a")))
    if r2 < r1 or c2 < c1:
        raise Unsupported("a reversed range (%s)" % text)
    return Ref(sheet, r1, c1, r2, c2, absolute)


def _r1c1_part(row: int, col: int, row_abs: bool, col_abs: bool,
               host_row: int, host_col: int) -> str:
    r = "R%d" % row if row_abs else "R[%d]" % (row - host_row)
    c = "C%d" % col if col_abs else "C[%d]" % (col - host_col)
    return r + c


def _r1c1(ref: Ref, host_row: int, host_col: int) -> str:
    ca1, ra1, ca2, ra2 = ref.absolute
    text = _r1c1_part(ref.r1, ref.c1, ra1, ca1, host_row, host_col)
    if not ref.is_cell:
        text += ":" + _r1c1_part(ref.r2, ref.c2, ra2, ca2, host_row, host_col)
    if ref.sheet is not None:
        # Always quoted, so the same sheet is spelled one way.
        text = "'%s'!%s" % (ref.sheet.replace("'", "''"), text)
    return text


# ---------------------------------------------------------------------------
# Tokens.
# ---------------------------------------------------------------------------

def _tokens(formula: str) -> list:
    from openpyxl.formula.tokenizer import Token, Tokenizer
    if not formula.startswith("="):
        raise Unsupported("not a formula")
    try:
        items = Tokenizer(formula).items
    except Exception as exc:   # openpyxl raises a bare TokenizerError
        raise Unsupported("a formula that does not parse (%s)" % exc) from None
    return [t for t in items if t.type != Token.WSPACE]


def normalize(formula: str, row: int, col: int) -> str:
    """The formula rewritten relative to its own cell, R1C1-style.

    Everything that is not a reference is kept as written, except function
    names, which Excel already stores upper-case. A reference this build
    cannot read (a defined name, say) is kept verbatim: it is still the same
    text wherever the formula was copied to, which is all identity needs.
    """
    from openpyxl.formula.tokenizer import Token
    out = []
    for tok in _tokens(formula):
        if tok.type == Token.OPERAND and tok.subtype == Token.RANGE:
            try:
                out.append(_r1c1(parse_ref(tok.value), row, col))
            except Unsupported:
                out.append(tok.value)
        elif tok.type == Token.FUNC:
            out.append(tok.value.upper())
        else:
            out.append(tok.value)
    return "=" + "".join(out)


# ---------------------------------------------------------------------------
# Parsing.  A recursive descent over the tokens, for exactly the grammar the
# evaluator understands: + - * /, unary minus, parentheses, numbers, text,
# TRUE/FALSE, references, and function calls.
#
# Nodes are plain tuples:
#   ("num", Decimal)  ("str", text)  ("bool", flag)  ("ref", Ref)
#   ("neg", node)     ("bin", op, left, right)       ("fn", NAME, [args])
# ---------------------------------------------------------------------------

def parse(formula: str):
    parser = _Parser(_tokens(formula))
    node = parser.additive()
    if parser.i != len(parser.toks):
        raise Unsupported("the operator %r" % parser.toks[parser.i].value)
    return node


class _Parser:
    def __init__(self, toks):
        from openpyxl.formula.tokenizer import Token
        self.T = Token
        self.toks = toks
        self.i = 0

    def peek(self):
        return self.toks[self.i] if self.i < len(self.toks) else None

    def take(self):
        tok = self.peek()
        if tok is None:
            raise Unsupported("a formula that ends early")
        self.i += 1
        return tok

    def _infix(self, ops):
        tok = self.peek()
        return (tok is not None and tok.type == self.T.OP_IN
                and tok.value in ops)

    def additive(self):
        node = self.term()
        while self._infix(("+", "-")):
            op = self.take().value
            node = ("bin", op, node, self.term())
        return node

    def term(self):
        node = self.unary()
        while self._infix(("*", "/")):
            op = self.take().value
            node = ("bin", op, node, self.unary())
        return node

    def unary(self):
        tok = self.peek()
        if tok is not None and tok.type == self.T.OP_PRE:
            self.take()
            inner = self.unary()
            if tok.value == "-":
                return ("neg", inner)
            if tok.value == "+":
                return inner
            raise Unsupported("the prefix operator %r" % tok.value)
        node = self.primary()
        tok = self.peek()
        if tok is not None and tok.type == self.T.OP_POST:
            raise Unsupported("the postfix operator %r" % tok.value)
        return node

    def primary(self):
        T = self.T
        tok = self.take()
        if tok.type == T.OPERAND:
            if tok.subtype == T.NUMBER:
                try:
                    return ("num", Decimal(tok.value))
                except InvalidOperation:
                    raise Unsupported("the number %r" % tok.value) from None
            if tok.subtype == T.TEXT:
                return ("str", tok.value[1:-1].replace('""', '"'))
            if tok.subtype == T.LOGICAL:
                return ("bool", tok.value.upper() == "TRUE")
            if tok.subtype == T.RANGE:
                return ("ref", parse_ref(tok.value))
            raise Unsupported("the value %s" % tok.value)
        if tok.type == T.FUNC and tok.subtype == T.OPEN:
            name = tok.value[:-1].upper()
            if name.startswith("_XLFN."):
                name = name[len("_XLFN."):]
            args = []
            nxt = self.peek()
            if nxt is not None and nxt.type == T.FUNC and nxt.subtype == T.CLOSE:
                self.take()
                return ("fn", name, args)
            while True:
                args.append(self.additive())
                sep = self.take()
                if sep.type == T.SEP and sep.subtype == T.ARG:
                    continue
                if sep.type == T.FUNC and sep.subtype == T.CLOSE:
                    return ("fn", name, args)
                raise Unsupported("%r inside %s()" % (sep.value, name))
        if tok.type == T.PAREN and tok.subtype == T.OPEN:
            node = self.additive()
            close = self.take()
            if not (close.type == T.PAREN and close.subtype == T.CLOSE):
                raise Unsupported("%r where a ')' was expected" % close.value)
            return node
        raise Unsupported("%r" % tok.value)
