from pathlib import Path

import pytest

from app.paper import paper_store
from app.runs import run_store


@pytest.fixture(autouse=True)
def isolate_run_ledger(tmp_path: Path) -> None:
    """Give every test a fresh SQLite catalog and artifact directory."""

    run_store.use_workspace(tmp_path)
    paper_store.use_workspace(tmp_path)
