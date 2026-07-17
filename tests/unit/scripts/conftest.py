"""Bootstrap sys.path so test modules can import check_mcp_coverage from scripts/ directly."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
