# gold_redemptions — CLI entrypoint
#
# Usage:
#   python run.py
#   python run.py --no-send
#   python run.py --force --no-send

from __future__ import annotations

import runpy
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC_MAIN = ROOT / "src" / "main.py"

if __name__ == "__main__":
    if not SRC_MAIN.exists():
        raise SystemExit(f"Missing entry module: {SRC_MAIN}")
    runpy.run_path(str(SRC_MAIN), run_name="__main__")
