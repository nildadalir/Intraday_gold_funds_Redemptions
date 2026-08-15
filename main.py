# Intra-Day Gold Redemptions — CLI entrypoint
#
# Usage:
#   python main.py --poc
#   python main.py --asset-id 30018
#   python main.py

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
