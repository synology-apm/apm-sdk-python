"""Bootstrap sys.path so test modules can import scripts/*.py modules directly."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
