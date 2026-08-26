"""Batch pipeline: AssetId funds → TSETMC → HTML report."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import config
from calculator import ErrorRecord, ValuationResult, value_fund_safe
from fund_mapper import FundPair, group_funds
from market_hours import is_gold_market_open
from report_generator import all_calculated_values_zero, render_html_report
from tsetmc_client import create_client

logger = logging.getLogger(__name__)


@dataclass
class BatchSummary:
    processed: list[ValuationResult] = field(default_factory=list)
    errors: list[ErrorRecord] = field(default_factory=list)
    report_path: Path | None = None


def run_batch(
    *,
    funds: list[FundPair] | None = None,
    output_path: Path | None = None,
    generated_at: datetime | None = None,
) -> BatchSummary:
    if funds is None:
        funds, structural = group_funds()
    else:
        structural = []

    summary = BatchSummary(
        errors=[
            ErrorRecord(fund_name=msg.split(":")[0], tse_id="—", reason=msg)
            for msg in structural
        ]
    )
    for msg in structural:
        logger.error("%s", msg)

    workers = config.BATCH_MAX_WORKERS
    logger.info("Batch: funds=%s workers=%s", len(funds), workers)

    def _accept(result: ValuationResult | ErrorRecord) -> None:
        if isinstance(result, ErrorRecord):
            summary.errors.append(result)
        else:
            summary.processed.append(result)

    client = create_client()
    try:
        if workers <= 1:
            for fund in funds:
                _accept(value_fund_safe(fund, client))
        else:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = [
                    pool.submit(value_fund_safe, fund, client) for fund in funds
                ]
                for fut in as_completed(futures):
                    _accept(fut.result())
    finally:
        client.close()

    if all_calculated_values_zero(summary.processed) and output_path is None:
        logger.warning(
            "All calculated values are 0; writing to %s (gold market open=%s)",
            config.OUTPUT_ERROR_DIR,
            is_gold_market_open(),
        )

    summary.report_path = render_html_report(
        summary.processed,
        summary.errors,
        output_path=output_path,
        generated_at=generated_at,
    )
    return summary
