"""
Intra-Day Gold Redemptions — CLI

  python main.py              # batch all AssetIds
  python main.py --poc        # Proof of Concept: آتش only
  python main.py --asset-id 30018
"""

from __future__ import annotations

import argparse
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
from calculator import ErrorRecord, value_fund  # noqa: E402
from fund_mapper import get_fund_by_asset_id, get_fund_by_main_instrument  # noqa: E402
from pipeline import run_batch  # noqa: E402
from report_generator import render_html_report  # noqa: E402
from tsetmc_client import create_client  # noqa: E402


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


def _print_poc(result) -> None:
    print()
    print("=== PoC RESULT ===")
    print(f"Asset:              {result.asset}")
    print(f"AssetId:            {result.asset_id}")
    print(f"Main Instrument:    {result.instrument}")
    print(f"Main TseId:         {result.main_tse_id}")
    print(f"Last Trade:         {result.last_trade_price}")
    print(f"NAV Redemption:     {result.nav_redemption}")
    print(f"NAV Issue:          {result.nav_issue}")
    print(f"Market Instrument:  {result.market_instrument}")
    print(f"Market TseId:       {result.market_tse_id}")
    print(f"Legal Volume:       {result.legal_buy_volume}")
    print(f"Category:           {result.category}")
    print(f"Selected Price:     {result.selected_price}")
    print(f"Calculated Value:   {result.calculated_value}")
    for w in result.warnings:
        print(f"Warning:            {w}")
    print("==================")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Intra-Day Gold Redemptions")
    parser.add_argument(
        "--poc",
        action="store_true",
        help=f"Proof of Concept for {config.POC_INSTRUMENT} only",
    )
    parser.add_argument("--asset-id", help="Process a single AssetId")
    parser.add_argument(
        "--all",
        action="store_true",
        help="Process all AssetIds (default when no flag)",
    )
    args = parser.parse_args(argv)
    _setup_logging()
    log = logging.getLogger("main")

    if args.poc or args.asset_id:
        asset_id = args.asset_id or config.POC_ASSET_ID
        log.info("PoC / single fund AssetId=%s", asset_id)
        try:
            fund = get_fund_by_asset_id(asset_id)
        except LookupError:
            fund = get_fund_by_main_instrument(config.POC_INSTRUMENT)
        with create_client() as client:
            try:
                result = value_fund(client, fund)
            except Exception as exc:
                log.error("PoC failed: %s", exc)
                print(f"\n=== FAILED ===\n{exc}")
                return 1
        _print_poc(result)
        path = render_html_report([result], [])
        print(f"Report: {path}")
        return 0

    log.info("Starting batch (all AssetIds), provider=%s", config.TSETMC_PROVIDER)
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
