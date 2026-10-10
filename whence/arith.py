# SPDX-License-Identifier: Apache-2.0
"""General-purpose arithmetic transforms.

The reference close names its sums after what they mean -- `sum_by_region`,
`sum_regions` -- because a reader of pipeline.py should see the accounting,
not the arithmetic. A spreadsheet does not say what its sums mean; `=SUM(B4:B7)`
is just a sum. So formulas lowered out of a workbook are recorded with these
plain names instead.

They live in their own module, with no third-party imports, because the
verifier must know them on an auditor's machine that has never installed a
spreadsheet library. attest._load_transforms imports this file for exactly
that reason. (Multiplication needs nothing new: `multiply`, from pipeline.py,
is already the general product.)
"""

from __future__ import annotations

from decimal import Decimal
from typing import List

from .tracked import tracked


@tracked("sum", commutative=True, variadic=True)
def add(amounts: List[Decimal]) -> Decimal:
    """A plain sum. Order is irrelevant, so it records no operand order."""
    return sum(amounts, Decimal("0"))


@tracked("subtract")
def subtract(minuend: Decimal, subtrahend: Decimal) -> Decimal:
    """a - b. Not commutative, so the operand order is recorded and hashed
    (SPEC.md section 3.6)."""
    return minuend - subtrahend
