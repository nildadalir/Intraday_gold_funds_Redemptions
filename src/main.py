"""Gold fund valuation CLI.

Examples:
  python main.py --symbol آتش
  python main.py --mock-report
  python main.py --health-check
  python main.py --all
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = Path(__file__).resolve().parent
for path in (str(ROOT), str(SRC)):
    if path not in sys.path:
        sys.path.insert(0, path)

import config
from batch import run_batch_pipeline
from bi_loader import (
    BiLoadError,
    BiValidationError,
    get_fund_by_symbol,
    validate_for_processing,
)
from fund_calculator import value_fund_record
from report_generator import (
    format_poc_output,
    generate_mock_html_report,
    save_debug_json,
    save_poc_snapshot,
)
from tsetmc_client import (
    HealthCheckResult,
    TsetmcError,
    create_client,
    symbol_slug,
)


def setup_logging(name: str = "run") -> None:
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = config.LOG_DIR / f"{name}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_path, encoding="utf-8"),
        ],
        force=True,
    )


def run_symbol(symbol: str) -> int:
    setup_logging(f"symbol_{symbol_slug(symbol)}")
    logger = logging.getLogger("main")

    try:
        record = get_fund_by_symbol(symbol)
        validate_for_processing(record)
    except (BiLoadError, BiValidationError) as exc:
        logger.error("BI lookup failed: %s", exc)
        print("\n=== FAILED ===")
        print(exc)
        return 1

    logger.info(
        "Valuing %s (TseId=%s, mock=%s, proxy=%s)",
        record.instrument,
        record.tse_id,
        config.USE_MOCK_DATA,
        bool(config.TSETMC_PROXY),
    )
    from market_hours import gold_market_status_message

    logger.info("%s", gold_market_status_message())
    try:
        with create_client() as client:
            client.raw_label = symbol_slug(symbol)
            result = value_fund_record(client, record)
    except TsetmcError as exc:
        logger.error("Valuation failed: %s", exc)
        print("\n=== FAILED ===")
        print(exc)
        return 1

    output = format_poc_output(result)
    snapshot = save_poc_snapshot(result, config.OUTPUT_DIR)
    debug_path = save_debug_json(result)
    print("\n=== RESULT ===")
    print(output)
    print(f"\nSaved text: {snapshot}")
    print(f"Saved debug JSON: {debug_path}")
    return 0


def run_mock_report() -> int:
    setup_logging("mock_report")
    logger = logging.getLogger("main")
    path = generate_mock_html_report()
    logger.info("Mock HTML report ready: %s", path)
    print(f"\nMock report written: {path}")
    return 0


def run_health_check() -> int:
    setup_logging("health_check")
    logger = logging.getLogger("main")
    logger.info(
        "Running TSETMC health check (mock=%s, proxy=%s)",
        config.USE_MOCK_DATA,
        bool(config.TSETMC_PROXY),
    )
    with create_client() as client:
        result = client.health_check()
    _print_health(result)
    return 0 if result.ok else 1


def _print_health(result: HealthCheckResult) -> None:
    print("\n=== TSETMC HEALTH CHECK ===")
    print(f"Proxy configured: {result.proxy_configured}")
    print(f"DNS: {'OK' if result.dns_ok else 'FAIL'}")
    if result.dns_ips:
        print(f"  IPs: {', '.join(result.dns_ips)}")
    if result.dns_error:
        print(f"  Error: {result.dns_error}")
    print(f"HTTPS: {'OK' if result.https_ok else 'FAIL'}")
    if result.https_error:
        print(f"  Error: {result.https_error}")
    print(f"API: {'OK' if result.api_ok else 'FAIL'}")
    if result.api_status_code is not None:
        print(f"  Status: {result.api_status_code}")
    if result.api_latency_ms is not None:
        print(f"  Latency: {result.api_latency_ms:.0f} ms")
    if result.api_error:
        print(f"  Error: {result.api_error}")
    print(f"Overall: {'PASS' if result.ok else 'FAIL'}")


def run_all() -> int:
    setup_logging("batch")
    logger = logging.getLogger("main")
    if not config.BATCH_ENABLED:
        print("BATCH_ENABLED is False — refusing to run --all")
        return 2

    logger.info("Starting batch (--all), provider=%s", config.TSETMC_PROVIDER)
    summary = run_batch_pipeline(execute=True)
    print(
        f"\nBatch complete: ok={summary.ok_count} "
        f"skipped_null={summary.skip_count} errors={summary.error_count}"
    )
    if summary.report_path:
        print(f"Report: {summary.report_path}")
    for err in summary.errors:
        print(f"  ERROR {err.fund_name} ({err.tse_id}): {err.reason}")
    # Always succeed exit if report was written; partial failures are in HTML.
    return 0 if summary.report_path else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Gold fund valuation report generator"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--symbol",
        metavar="SYMBOL",
        help="Value a single fund symbol (e.g. آتش)",
    )
    group.add_argument(
        "--mock-report",
        action="store_true",
        help="Render mock HTML report for template review",
    )
    group.add_argument(
        "--health-check",
        action="store_true",
        help="Test DNS, HTTPS, and TSETMC API latency",
    )
    group.add_argument(
        "--all",
        action="store_true",
        help="Process all funds (gated by config.BATCH_ENABLED)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.health_check:
        return run_health_check()
    if args.mock_report:
        return run_mock_report()
    if args.all:
        return run_all()
    return run_symbol(args.symbol)


if __name__ == "__main__":
    raise SystemExit(main())
