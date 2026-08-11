"""BI Excel loader and row validation for gold funds."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

import config

logger = logging.getLogger(__name__)

REQUIRED_COLUMNS = (
    "InstrumentId",
    "Instrument",
    "AssetId",
    "InstrumentCode",
    "TseId",
)

# Newer BI exports include *2/*3/*4 boards; valuation uses main board only.
_BOARD_SUFFIX_RE = re.compile(r"[234]$")
_MAIN_MARKET_TOKEN = "اصلی"


class BiLoadError(Exception):
    """Excel missing or unreadable."""


class BiValidationError(Exception):
    """A fund row failed validation."""


@dataclass(frozen=True)
class FundRecord:
    """One gold-fund row from the BI export."""

    asset_id: str
    instrument_id: str
    instrument: str
    instrument_code: str
    tse_id: int | None
    market: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def asset(self) -> str:
        """Confirmed mapping: Asset = Instrument (no Asset column in BI)."""
        return self.instrument


def _cell_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.upper() == "NULL":
        return None
    return text


def parse_tse_id(value: Any) -> int | None:
    """Parse Excel TseId (supports scientific notation floats)."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, float):
        if value != value:  # NaN
            return None
        return int(value)
    text = _cell_str(value)
    if text is None:
        return None
    try:
        return int(float(text))
    except ValueError as exc:
        raise BiValidationError(f"Unparseable TseId: {value!r}") from exc


def is_board_variant_instrument(instrument: str) -> bool:
    """True for secondary boards like طلا2 / آتش3 / زر4."""
    name = instrument.strip()
    return bool(name) and bool(_BOARD_SUFFIX_RE.search(name))


def is_primary_fund_row(instrument: str, market: str | None = None) -> bool:
    """
    Keep valuation targets only: main-board base symbols.

    New BI file uses Market=بازار معاملات اصلی (not خرده فروشی).
    Rows ending in 2/3/4 are آد-لات / جبرانی / بلوکی boards.
    """
    if not instrument or is_board_variant_instrument(instrument):
        return False
    if market:
        market_norm = market.replace("\u200c", "").strip()
        if _MAIN_MARKET_TOKEN not in market_norm and "خرده" not in market_norm:
            return False
    return True


def load_fund_rows(
    excel_path: Path | None = None,
    *,
    primary_only: bool = True,
) -> list[FundRecord]:
    path = excel_path or config.EXCEL_PATH
    if not path.exists():
        raise BiLoadError(f"BI Excel not found: {path}")

    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        ws = wb.active
        rows_iter = ws.iter_rows(values_only=True)
        header_row = next(rows_iter, None)
        if not header_row:
            raise BiLoadError(f"Empty workbook: {path}")
        headers = [str(h).strip() if h is not None else "" for h in header_row]
        index = {name: i for i, name in enumerate(headers) if name}
        missing = [c for c in REQUIRED_COLUMNS if c not in index]
        if missing:
            raise BiLoadError(f"BI Excel missing columns: {missing}")

        records: list[FundRecord] = []
        skipped_boards = 0
        for row_num, row in enumerate(rows_iter, start=2):
            if row is None or all(v is None for v in row):
                continue

            def col(name: str) -> Any:
                i = index[name]
                return row[i] if i < len(row) else None

            instrument = _cell_str(col("Instrument"))
            if instrument is None:
                logger.warning("Skipping row %s: empty Instrument", row_num)
                continue

            market = _cell_str(col("Market")) if "Market" in index else None
            if primary_only and not is_primary_fund_row(instrument, market):
                skipped_boards += 1
                continue

            try:
                tse_id = parse_tse_id(col("TseId"))
            except BiValidationError as exc:
                logger.error("Row %s (%s): %s", row_num, instrument, exc)
                tse_id = None

            raw = {
                h: (row[i] if i < len(row) else None)
                for h, i in index.items()
            }
            records.append(
                FundRecord(
                    asset_id=_cell_str(col("AssetId")) or "",
                    instrument_id=_cell_str(col("InstrumentId")) or "",
                    instrument=instrument,
                    instrument_code=_cell_str(col("InstrumentCode")) or "",
                    tse_id=tse_id,
                    market=market or "",
                    raw=raw,
                )
            )
        if primary_only and skipped_boards:
            logger.info(
                "Filtered out %s non-primary board rows (*2/*3/*4 or non-main market)",
                skipped_boards,
            )
        return records
    finally:
        wb.close()


def get_fund_by_symbol(
    symbol: str, excel_path: Path | None = None
) -> FundRecord:
    rows = load_fund_rows(excel_path, primary_only=True)
    matches = [r for r in rows if r.instrument == symbol]
    if not matches:
        # Fall back to full sheet so --symbol آتش2 can still be inspected.
        rows = load_fund_rows(excel_path, primary_only=False)
        matches = [r for r in rows if r.instrument == symbol]
    if not matches:
        raise BiLoadError(f"Symbol {symbol!r} not found in BI Excel")
    if len(matches) > 1:
        primary = [
            r for r in matches if is_primary_fund_row(r.instrument, r.market)
        ]
        chosen = primary[0] if primary else matches[0]
        logger.warning(
            "Multiple BI rows for %s; using InstrumentId=%s Market=%s",
            symbol,
            chosen.instrument_id,
            chosen.market,
        )
        return chosen
    return matches[0]


def validate_for_processing(record: FundRecord) -> FundRecord:
    """Ensure TseId exists; raise if not processable."""
    if record.tse_id is None:
        raise BiValidationError(
            f"NULL TseId for {record.instrument!r} "
            f"(InstrumentId={record.instrument_id})"
        )
    return record


def iter_processable_funds(
    excel_path: Path | None = None,
) -> tuple[list[FundRecord], list[FundRecord]]:
    """
    Split primary BI rows into (processable, skipped).

    Skipped = NULL / missing TseId (logged by caller).
    """
    rows = load_fund_rows(excel_path, primary_only=True)
    processable: list[FundRecord] = []
    skipped: list[FundRecord] = []
    for row in rows:
        if row.tse_id is None:
            skipped.append(row)
        else:
            processable.append(row)
    return processable, skipped
