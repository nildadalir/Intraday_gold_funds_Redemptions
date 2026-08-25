"""
Intra-Day Gold Redemptions — CLI

  python run.py
  python run.py --no-send
  python run.py --force --no-send
"""

from __future__ import annotations

import argparse
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

SRC = Path(__file__).resolve().parent
ROOT = SRC.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import config  # noqa: E402
from orchestration import run_daily_attempt  # noqa: E402


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
    logging.getLogger("httpx").setLevel(config.LOG_HTTP_LEVEL)
    logging.getLogger("httpcore").setLevel(config.LOG_HTTP_LEVEL)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Intra-day gold redemption report")
    parser.add_argument("--no-send", action="store_true", help="Skip email")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Rebuild HTML even if today is already logged complete",
    )
    args = parser.parse_args(argv)

    _setup_logging()
    log = logging.getLogger("main")
    log.info("Starting daily pipeline (no_send=%s force=%s)", args.no_send, args.force)

    try:
        result = run_daily_attempt(no_send=args.no_send, force=args.force)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if result.skipped:
        print(f"{result.report_date} already complete. Email not sent.")
        return 0

    if result.success:
        if result.output_path:
            print(f"Wrote {result.output_path}")
        if result.email_sent:
            print(f"Email sent for {result.report_date}.")
        return 0

    if result.email_failed:
        print(
            f"Email failed for {result.report_date}: {'; '.join(result.errors or [])}",
            file=sys.stderr,
        )
        print("HTML is in output/; SMTP did not accept the message.", file=sys.stderr)
        return 1

    if result.validation_failed:
        print(f"Validation failed for {result.report_date}:", file=sys.stderr)
        for err in result.errors or []:
            print(f"  - {err}", file=sys.stderr)
        if result.error_path:
            print(f"Incomplete report: {result.error_path}", file=sys.stderr)
        return 1

    if result.error_path:
        print(f"Report in output_error (not emailed): {result.error_path}")
        for err in result.errors or []:
            print(f"  {err}")
        return 0

    print(
        f"Generation failed for {result.report_date}: {'; '.join(result.errors or [])}",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
