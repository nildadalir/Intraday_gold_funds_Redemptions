"""Map BI instruments into funds grouped by AssetId."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from excel_reader import InstrumentRow, load_instrument_rows

logger = logging.getLogger(__name__)

_BOARD_SUFFIX_RE = re.compile(r"[234]$")
_MAIN_MARKET = "اصلی"
_MARKET_BOARD = "آد"


@dataclass(frozen=True)
class FundPair:
    """One gold fund: main board (price/NAV) + market board (legal volume)."""

    asset_id: str
    asset: str
    main: InstrumentRow
    market: InstrumentRow


def is_main_board(row: InstrumentRow) -> bool:
    if _BOARD_SUFFIX_RE.search(row.instrument.strip()):
        return False
    return _MAIN_MARKET in (row.market or "")


def is_market_board(row: InstrumentRow) -> bool:
    """Legal-volume board: *2 / آد-لات."""
    name = row.instrument.strip()
    if name.endswith("2"):
        return True
    return _MARKET_BOARD in (row.market or "")


def group_funds(rows: list[InstrumentRow] | None = None) -> tuple[list[FundPair], list[str]]:
    """
    Group by AssetId. Returns (funds, structural_errors).

    Structural errors = missing main, missing market, or missing TseId.
    """
    rows = rows if rows is not None else load_instrument_rows()
    by_asset: dict[str, list[InstrumentRow]] = {}
    for row in rows:
        by_asset.setdefault(row.asset_id, []).append(row)

    funds: list[FundPair] = []
    errors: list[str] = []

    for asset_id, instruments in sorted(by_asset.items(), key=lambda x: x[0]):
        asset_name = instruments[0].asset
        mains = [r for r in instruments if is_main_board(r)]
        markets = [r for r in instruments if is_market_board(r)]

        if not mains:
            errors.append(
                f"AssetId={asset_id} ({asset_name}): Missing main instrument"
            )
            continue
        if len(mains) > 1:
            logger.warning(
                "AssetId=%s has %s main boards; using %s",
                asset_id,
                len(mains),
                mains[0].instrument,
            )
        main = mains[0]

        if not markets:
            # Non-listed / incomplete funds (e.g. غیر بورسی) — skip quietly.
            logger.info(
                "Skipping AssetId=%s (%s): no market instrument (*2)",
                asset_id,
                asset_name,
            )
            continue
        # Prefer exact *2 name over other آد-لات matches.
        markets_sorted = sorted(
            markets,
            key=lambda r: (0 if r.instrument.strip().endswith("2") else 1, r.instrument),
        )
        market = markets_sorted[0]

        if main.tse_id is None:
            errors.append(
                f"AssetId={asset_id} ({asset_name}): Missing TseId on main "
                f"{main.instrument}"
            )
            continue
        if market.tse_id is None:
            errors.append(
                f"AssetId={asset_id} ({asset_name}): Missing TseId on market "
                f"{market.instrument}"
            )
            continue

        funds.append(
            FundPair(
                asset_id=asset_id,
                asset=asset_name,
                main=main,
                market=market,
            )
        )

    logger.info(
        "Mapped %s funds by AssetId (%s structural issues)",
        len(funds),
        len(errors),
    )
    return funds, errors

