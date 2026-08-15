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
    asset_id: str
    asset: str
    instrument_id: str
    instrument: str
    market_instrument: str
    main_tse_id: str
    market_tse_id: str
    last_trade_price: float
    nav_redemption: float
    nav_issue: float | None
    legal_buy_volume: float
    legal_sell_volume: float
    selected_price: float
    calculated_value: float
    category: Category
    warnings: tuple[str, ...] = ()


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
    asset = fund.asset
    main = fund.main
    market = fund.market
    assert main.tse_id and market.tse_id

    logger.info(
        "Processing AssetId=%s Asset=%s main=%s(%s) market=%s(%s)",
        fund.asset_id,
        asset,
        main.instrument,
        main.tse_id,
        market.instrument,
        market.tse_id,
    )

    last = client.get_last_trade_price(main.tse_id)
    logger.info("Last Trade Price: %s", last)

    nav = client.get_etf_nav(main.tse_id)
    logger.info("NAV Redemption: %s", nav.redemption)
    logger.info("NAV Issue: %s", nav.issue)

    volumes = client.get_legal_volumes(market.tse_id)
    legal_buy = volumes.buy_n_volume
    legal_sell = volumes.sell_n_volume
    logger.info(
        "Legal Volume buy_N=%s sell_N=%s source=%s",
        legal_buy,
        legal_sell,
        volumes.source,
    )

    warnings: list[str] = []
    if legal_sell and abs(legal_buy - legal_sell) > 1e-6:
        msg = (
            f"Legal buy/sell differ for {market.instrument}: "
            f"buy={legal_buy} sell={legal_sell}; using buy"
        )
        warnings.append(msg)
        logger.warning("%s", msg)

    if legal_buy <= 0:
        warnings.append(
            f"Live legal volume is 0 for {market.instrument} "
            f"(TseId={market.tse_id}); using page value 0"
        )
        logger.warning("%s", warnings[-1])

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
    logger.info("Category: %s | Selected Price: %s | Value: %s", category, selected, value)

    return ValuationResult(
        asset_id=fund.asset_id,
        asset=asset,
        instrument_id=main.instrument_id,
        instrument=main.instrument,
        market_instrument=market.instrument,
        main_tse_id=main.tse_id,
        market_tse_id=market.tse_id,
        last_trade_price=last,
        nav_redemption=nav.redemption,
        nav_issue=nav.issue,
        legal_buy_volume=legal_buy,
        legal_sell_volume=legal_sell,
        selected_price=selected,
        calculated_value=value,
        category=category,
        warnings=tuple(warnings),
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
            fund_name=fund.asset,
            tse_id=fund.main.tse_id or "NULL",
            reason=str(exc),
        )
    finally:
        if owns:
            active.close()
