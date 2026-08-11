"""BI Excel loader and row validation for gold funds."""

from __future__ import annotations

import logging
from dataclasses import dataclass
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
    raw: dict[str, Any]

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


def load_fund_rows(excel_path: Path | None = None) -> list[FundRecord]:
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
                    raw=raw,
                )
            )
        return records
    finally:
        wb.close()


def get_fund_by_symbol(
    symbol: str, excel_path: Path | None = None
) -> FundRecord:
    rows = load_fund_rows(excel_path)
    matches = [r for r in rows if r.instrument == symbol]
    if not matches:
        raise BiLoadError(f"Symbol {symbol!r} not found in BI Excel")
    if len(matches) > 1:
        logger.warning(
            "Multiple BI rows for %s; using first (InstrumentId=%s)",
            symbol,
            matches[0].instrument_id,
        )
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
    Split BI rows into (processable, skipped).

    Skipped = NULL / missing TseId (logged by caller).
    """
    rows = load_fund_rows(excel_path)
    processable: list[FundRecord] = []
    skipped: list[FundRecord] = []
    for row in rows:
        if row.tse_id is None:
            skipped.append(row)
        else:
            processable.append(row)
    return processable, skipped
