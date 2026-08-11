"""Fund valuation logic (retail board last/NAV/legal volume)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

import config
from market_hours import gold_market_is_open, gold_market_status_message
from tsetmc_client import (
    TsetmcClient,
    TsetmcDataError,
    TsetmcError,
)

if TYPE_CHECKING:
    from bi_loader import FundRecord

logger = logging.getLogger(__name__)

Category = Literal["Redemption", "Issue/Redemption"]


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
    legal_volume_as_of: int | None = None
    legal_volume_source: str = ""

    @property
    def asset(self) -> str:
        return self.symbol

    @property
    def instrument(self) -> str:
        return self.symbol


class InvalidMarketTseIdError(TsetmcDataError):
    """Instrument TseId missing, invalid, or a mock placeholder in live mode."""


def validate_live_market_tseid(market_tse_id: str | None) -> str:
    """
    Production guard: reject missing/invalid/mock TseIds when not mocking.
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


# Excel stores large TseIds as float64; BI exports often drift by tens–hundreds.
_EXCEL_FLOAT_TSEID_TOLERANCE = 512


def _ins_code_from_pytse_map(fund_symbol: str) -> str | None:
    """Offline symbol→insCode from pytse_client's bundled map (may be stale)."""
    try:
        from pytse_client.symbols_data import get_ticker_index

        idx = get_ticker_index(fund_symbol)
        if idx is None:
            return None
        return str(idx).strip() or None
    except Exception as exc:
        logger.debug("pytse symbol map lookup failed for %s: %s", fund_symbol, exc)
        return None


def resolve_fund_ins_code(
    client: TsetmcClient,
    fund_symbol: str,
    excel_tse_id: int | str | None = None,
) -> str:
    """
    Resolve the retail/main fund share insCode via TSETMC search.

    BI Excel TseId is often float-corrupted (16–17 digit IDs). Prefer an
    exact search hit for the fund symbol (خرده فروشی / اصلی when available);
    use Excel only as a near-match hint. Falls back to pytse_client's static
    symbol map when search has no hit. Tries the offline map / local cache
    first when within Excel float tolerance (avoids a network search).
    """
    from inscode_cache import get_cached_ins_code, put_cached_ins_code

    def _excel_near(candidate: str) -> bool:
        if excel_tse_id is None:
            return True
        try:
            return (
                abs(int(candidate) - int(str(excel_tse_id).strip()))
                <= _EXCEL_FLOAT_TSEID_TOLERANCE
            )
        except ValueError:
            return False

    mapped = _ins_code_from_pytse_map(fund_symbol)
    if mapped and _excel_near(mapped):
        logger.info(
            "Resolved fund insCode for %s via pytse map "
            "(Excel near-match): %s",
            fund_symbol,
            mapped,
        )
        put_cached_ins_code(fund_symbol, mapped)
        return mapped

    cached = get_cached_ins_code(fund_symbol)
    if cached and _excel_near(cached):
        logger.info(
            "Resolved fund insCode for %s via local cache: %s",
            fund_symbol,
            cached,
        )
        return cached

    hits = client.search_instruments(fund_symbol)
    fund_norm = _normalize_symbol(fund_symbol)
    exact = [h for h in hits if _normalize_symbol(h.symbol) == fund_norm]
    if not exact:
        if mapped:
            logger.warning(
                "No search hit for %s; using pytse map insCode %s",
                fund_symbol,
                mapped,
            )
            put_cached_ins_code(fund_symbol, mapped)
            return mapped
        if cached:
            logger.warning(
                "No search hit for %s; using cached insCode %s",
                fund_symbol,
                cached,
            )
            return cached
        raise TsetmcDataError(
            f"No TSETMC search hit for fund symbol {fund_symbol!r}"
        )
    pool = [h for h in exact if h.is_active] or exact

    # Prefer retail / main board for last price / NAV / legal volume
    preferred = [
        h
        for h in pool
        if ("خرده" in f"{h.flow_title} {h.market_title}")
        or ("اصلی" in f"{h.flow_title} {h.market_title}")
    ]
    if preferred:
        pool = preferred

    if excel_tse_id is None:
        chosen = pool[0]
        logger.info(
            "Resolved fund insCode for %s via search: %s",
            fund_symbol,
            chosen.ins_code,
        )
        put_cached_ins_code(fund_symbol, chosen.ins_code)
        return chosen.ins_code

    excel_s = str(excel_tse_id).strip()
    for hit in pool:
        if hit.ins_code == excel_s:
            logger.info(
                "Fund insCode for %s matches Excel TseId: %s",
                fund_symbol,
                excel_s,
            )
            put_cached_ins_code(fund_symbol, hit.ins_code)
            return hit.ins_code

    try:
        excel_i = int(excel_s)
    except ValueError:
        excel_i = None

    if excel_i is not None:
        ranked = sorted(pool, key=lambda h: abs(int(h.ins_code) - excel_i))
        best = ranked[0]
        delta = abs(int(best.ins_code) - excel_i)
        if delta <= _EXCEL_FLOAT_TSEID_TOLERANCE:
            logger.warning(
                "Excel TseId %s looks float-corrupted for %s; "
                "using search insCode %s (delta=%s)",
                excel_s,
                fund_symbol,
                best.ins_code,
                delta,
            )
        else:
            logger.warning(
                "Excel TseId %s differs from search insCode %s for %s "
                "(delta=%s); preferring search",
                excel_s,
                best.ins_code,
                fund_symbol,
                delta,
            )
        put_cached_ins_code(fund_symbol, best.ins_code)
        return best.ins_code

    chosen = pool[0]
    logger.warning(
        "Unusable Excel TseId %r for %s; using search insCode %s",
        excel_tse_id,
        fund_symbol,
        chosen.ins_code,
    )
    put_cached_ins_code(fund_symbol, chosen.ins_code)
    return chosen.ins_code


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
    logger.info("Excel TseId: %s", tse_id)
    logger.info("%s", gold_market_status_message())

    try:
        resolved_code = resolve_fund_ins_code(client, symbol, tse_id)
    except TsetmcError as exc:
        from inscode_cache import get_cached_ins_code

        mapped = _ins_code_from_pytse_map(symbol)
        cached = get_cached_ins_code(symbol)
        if mapped:
            logger.warning(
                "insCode search failed for %s (%s); using pytse map %s",
                symbol,
                exc,
                mapped,
            )
            resolved_code = mapped
        elif cached:
            logger.warning(
                "insCode search failed for %s (%s); using cache %s",
                symbol,
                exc,
                cached,
            )
            resolved_code = cached
        else:
            logger.warning(
                "insCode search failed for %s (%s); falling back to Excel TseId %s",
                symbol,
                exc,
                tse_id,
            )
            resolved_code = str(tse_id)
    resolved_tse_id = int(resolved_code)
    if resolved_tse_id != int(tse_id):
        logger.warning(
            "Using search insCode %s instead of Excel TseId %s for %s",
            resolved_tse_id,
            tse_id,
            symbol,
        )
    else:
        logger.info("TseId: %s", resolved_tse_id)

    last_price = client.get_last_trade_price(resolved_tse_id)
    logger.info(
        "Last Price: %s", int(last_price) if last_price.is_integer() else last_price
    )

    nav = client.get_etf_nav(resolved_tse_id)
    logger.info("NAV Redemption: %s", nav.redemption)
    logger.info(
        "NAV Issue/Redemption: %s",
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

    # Legal volume from the retail (خرده فروشی) board — same instrument as
    # last price / NAV — not from the market-maker {symbol}2 page.
    retail_tse_id = validate_live_market_tseid(str(resolved_tse_id))
    logger.info("Retail board for legal volume: %s (%s)", symbol, retail_tse_id)

    client_type = client.get_client_type(retail_tse_id)
    legal_buy = client_type.buy_n_volume
    legal_sell = client_type.sell_n_volume
    warnings: list[str] = []
    if not gold_market_is_open():
        warnings.append(
            f"gold market closed (Tehran session Sat–Wed "
            f"{config.MARKET_OPEN_TIME}–{config.MARKET_CLOSE_TIME}); "
            "used last available session data where live boards were empty"
        )
    if client_type.as_of_date and client_type.source not in {
        "live",
        "pytse_instinfofast",
    }:
        warnings.append(
            f"legal volume from {client_type.source} as_of={client_type.as_of_date}"
        )
        logger.info(
            "Legal volume source=%s as_of=%s",
            client_type.source,
            client_type.as_of_date,
        )
    if legal_buy != legal_sell:
        warning = (
            f"legal buy/sell mismatch: buy_N_Volume={legal_buy} "
            f"sell_N_Volume={legal_sell}; using buy_N_Volume"
        )
        warnings.append(warning)
        logger.warning(warning)
    if legal_buy <= 0:
        raise TsetmcDataError(
            f"Missing/zero legal buy volume for retail {symbol} "
            f"({retail_tse_id}): buy_N_Volume={legal_buy}"
        )

    logger.info("Legal Volume (buy_N): %s", int(legal_buy))
    logger.info("Legal Volume (sell_N): %s", int(legal_sell))

    value = calculate_fund_value(legal_buy, selected_price)
    logger.info("Value: %s", int(value) if float(value).is_integer() else value)

    return ValuationResult(
        symbol=symbol,
        tse_id=resolved_tse_id,
        last_trade_price=last_price,
        nav_redemption=nav.redemption,
        nav_issue=nav.issue,
        market_symbol=symbol,
        market_tse_id=retail_tse_id,
        legal_buy_volume=legal_buy,
        legal_sell_volume=legal_sell,
        selected_price=selected_price,
        calculated_value=value,
        category=category,
        asset_id=asset_id,
        instrument_id=instrument_id,
        instrument_code=instrument_code,
        warnings=tuple(warnings),
        legal_volume_as_of=client_type.as_of_date,
        legal_volume_source=client_type.source,
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
