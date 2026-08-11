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
            buy_i = float(indiv.buy_vol) if indiv is not None else 0.0
            sell_i = float(indiv.sell_vol) if indiv is not None else 0.0
            logger.info(
                "client_type via pytse URL for %s buy_N=%s sell_N=%s",
                ins_code,
                buy_n,
                sell_n,
            )
            return ClientTypeVolumes(
                buy_n_volume=buy_n,
                sell_n_volume=sell_n,
                buy_i_volume=buy_i,
                sell_i_volume=sell_i,
            )
        except Exception as exc:
            logger.warning(
                "pytse client_type URL failed for %s (%s); CDN fallback",
                ins_code,
                exc,
            )
            return super().get_client_type(ins_code)

    def search_instruments(self, query: str) -> list[SearchHit]:
        try:
            import finpy_tse as fpy

            # finpy uses requests without trust_env override; call CDN-shaped
            # search the same way finpy does, but with our session.
            from persiantools import characters

            with _session() as session:
                page = session.get(
                    f"http://cdn.tsetmc.com/api/Instrument/GetInstrumentSearch/{query}",
                    timeout=_LIB_TIMEOUT,
                )
            page.raise_for_status()
            rows = page.json().get("instrumentSearch") or []
            hits: list[SearchHit] = []
            for row in rows:
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
                        market_title=str(row.get("cgrValCotTitle") or "").strip(),
                        last_date=last_date_i,
                        is_active=bool(last_date_i and last_date_i != 0),
                    )
                )
            if hits:
                logger.info(
                    "search via finpy-style CDN for %r -> %s hits",
                    query,
                    len(hits),
                )
                return hits
            # Fall back to finpy helper if our parse failed
            df = fpy.get_tse_webid(query)
            if df is False or df is None or getattr(df, "empty", True):
                raise TsetmcDataError("finpy_tse search returned empty")
            hits = []
            for (ticker, active), row in df.iterrows():
                ins_code = str(row["WebID"]).strip()
                symbol = str(ticker).strip()
                if not ins_code or not symbol:
                    continue
                try:
                    last_date_i = int(active)
                except (TypeError, ValueError):
                    last_date_i = 0
                hits.append(
                    SearchHit(
                        ins_code=ins_code,
                        symbol=symbol,
                        name=str(row.get("Name") or "").strip(),
                        flow=None,
                        flow_title="",
                        market_title=str(row.get("Market") or "").strip(),
                        last_date=last_date_i,
                        is_active=bool(last_date_i and last_date_i != 0),
                    )
                )
            if hits:
                return hits
            raise TsetmcDataError("no search hits")
        except Exception as exc:
            logger.warning(
                "finpy/pytse search failed for %r (%s); CDN fallback",
                query,
                exc,
            )
            return super().search_instruments(query)

    def get_instrument_info(self, ins_code: int | str) -> dict[str, Any]:
        return super().get_instrument_info(ins_code)

    def get_etf_nav(self, ins_code: int | str) -> EtfNav:
        return super().get_etf_nav(ins_code)
