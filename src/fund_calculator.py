"""Fund valuation: main board for price/NAV; retail board for legal volume."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

import config
from market_hours import gold_market_is_open, gold_market_status_message
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
BoardKind = Literal["main", "retail"]

# Excel stores large TseIds as float64; BI exports often drift by tens–hundreds.
_EXCEL_FLOAT_TSEID_TOLERANCE = 512


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


def _board_label(hit: SearchHit) -> str:
    return f"{hit.flow_title} {hit.market_title}"


def _is_main_board(hit: SearchHit) -> bool:
    label = _board_label(hit)
    return "اصلی" in label


def _is_retail_board(hit: SearchHit) -> bool:
    label = _board_label(hit)
    return "خرده" in label


def _cache_key(symbol: str, board: BoardKind) -> str:
    sym = symbol.strip()
    return sym if board == "main" else f"{sym}#retail"


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


def _excel_near(candidate: str, excel_tse_id: int | str | None) -> bool:
    if excel_tse_id is None:
        return True
    try:
        return (
            abs(int(candidate) - int(str(excel_tse_id).strip()))
            <= _EXCEL_FLOAT_TSEID_TOLERANCE
        )
    except ValueError:
        return False


def _pick_from_hits(
    hits: list[SearchHit],
    *,
    fund_symbol: str,
    board: BoardKind,
    excel_tse_id: int | str | None = None,
) -> str | None:
    """Pick insCode for main (اصلی) or retail (خرده) from exact symbol hits."""
    fund_norm = _normalize_symbol(fund_symbol)
    exact = [h for h in hits if _normalize_symbol(h.symbol) == fund_norm]
    if not exact:
        return None
    active = [h for h in exact if h.is_active] or exact

    if board == "retail":
        pool = [h for h in active if _is_retail_board(h)]
        if not pool:
            return None
    else:
        pool = [h for h in active if _is_main_board(h)]
        if not pool:
            # Main BI row may still match Excel even when title lacks "اصلی".
            pool = list(active)

    if excel_tse_id is not None and board == "main":
        excel_s = str(excel_tse_id).strip()
        for hit in pool:
            if hit.ins_code == excel_s:
                return hit.ins_code
        try:
            excel_i = int(excel_s)
        except ValueError:
            excel_i = None
        if excel_i is not None:
            ranked = sorted(pool, key=lambda h: abs(int(h.ins_code) - excel_i))
            best = ranked[0]
            if abs(int(best.ins_code) - excel_i) <= _EXCEL_FLOAT_TSEID_TOLERANCE:
                return best.ins_code
            # Prefer nearest main/search hit even when Excel drifted a lot.
            return best.ins_code

    return pool[0].ins_code


def resolve_board_ins_code(
    client: TsetmcClient,
    fund_symbol: str,
    *,
    board: BoardKind,
    excel_tse_id: int | str | None = None,
    hits: list[SearchHit] | None = None,
) -> str:
    """
    Resolve insCode for either the main board (price/NAV) or retail board
    (legal volume / خرده فروشی).
    """
    from inscode_cache import get_cached_ins_code, put_cached_ins_code

    key = _cache_key(fund_symbol, board)
    # Excel TseId is the main board only — never use it to validate retail cache.
    excel_for_board = excel_tse_id if board == "main" else None

    if board == "main":
        mapped = _ins_code_from_pytse_map(fund_symbol)
        if mapped and _excel_near(mapped, excel_for_board):
            logger.info(
                "Resolved MAIN insCode for %s via pytse map: %s",
                fund_symbol,
                mapped,
            )
            if not hasattr(client, "_mock"):
                put_cached_ins_code(key, mapped)
            return mapped

    cached = get_cached_ins_code(key)
    if cached and (board == "retail" or _excel_near(cached, excel_for_board)):
        logger.info(
            "Resolved %s insCode for %s via local cache: %s",
            board.upper(),
            fund_symbol,
            cached,
        )
        return cached

    search_hits = hits if hits is not None else client.search_instruments(fund_symbol)
    chosen = _pick_from_hits(
        search_hits,
        fund_symbol=fund_symbol,
        board=board,
        excel_tse_id=excel_for_board,
    )
    if chosen:
        logger.info(
            "Resolved %s insCode for %s via search: %s",
            board.upper(),
            fund_symbol,
            chosen,
        )
        if not hasattr(client, "_mock"):
            put_cached_ins_code(key, chosen)
        return chosen

    if board == "main":
        mapped = _ins_code_from_pytse_map(fund_symbol)
        if mapped:
            logger.warning(
                "No main search hit for %s; using pytse map %s",
                fund_symbol,
                mapped,
            )
            if not hasattr(client, "_mock"):
                put_cached_ins_code(key, mapped)
            return mapped
        if cached:
            return cached
        if excel_tse_id is not None:
            logger.warning(
                "No main search hit for %s; falling back to Excel TseId %s",
                fund_symbol,
                excel_tse_id,
            )
            return str(excel_tse_id).strip()
        raise TsetmcDataError(
            f"No TSETMC main-board insCode for fund symbol {fund_symbol!r}"
        )

    raise TsetmcDataError(
        f"No TSETMC retail (خرده فروشی) board for fund symbol {fund_symbol!r}"
    )


def resolve_fund_ins_code(
    client: TsetmcClient,
    fund_symbol: str,
    excel_tse_id: int | str | None = None,
) -> str:
    """Backward-compatible alias: resolve MAIN board insCode."""
    return resolve_board_ins_code(
        client, fund_symbol, board="main", excel_tse_id=excel_tse_id
    )


def resolve_main_and_retail_ins_codes(
    client: TsetmcClient,
    fund_symbol: str,
    excel_tse_id: int | str | None = None,
) -> tuple[str, str]:
    """One search → main (price/NAV) + retail (legal volume) insCodes."""
    hits = client.search_instruments(fund_symbol)
    main_code = resolve_board_ins_code(
        client,
        fund_symbol,
        board="main",
        excel_tse_id=excel_tse_id,
        hits=hits,
    )
    retail_code = resolve_board_ins_code(
        client,
        fund_symbol,
        board="retail",
        excel_tse_id=None,
        hits=hits,
    )
    return main_code, retail_code


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
    logger.info("Excel TseId (main board): %s", tse_id)
    logger.info("%s", gold_market_status_message())

    try:
        main_code, retail_code = resolve_main_and_retail_ins_codes(
            client, symbol, tse_id
        )
    except TsetmcError as exc:
        from inscode_cache import get_cached_ins_code

        main_code = (
            get_cached_ins_code(_cache_key(symbol, "main"))
            or _ins_code_from_pytse_map(symbol)
            or str(tse_id)
        )
        retail_cached = get_cached_ins_code(_cache_key(symbol, "retail"))
        if retail_cached is None:
            raise TsetmcDataError(
                f"Could not resolve retail (خرده فروشی) board for {symbol}: {exc}"
            ) from exc
        retail_code = retail_cached
        logger.warning(
            "Board resolve partially failed for %s (%s); "
            "main=%s retail(cache)=%s",
            symbol,
            exc,
            main_code,
            retail_code,
        )

    main_tse_id = int(main_code)
    retail_tse_id = validate_live_market_tseid(str(retail_code))
    if main_tse_id != int(tse_id):
        logger.warning(
            "Using MAIN insCode %s instead of Excel TseId %s for %s",
            main_tse_id,
            tse_id,
            symbol,
        )
    logger.info("MAIN board (price/NAV): %s (%s)", symbol, main_tse_id)
    logger.info("RETAIL board (legal volume): %s (%s)", symbol, retail_tse_id)
    if str(main_tse_id) == str(retail_tse_id):
        logger.warning(
            "Main and retail insCodes are identical for %s (%s) — "
            "verify TSETMC search titles (اصلی vs خرده)",
            symbol,
            main_tse_id,
        )

    last_price = client.get_last_trade_price(main_tse_id)
    logger.info(
        "Last Price: %s", int(last_price) if last_price.is_integer() else last_price
    )

    nav = client.get_etf_nav(main_tse_id)
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

    # Legal volume ONLY from retail (خرده فروشی) board — not main / not *2.
    client_type = client.get_client_type(retail_tse_id)
    legal_buy = client_type.buy_n_volume
    legal_sell = client_type.sell_n_volume
    warnings: list[str] = [
        f"legal volume from retail board (خرده فروشی) insCode={retail_tse_id}"
    ]
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
        tse_id=main_tse_id,
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
