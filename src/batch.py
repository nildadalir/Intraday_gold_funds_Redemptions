"""
Batch valuation pipeline.

  BI Excel
    -> validate rows (main board only)
    -> skip NULL TseId with logging (error record)
    -> process each fund (continue on failure; optional parallelism)
    -> categorize / calculate
    -> sort descending
    -> render HTML (including Error Summary)
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import config
from bi_loader import FundRecord, iter_processable_funds
from fund_calculator import ValuationResult, value_fund_record
from report_generator import render_html_report
from tsetmc_client import TsetmcClient, create_client, symbol_slug

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ErrorRecord:
    fund_name: str
    tse_id: str
    reason: str

    def as_dict(self) -> dict[str, str]:
        return {
            "fund_name": self.fund_name,
            "tse_id": self.tse_id,
            "reason": self.reason,
        }


@dataclass
class BatchSummary:
    processed: list[ValuationResult] = field(default_factory=list)
    skipped: list[FundRecord] = field(default_factory=list)
    errors: list[ErrorRecord] = field(default_factory=list)
    report_path: Path | None = None

    @property
    def ok_count(self) -> int:
        return len(self.processed)

    @property
    def skip_count(self) -> int:
        return len(self.skipped)

    @property
    def error_count(self) -> int:
        return len(self.errors)


def _error_for(record: FundRecord, reason: str) -> ErrorRecord:
    return ErrorRecord(
        fund_name=record.instrument,
        tse_id="NULL" if record.tse_id is None else str(record.tse_id),
        reason=reason,
    )


def _value_one(
    record: FundRecord, client: TsetmcClient | None = None
) -> ValuationResult:
    owns = client is None
    active = client or create_client()
    try:
        active.raw_label = symbol_slug(record.instrument)
        result = value_fund_record(active, record)
        if (
            not config.USE_MOCK_DATA
            and result.market_tse_id in config.FORBIDDEN_MOCK_MARKET_TSE_IDS
        ):
            raise ValueError(
                f"Mock market TseId forbidden in live mode: "
                f"{result.market_tse_id}"
            )
        return result
    finally:
        if owns:
            active.close()


def run_batch_pipeline(
    *,
    excel_path: Path | None = None,
    client: TsetmcClient | None = None,
    output_path: Path | None = None,
    execute: bool = True,
) -> BatchSummary:
    processable, skipped = iter_processable_funds(excel_path)
    summary = BatchSummary(skipped=list(skipped))

    for row in skipped:
        reason = "NULL TseId in BI export"
        logger.error(
            "Skipping fund with NULL TseId: Instrument=%s InstrumentId=%s "
            "AssetId=%s InstrumentCode=%s",
            row.instrument,
            row.instrument_id,
            row.asset_id,
            row.instrument_code,
        )
        summary.errors.append(_error_for(row, reason))

    workers = config.BATCH_MAX_WORKERS
    logger.info(
        "Batch plan: processable=%s skipped_null_tseid=%s execute=%s workers=%s",
        len(processable),
        len(skipped),
        execute,
        workers,
    )
    from market_hours import gold_market_status_message

    logger.info("%s", gold_market_status_message())

    if not execute:
        logger.info("Batch execute=False — validation/skip plan only")
        return summary

    # Shared client only for sequential runs (thread-safe HTTP is per-worker).
    owns_client = client is None and workers <= 1
    active = client
    if owns_client:
        active = create_client()

    try:
        if workers <= 1:
            for record in processable:
                try:
                    summary.processed.append(_value_one(record, active))
                except Exception as exc:
                    reason = str(exc)
                    logger.error(
                        "Failed %s (TseId=%s): %s",
                        record.instrument,
                        record.tse_id,
                        reason,
                    )
                    summary.errors.append(_error_for(record, reason))
        else:
            logger.info("Batch parallel workers=%s", workers)
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {
                    pool.submit(_value_one, record, None): record
                    for record in processable
                }
                for fut in as_completed(futures):
                    record = futures[fut]
                    try:
                        summary.processed.append(fut.result())
                    except Exception as exc:
                        reason = str(exc)
                        logger.error(
                            "Failed %s (TseId=%s): %s",
                            record.instrument,
                            record.tse_id,
                            reason,
                        )
                        summary.errors.append(_error_for(record, reason))
    finally:
        if owns_client and active is not None:
            active.close()

    now = datetime.now()
    out = output_path or (
        config.OUTPUT_DIR / f"gold_fund_report_{now.strftime('%Y-%m-%d')}.html"
    )
    summary.report_path = render_html_report(
        summary.processed,
        output_path=out,
        subtitle=(
            f"Processed {summary.ok_count} · "
            f"errors {summary.error_count}"
        ),
        generated_at=now,
        errors=[e.as_dict() for e in summary.errors],
    )
    return summary
