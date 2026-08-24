"""
Intra-Day Gold Redemptions — CLI

  python main.py
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import config  # noqa: E402
from pipeline import run_batch  # noqa: E402


def _setup_logging() -> None:
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S")
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    root.handlers.clear()
    root.addHandler(sh)
    fh = RotatingFileHandler(
        config.LOG_DIR / "intra_day_gold_redemptions.log",
        maxBytes=config.LOG_MAX_BYTES,
        backupCount=config.LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    fh.setFormatter(fmt)
    root.addHandler(fh)


def main(argv: list[str] | None = None) -> int:
    _ = argv  # production CLI has no flags
    _setup_logging()
    log = logging.getLogger("main")
    log.info("Starting batch (all AssetIds)")
    summary = run_batch()
    print(
        f"\nBatch complete: ok={summary.ok_count} errors={summary.error_count}"
    )
    print(f"Report: {summary.report_path}")
    for err in summary.errors:
        print(f"  ERROR {err.fund_name} ({err.tse_id}): {err.reason}")
    return 0 if summary.ok_count else 1


if __name__ == "__main__":
    raise SystemExit(main())
