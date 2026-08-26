"""Fund valuation calculator."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from fund_mapper import FundPair
from tsetmc_client import TsetmcClient, TsetmcDataError, create_client

logger = logging.getLogger(__name__)

Category = Literal["Redemption", "Issue/Redemption"]


@dataclass(frozen=True)
class ValuationResult:
    instrument: str
    legal_buy_volume: float
    selected_price: float
    calculated_value: float
    category: Category


@dataclass(frozen=True)
class ErrorRecord:
    fund_name: str
    tse_id: str
    reason: str


def classify(
    last_trade_price: float, nav_redemption: float
) -> tuple[Category, str]:
    """
    Last <= NAV Redemption → Redemption (uses NAV Redemption).
    Last > NAV Redemption → Issue/Redemption (uses Issue NAV when available).
    """
    if last_trade_price <= nav_redemption:
        return "Redemption", "nav_redemption"
    return "Issue/Redemption", "nav_issue"


def value_fund(client: TsetmcClient, fund: FundPair) -> ValuationResult:
    main = fund.main
    market = fund.market
    assert main.tse_id and market.tse_id

    last = client.get_last_trade_price(main.tse_id)
    nav = client.get_etf_nav(main.tse_id)
    volumes = client.get_legal_volumes(market.tse_id)
    legal_buy = volumes.buy_n_volume
    legal_sell = volumes.sell_n_volume

    if legal_sell and abs(legal_buy - legal_sell) > 1e-6:
        logger.warning(
            "Legal buy/sell differ for %s: buy=%s sell=%s; using buy",
            market.instrument,
            legal_buy,
            legal_sell,
        )

    category, price_key = classify(last, nav.redemption)
    if price_key == "nav_redemption":
        selected = nav.redemption
    else:
        if nav.issue is None:
            raise TsetmcDataError(
                f"Missing Issue/Redemption Price (issue NAV) for {main.instrument}"
            )
        selected = nav.issue

    value = legal_buy * selected

    return ValuationResult(
        instrument=main.instrument,
        legal_buy_volume=legal_buy,
        selected_price=selected,
        calculated_value=value,
        category=category,
    )


def value_fund_safe(
    fund: FundPair, client: TsetmcClient | None = None
) -> ValuationResult | ErrorRecord:
    owns = client is None
    active = client or create_client()
    try:
        return value_fund(active, fund)
    except Exception as exc:
        logger.error("Failed AssetId=%s: %s", fund.asset_id, exc)
        return ErrorRecord(
            fund_name=fund.main.instrument,
            tse_id=fund.main.tse_id or "NULL",
            reason=str(exc),
        )
    finally:
        if owns:
            active.close()
