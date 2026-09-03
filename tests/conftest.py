# SPDX-License-Identifier: Apache-2.0
"""Shared fixtures.

The two closes are run once for the whole session. They take about a second
each, and every test that needs them needs exactly the same thing.
"""

import pytest

from whence.core import Store
from whence.pipeline import DATA, run_close


@pytest.fixture(scope="session")
def closes(tmp_path_factory):
    """Both quarter closes in one store: the first pass, and the restatement.

    The restatement differs for two independent reasons -- 14 late APAC orders
    that missed the first export, and three pricebook rows that were edited --
    which is what makes it a real test of `diff` rather than a demonstration.
    """
    store = Store(tmp_path_factory.mktemp("whence") / ".whence").init()
    first = run_close(store, label="first")
    restated = run_close(
        store,
        orders_path=DATA / "erp_orders_restated.csv",
        pricebook_path=DATA / "pricebook_v4.csv",
        label="restated",
    )
    return store, first, restated


@pytest.fixture(scope="session")
def store(closes):
    return closes[0]


@pytest.fixture(scope="session")
def first(closes):
    return closes[1]


@pytest.fixture(scope="session")
def restated(closes):
    return closes[2]
