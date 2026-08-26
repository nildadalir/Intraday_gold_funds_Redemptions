"""SQL Server reader — instruments grouped later by AssetId."""

from __future__ import annotations

from typing import Any

import pyodbc

import config
from excel_reader import ExcelReadError, InstrumentRow, parse_tse_id

REQUIRED_COLUMNS = (
    "Instrument",
    "AssetId",
    "TseId",
    "Market",
)


class DbReadError(Exception):
    """Database unreachable, query failed, or missing required columns."""


def _cell_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.upper() == "NULL":
        return None
    return text


def _connection_string() -> str:
    driver = config.DB_DRIVER.strip("{}")
    parts = [
        f"DRIVER={{{driver}}};",
        f"SERVER={config.DB_SERVER};",
        f"DATABASE={config.DB_DATABASE};",
    ]
    if config.DB_TRUSTED_CONNECTION:
        parts.append("Trusted_Connection=yes;")
    else:
        if not config.DB_USERNAME or not config.DB_PASSWORD:
            raise DbReadError(
                "DB_USERNAME and DB_PASSWORD must be set in .env when "
                "db.trusted_connection is false"
            )
        parts.append(f"UID={config.DB_USERNAME};")
        parts.append(f"PWD={config.DB_PASSWORD};")
    return "".join(parts)


def _load_query() -> str:
    path = config.DB_QUERY_PATH
    if not path.exists():
        raise DbReadError(f"SQL query file not found: {path}")
    sql = path.read_text(encoding="utf-8").strip()
    if not sql:
        raise DbReadError(f"SQL query file is empty: {path}")
    return sql


def load_instrument_rows() -> list[InstrumentRow]:
    sql = _load_query()
    try:
        conn = pyodbc.connect(_connection_string())
    except pyodbc.Error as exc:
        raise DbReadError(f"Could not connect to SQL Server: {exc}") from exc

    try:
        cursor = conn.cursor()
        cursor.execute(sql)
        headers = [col[0] for col in (cursor.description or [])]
        col_index = {str(name).strip(): i for i, name in enumerate(headers) if name}
        missing = [c for c in REQUIRED_COLUMNS if c not in col_index]
        if missing:
            raise DbReadError(f"Query result missing columns: {missing}")

        out: list[InstrumentRow] = []
        for raw in cursor.fetchall():

            def col(name: str) -> Any:
                idx = col_index.get(name)
                if idx is None or idx >= len(raw):
                    return None
                return raw[idx]

            instrument = _cell_str(col("Instrument"))
            asset_id = _cell_str(col("AssetId"))
            if not instrument or not asset_id:
                continue
            asset = instrument
            instrument_id = asset_id

            market = _cell_str(col("Market")) or ""

            try:
                tse_id = parse_tse_id(col("TseId"))
            except ExcelReadError as exc:
                raise DbReadError(str(exc)) from exc

            out.append(
                InstrumentRow(
                    instrument_id=instrument_id,
                    instrument=instrument,
                    asset_id=asset_id,
                    asset=asset,
                    tse_id=tse_id,
                    market=market.strip(),
                )
            )
        return out
    except DbReadError:
        raise
    except pyodbc.Error as exc:
        raise DbReadError(f"SQL query failed: {exc}") from exc
    finally:
        conn.close()
