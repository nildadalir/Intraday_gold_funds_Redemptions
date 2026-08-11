# Gold Fund Valuation — CLI entrypoint
#
# Usage:
#   python main.py --health-check
#   python main.py --symbol آتش
#   python main.py --mock-report
#   python main.py --all

from __future__ import annotations

import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC_MAIN = ROOT / "src" / "main.py"


if __name__ == "__main__":
    if not SRC_MAIN.exists():
        raise SystemExit(f"Missing entry module: {SRC_MAIN}")
    runpy.run_path(str(SRC_MAIN), run_name="__main__")
