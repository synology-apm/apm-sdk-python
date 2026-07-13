"""Bootstrap sys.path so test modules can import example scripts directly.

Convention: example modules are imported flat (``from backup_catchup import ...``)
via this path insert; shared test helpers use the full package path
(``from tests.unit.examples._fixtures import ...``).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "examples"))
