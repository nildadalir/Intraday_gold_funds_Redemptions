"""Fund valuation logic and market-instrument resolution."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

import config
from tsetmc_client import (
    SearchHit,
    TsetmcClient,
    TsetmcDataError,
    TsetmcError,
)

if TYPE_CHECKING:
    from bi_loader import FundRecord

logger = logging.getLogger(__name__)

Category = Literal["Redemption", "Issue/Redemption"]

# Arabic / Persian digits for "2"
_MARKET_SUFFIXES = ("2", "۲", "٢")


@dataclass(frozen=True)
class ValuationResult:
    symbol: str
    tse_id: int
    last_trade_price: float
    nav_redemption: float
    nav_issue: float | None
    market_symbol: str
    market_tse_id: str
    legal_buy_volume: float
    legal_sell_volume: float
    selected_price: float
    calculated_value: float
    category: Category
    asset_id: str = ""
    instrument_id: str = ""
    instrument_code: str = ""
    warnings: tuple[str, ...] = ()

    @property
    def asset(self) -> str:
        return self.symbol

    @property
    def instrument(self) -> str:
        return self.symbol


class MarketInstrumentNotFoundError(TsetmcError):
    """Could not resolve a validated market instrument for a fund."""


class InvalidMarketTseIdError(TsetmcDataError):
    """Market TseId missing, invalid, or a mock placeholder in live mode."""


def validate_live_market_tseid(market_tse_id: str | None) -> str:
    """
    Production guard: reject missing/invalid/mock market TseIds when not mocking.
    """
    if config.USE_MOCK_DATA:
        if market_tse_id is None or str(market_tse_id).strip() == "":
            raise InvalidMarketTseIdError("Missing market TseId (even in mock)")
        return str(market_tse_id).strip()

    mid = "" if market_tse_id is None else str(market_tse_id).strip()
    if not mid or mid.upper() == "NULL":
        raise InvalidMarketTseIdError("Missing market TseId")
    if not mid.isdigit():
        raise InvalidMarketTseIdError(f"Invalid market TseId: {mid!r}")
    if mid in config.FORBIDDEN_MOCK_MARKET_TSE_IDS:
        raise InvalidMarketTseIdError(
            f"Mock market TseId forbidden in live mode: {mid}"
        )
    return mid


def classify_category(
    last_trade_price: float, nav_redemption: float
) -> tuple[Category, str]:
    """Return (category, which_nav_field_to_use)."""
    if last_trade_price <= nav_redemption:
        return "Redemption", "redemption"
    return "Issue/Redemption", "issue"


def calculate_fund_value(legal_volume: float, selected_price: float) -> float:
    if legal_volume < 0:
        raise TsetmcDataError(f"Negative legal volume: {legal_volume}")
    if selected_price <= 0:
        raise TsetmcDataError(f"Non-positive selected price: {selected_price}")
    return legal_volume * selected_price


def _normalize_symbol(value: str) -> str:
    """Normalize Persian/Arabic digits and strip whitespace for comparisons."""
    trans = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
    return value.translate(trans).strip()


def _is_market_symbol_for_fund(fund_symbol: str, candidate_symbol: str) -> bool:
    fund = _normalize_symbol(fund_symbol)
    cand = _normalize_symbol(candidate_symbol)
    if cand == fund:
        return False
    for suffix in ("2",):
        if cand == f"{fund}{suffix}":
            return True
    # Allow minor whitespace / zero-width differences already stripped
    return bool(re.fullmatch(re.escape(fund) + r"2", cand))


def resolve_market_instrument(
    client: TsetmcClient,
    fund_symbol: str,
    fund_tse_id: int | str,
) -> SearchHit:
    """
    Resolve market instrument (e.g. آتش2) with validation beyond string match.

    Validation:
    1. Symbol equals fund + 2 / ۲
    2. Candidate is active
    3. Different insCode from the fund
    4. Instrument name is similar to the fund name
    5. Client-type endpoint returns usable data
    """
    queries = [f"{fund_symbol}{s}" for s in _MARKET_SUFFIXES]
    # Also search bare fund symbol to catch related listings
    queries.append(fund_symbol)

    seen_codes: set[str] = set()
    candidates: list[SearchHit] = []
    for query in queries:
        for hit in client.search_instruments(query):
            if hit.ins_code in seen_codes:
                continue
            seen_codes.add(hit.ins_code)
            candidates.append(hit)

    fund_code = str(fund_tse_id)
    scored: list[tuple[int, SearchHit, str]] = []

    for hit in candidates:
        if hit.ins_code == fund_code:
            continue
        if not _is_market_symbol_for_fund(fund_symbol, hit.symbol):
            continue
        if not hit.is_active:
            logger.warning(
                "Skipping inactive market candidate %s (%s)",
                hit.symbol,
                hit.ins_code,
            )
            continue

        score = 0
        reasons: list[str] = []

        # Exact market-suffix symbol match
        score += 50
        reasons.append("symbol_suffix_match")

        # Name similarity: market name usually contains fund name
        fund_norm = _normalize_symbol(fund_symbol)
        name_norm = _normalize_symbol(hit.name)
        if fund_norm and fund_norm in name_norm:
            score += 20
            reasons.append("name_contains_fund")

        # Prefer listings whose market title is not the retail fund page
        market_blob = f"{hit.flow_title} {hit.market_title}".lower()
        if "خرده" in market_blob or "retail" in market_blob:
            score -= 15
            reasons.append("retail_market_penalty")
        else:
            score += 10
            reasons.append("non_retail_market")

        # Must expose client-type data (legal volume source)
        try:
            ct = client.get_client_type(hit.ins_code)
            if ct.buy_n_volume < 0 or ct.sell_n_volume < 0:
                raise TsetmcDataError("negative client volumes")
            score += 25
            reasons.append("client_type_ok")
        except TsetmcError as exc:
            logger.warning(
                "Market candidate %s (%s) failed client-type check: %s",
                hit.symbol,
                hit.ins_code,
                exc,
            )
            continue

        # Soft check: instrument info reachable
        try:
            info = client.get_instrument_info(hit.ins_code)
            l18 = str(info.get("lVal18AFC") or "")
            if _is_market_symbol_for_fund(fund_symbol, l18):
                score += 10
                reasons.append("instrument_info_confirmed")
        except TsetmcError as exc:
            logger.warning(
                "Could not load instrumentInfo for %s: %s", hit.ins_code, exc
            )

        scored.append((score, hit, ",".join(reasons)))

    if not scored:
        raise MarketInstrumentNotFoundError(
            f"No validated market instrument found for fund {fund_symbol!r}"
        )

    scored.sort(key=lambda item: item[0], reverse=True)
    best_score, best, reasons = scored[0]
    logger.info(
        "Resolved market instrument for %s -> %s (%s) score=%s reasons=%s",
        fund_symbol,
        best.symbol,
        best.ins_code,
        best_score,
        reasons,
    )
    return best


def value_fund(
    client: TsetmcClient,
    *,
    symbol: str,
    tse_id: int,
    asset_id: str = "",
    instrument_id: str = "",
    instrument_code: str = "",
) -> ValuationResult:
    logger.info("Processing %s", symbol)
    logger.info("TseId: %s", tse_id)

    last_price = client.get_last_trade_price(tse_id)
    logger.info("Last Price: %s", int(last_price) if last_price.is_integer() else last_price)

    nav = client.get_etf_nav(tse_id)
    logger.info("NAV Redemption: %s", nav.redemption)
    logger.info(
        "NAV Issue: %s",
        "N/A" if nav.issue is None else nav.issue,
    )

    category, price_key = classify_category(last_price, nav.redemption)
    if price_key == "redemption":
        selected_price = nav.redemption
    else:
        if nav.issue is None:
            raise TsetmcDataError(
                f"NAV issue/redemption (pSubTran) missing for {symbol} "
                f"but required for category Issue/Redemption"
            )
        selected_price = nav.issue
    logger.info("Category: %s", category)
    logger.info("Selected Price: %s", selected_price)

    market = resolve_market_instrument(client, symbol, tse_id)
    market_tse_id = validate_live_market_tseid(market.ins_code)
    logger.info("Market Symbol: %s (%s)", market.symbol, market_tse_id)

    client_type = client.get_client_type(market_tse_id)
    legal_buy = client_type.buy_n_volume
    legal_sell = client_type.sell_n_volume
    warnings: list[str] = []
    if legal_buy != legal_sell:
        warning = (
            f"legal buy/sell mismatch: buy_N_Volume={legal_buy} "
            f"sell_N_Volume={legal_sell}; using buy_N_Volume"
        )
        warnings.append(warning)
        logger.warning(warning)
    if legal_buy <= 0:
        raise TsetmcDataError(
            f"Missing/zero legal buy volume for market {market.symbol} "
            f"({market_tse_id}): buy_N_Volume={legal_buy}"
        )

    logger.info("Legal Volume (buy_N): %s", int(legal_buy))
    logger.info("Legal Volume (sell_N): %s", int(legal_sell))

    value = calculate_fund_value(legal_buy, selected_price)
    logger.info("Value: %s", int(value) if float(value).is_integer() else value)

    return ValuationResult(
        symbol=symbol,
        tse_id=tse_id,
        last_trade_price=last_price,
        nav_redemption=nav.redemption,
        nav_issue=nav.issue,
        market_symbol=market.symbol,
        market_tse_id=market_tse_id,
        legal_buy_volume=legal_buy,
        legal_sell_volume=legal_sell,
        selected_price=selected_price,
        calculated_value=value,
        category=category,
        asset_id=asset_id,
        instrument_id=instrument_id,
        instrument_code=instrument_code,
        warnings=tuple(warnings),
    )


def value_fund_record(client: TsetmcClient, record: FundRecord) -> ValuationResult:
    from bi_loader import validate_for_processing

    validated = validate_for_processing(record)
    assert validated.tse_id is not None
    return value_fund(
        client,
        symbol=validated.instrument,
        tse_id=validated.tse_id,
        asset_id=validated.asset_id,
        instrument_id=validated.instrument_id,
        instrument_code=validated.instrument_code,
    )
