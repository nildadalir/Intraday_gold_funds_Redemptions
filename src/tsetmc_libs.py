"""
Preferred TSETMC access using pytse_client + finpy_tse utilities,
without constructing pytse Ticker (Ticker.__init__ downloads full history).

  - finpy_tse.get_tse_webid  → instrument search
  - pytse_client settings URL + short-timeout requests → last price / legal vol
  - CDN httpx (parent) → ETF dual NAV + fallbacks + raw dumps
"""

from __future__ import annotations

import logging
from typing import Any

import requests

import config
from tsetmc_client import (
    ClientTypeVolumes,
    EtfNav,
    SearchHit,
    TsetmcClient,
    TsetmcDataError,
)

logger = logging.getLogger(__name__)

_LIB_TIMEOUT = 8.0


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update(config.TSETMC_HEADERS)
    # Prefer explicit TSETMC_PROXY; do not inherit Cursor VPN env proxies.
    s.trust_env = False
    if config.TSETMC_PROXY:
        s.proxies = {
            "http": config.TSETMC_PROXY,
            "https": config.TSETMC_PROXY,
        }
    return s


class PreferredLibsClient(TsetmcClient):
    def get_last_trade_price(self, ins_code: int | str) -> float:
        try:
            from pytse_client import tse_settings

            url = tse_settings.TSE_ISNT_INFO_URL.format(ins_code)
            with _session() as session:
                response = session.get(url, timeout=_LIB_TIMEOUT)
            response.raise_for_status()
            price_section = response.text.split(";")[0].split(",")
            last_price = int(price_section[2])
            if last_price <= 0:
                raise TsetmcDataError("non-positive last price from pytse URL")
            logger.info(
                "last_price via pytse_client URL for %s = %s",
                ins_code,
                last_price,
            )
            return float(last_price)
        except Exception as exc:
            logger.warning(
                "pytse last_price URL failed for %s (%s); CDN fallback",
                ins_code,
                exc,
            )
            return super().get_last_trade_price(ins_code)

    def get_client_type(self, ins_code: int | str) -> ClientTypeVolumes:
        from market_hours import gold_market_is_open, gold_market_status_message

        market_open = gold_market_is_open()
        if not market_open:
            logger.info("%s", gold_market_status_message())

        # 1) Intraday board — only useful during the gold session
        if market_open:
            try:
                from pytse_client import tse_settings
                from pytse_client.ticker.api_extractors import (
                    get_corporate_trade_summary,
                    get_individual_trade_summary,
                )

                url = tse_settings.TSE_ISNT_INFO_URL.format(ins_code)
                with _session() as session:
                    response = session.get(url, timeout=_LIB_TIMEOUT)
                response.raise_for_status()
                sections = response.text.split(";")
                if len(sections) < 5:
                    raise TsetmcDataError("instinfofast missing trade summary")
                corp = get_corporate_trade_summary(sections[4])
                indiv = get_individual_trade_summary(sections[4])
                if corp is None:
                    raise TsetmcDataError("corporate trade summary missing")
                buy_n = float(corp.buy_vol)
                sell_n = float(corp.sell_vol)
                if buy_n <= 0:
                    raise TsetmcDataError("instinfofast corporate buy_vol is 0")
                buy_i = float(indiv.buy_vol) if indiv is not None else 0.0
                sell_i = float(indiv.sell_vol) if indiv is not None else 0.0
                logger.info(
                    "client_type via pytse instinfofast for %s buy_N=%s sell_N=%s",
                    ins_code,
                    buy_n,
                    sell_n,
                )
                return ClientTypeVolumes(
                    buy_n_volume=buy_n,
                    sell_n_volume=sell_n,
                    buy_i_volume=buy_i,
                    sell_i_volume=sell_i,
                    source="pytse_instinfofast",
                )
            except Exception as exc:
                logger.warning(
                    "pytse instinfofast client_type failed for %s (%s)",
                    ins_code,
                    exc,
                )
        else:
            logger.info(
                "Skipping live instinfofast client_type for %s (market closed)",
                ins_code,
            )

        # 2) Legacy clienttype.aspx history (pytse download path)
        try:
            from pytse_client import tse_settings

            url = tse_settings.TSE_CLIENT_TYPE_DATA_URL.format(ins_code)
            with _session() as session:
                response = session.get(url, timeout=_LIB_TIMEOUT)
            response.raise_for_status()
            rows = [
                row.split(",")
                for row in (response.text or "").split(";")
                if row.strip()
            ]
            # Columns per pytse download_ticker_client_types_record
            best: ClientTypeVolumes | None = None
            best_date = -1
            for row in rows:
                if len(row) < 9:
                    continue
                try:
                    date_i = int(row[0])
                    buy_i = float(row[5])
                    buy_n = float(row[6])  # corporate_buy_vol
                    sell_i = float(row[7])
                    sell_n = float(row[8])
                except (TypeError, ValueError):
                    continue
                if buy_n <= 0:
                    continue
                if date_i >= best_date:
                    best_date = date_i
                    best = ClientTypeVolumes(
                        buy_n_volume=buy_n,
                        sell_n_volume=sell_n,
                        buy_i_volume=buy_i,
                        sell_i_volume=sell_i,
                        as_of_date=date_i,
                        source="pytse_clienttype_aspx",
                    )
            if best is not None:
                logger.info(
                    "client_type via pytse clienttype.aspx for %s date=%s "
                    "buy_N=%s sell_N=%s",
                    ins_code,
                    best.as_of_date,
                    best.buy_n_volume,
                    best.sell_n_volume,
                )
                return best
            raise TsetmcDataError("clienttype.aspx had no buy_N>0 rows")
        except Exception as exc:
            logger.warning(
                "pytse clienttype.aspx failed for %s (%s); CDN fallback",
                ins_code,
                exc,
            )
            return super().get_client_type(ins_code)

    def search_instruments(self, query: str) -> list[SearchHit]:
        """
        Merge CDN GetInstrumentSearch with pytse legacy search.aspx.

        CDN often omits IME market-maker boards (e.g. آتش2); old.tsetmc.com
        search.aspx still returns them.
        """
        hits_by_code: dict[str, SearchHit] = {}

        try:
            for hit in self._search_cdn(query):
                hits_by_code[hit.ins_code] = hit
        except Exception as exc:
            logger.warning("CDN search failed for %r (%s)", query, exc)

        try:
            for hit in self._search_legacy_aspx(query):
                prev = hits_by_code.get(hit.ins_code)
                if prev is None:
                    hits_by_code[hit.ins_code] = hit
                else:
                    # Prefer richer market/flow titles when CDN left them empty
                    if not prev.market_title and hit.market_title:
                        hits_by_code[hit.ins_code] = hit
        except Exception as exc:
            logger.warning("legacy search.aspx failed for %r (%s)", query, exc)

        hits = list(hits_by_code.values())
        if hits:
            logger.info(
                "search merged CDN+legacy for %r -> %s hits", query, len(hits)
            )
            return hits

        logger.warning(
            "lib search empty for %r; httpx CDN fallback", query
        )
        return super().search_instruments(query)

    def _search_cdn(self, query: str) -> list[SearchHit]:
        from persiantools import characters

        with _session() as session:
            page = session.get(
                f"https://cdn.tsetmc.com/api/Instrument/GetInstrumentSearch/{query}",
                timeout=_LIB_TIMEOUT,
            )
        page.raise_for_status()
        rows = page.json().get("instrumentSearch") or []
        hits: list[SearchHit] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            ins_code = str(row.get("insCode") or "").strip()
            symbol = characters.ar_to_fa(
                "".join(str(row.get("lVal18AFC") or "").split("\u200c")).strip()
            )
            if not ins_code or not symbol:
                continue
            last_date = row.get("lastDate")
            try:
                last_date_i = int(last_date) if last_date is not None else None
            except (TypeError, ValueError):
                last_date_i = None
            hits.append(
                SearchHit(
                    ins_code=ins_code,
                    symbol=symbol,
                    name=characters.ar_to_fa(
                        " ".join(
                            i.strip()
                            for i in str(row.get("lVal30") or "").split("\u200c")
                        ).strip()
                    ),
                    flow=None,
                    flow_title=str(row.get("flowTitle") or "").strip(),
                    market_title=str(
                        row.get("cgrValCotTitle") or row.get("cgrValCot") or ""
                    ).strip(),
                    last_date=last_date_i,
                    is_active=bool(last_date_i and last_date_i != 0),
                )
            )
        return hits

    def _search_legacy_aspx(self, query: str) -> list[SearchHit]:
        """pytse TSE_SYMBOL_ID_URL — finds بازارگردان boards CDN omits."""
        from persiantools import characters
        from pytse_client import tse_settings

        url = tse_settings.TSE_SYMBOL_ID_URL.format(query.strip())
        with _session() as session:
            response = session.get(url, timeout=_LIB_TIMEOUT)
        response.raise_for_status()
        hits: list[SearchHit] = []
        for chunk in (response.text or "").split(";"):
            if not chunk.strip():
                continue
            parts = chunk.split(",")
            if len(parts) < 3:
                continue
            symbol = characters.ar_to_fa(
                "".join(parts[0].split("\u200c")).strip()
            )
            name = characters.ar_to_fa(
                " ".join(p.strip() for p in parts[1].split("\u200c")).strip()
            )
            ins_code = str(parts[2]).strip()
            if not symbol or not ins_code or not ins_code.isdigit():
                continue
            # Field 7 is active flag when present (pytse convention)
            active = True
            if len(parts) > 7:
                active = str(parts[7]).strip() in {"1", "true", "True"}
            market_title = ""
            if len(parts) > 5:
                market_title = characters.ar_to_fa(parts[5].strip())
            hits.append(
                SearchHit(
                    ins_code=ins_code,
                    symbol=symbol,
                    name=name,
                    flow=None,
                    flow_title="",
                    market_title=market_title,
                    last_date=None,
                    is_active=active,
                )
            )
        return hits

    def get_instrument_info(self, ins_code: int | str) -> dict[str, Any]:
        return super().get_instrument_info(ins_code)

    def get_etf_nav(self, ins_code: int | str) -> EtfNav:
        return super().get_etf_nav(ins_code)
