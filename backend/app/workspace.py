from __future__ import annotations

import os
from pathlib import Path


def default_workspace_root() -> Path:
    configured = os.environ.get("VQD_WORKSPACE")
    return Path(configured).expanduser().resolve() if configured else Path(__file__).parents[2]
