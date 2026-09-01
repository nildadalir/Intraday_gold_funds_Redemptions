"""TSETMC client — TseId-based last price, ETF NAV, legal (حقوقی) volume."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

import config

logger = logging.getLogger(__name__)


class TsetmcError(Exception):
    """Base TSETMC failure."""


class TsetmcNetworkError(TsetmcError):
    """Timeout / transport failure."""


class TsetmcDataError(TsetmcError):
    """Missing or unusable fields in a successful response."""


@dataclass(frozen=True)
class ClosingPriceInfo:
    last_trade: float
    closing: float  # قیمت پایانی


@dataclass(frozen=True)
class EtfNav:
    redemption: float
    issue: float | None


@dataclass(frozen=True)
class ClientTypeVolumes:
    buy_n_volume: float
    sell_n_volume: float


class TsetmcClient:
    def __init__(
        self,
        *,
        base_url: str = config.TSETMC_BASE_URL,
        timeout: float = config.TSETMC_TIMEOUT_SECONDS,
        connect_timeout: float = config.TSETMC_CONNECT_TIMEOUT_SECONDS,
        proxy: str | None = config.TSETMC_PROXY,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.proxy = proxy
        timeout_cfg = httpx.Timeout(timeout, connect=connect_timeout)
        kwargs: dict[str, Any] = {
            "base_url": self.base_url,
            "headers": headers or dict(config.TSETMC_HEADERS),
            "timeout": timeout_cfg,
            "follow_redirects": True,
        }
        if proxy:
            kwargs["proxy"] = proxy
            logger.info("TSETMC proxy configured")
        self._client = httpx.Client(**kwargs)

    def close(self) -> None:
        self._client.close()

    @retry(
        reraise=True,
        stop=stop_after_attempt(max(1, int(config.TSETMC_MAX_RETRIES))),
        wait=wait_exponential(
            multiplier=config.TSETMC_RETRY_WAIT_SECONDS, min=1, max=8
        ),
        retry=retry_if_exception_type(TsetmcNetworkError),
    )
    def _get_json(self, path: str) -> dict[str, Any]:
        try:
            response = self._client.get(path)
        except httpx.TimeoutException as exc:
            raise TsetmcNetworkError(
                f"Timeout calling {self.base_url}{path}: {exc}"
            ) from exc
        except httpx.HTTPError as exc:
            raise TsetmcNetworkError(
                f"HTTP error calling {self.base_url}{path}: {exc}"
            ) from exc
        if response.status_code >= 400:
            raise TsetmcDataError(f"HTTP {response.status_code} for {path}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise TsetmcDataError(f"Non-JSON response for {path}") from exc
        if not isinstance(payload, dict):
            raise TsetmcDataError(f"Unexpected JSON root for {path}")
        return payload

    def get_closing_price_info(self, tse_id: str) -> ClosingPriceInfo:
        """Last trade and قیمت پایانی from the first (main) market board."""
        payload = self._get_json(
            f"/api/ClosingPrice/GetClosingPriceInfo/{tse_id}"
        )
        info = payload.get("closingPriceInfo")
        if not isinstance(info, dict):
            raise TsetmcDataError(
                f"Missing ClosingPriceInfo for TseId={tse_id}"
            )
        last_raw = info.get("pDrCotVal") or info.get("pl") or info.get("pClosing")
        closing_raw = info.get("pClosing")
        last = float(last_raw) if last_raw is not None else 0.0
        closing = float(closing_raw) if closing_raw is not None else 0.0
        if last <= 0:
            raise TsetmcDataError(
                f"Missing Last Trade Price for TseId={tse_id}: ClosingPriceInfo empty/invalid"
            )
        if closing <= 0:
            raise TsetmcDataError(
                f"Missing قیمت پایانی (pClosing) for TseId={tse_id}"
            )
        return ClosingPriceInfo(last_trade=last, closing=closing)

    def get_last_trade_price(self, tse_id: str) -> float:
        """Last trade from ClosingPriceInfo."""
        return self.get_closing_price_info(tse_id).last_trade

    def get_etf_nav(self, tse_id: str) -> EtfNav:
        payload = self._get_json(f"/api/Fund/GetETFByInsCode/{tse_id}")
        etf = payload.get("etf")
        if not isinstance(etf, dict):
            raise TsetmcDataError(f"Missing NAV for TseId={tse_id}")
        # pRedTran = redemption; pSubTran / similar = issue
        red = etf.get("pRedTran") or etf.get("redNav") or etf.get("pNav")
        issue = (
            etf.get("pSubTran")
            or etf.get("subNav")
            or etf.get("psubNav")
            or etf.get("pSubNav")
        )
        if red is None or float(red) <= 0:
            raise TsetmcDataError(f"Missing NAV Redemption for TseId={tse_id}")
        issue_f = float(issue) if issue is not None and float(issue) > 0 else None
        return EtfNav(redemption=float(red), issue=issue_f)

    def get_legal_volumes(self, tse_id: str) -> ClientTypeVolumes:
        """
        Legal buy/sell from live ClientType (حقوقی = buy_N / sell_N).

        Uses the same live feed as the TSETMC instrument page. Does not
        fall back to history — if the page shows 0, we return 0.
        """
        payload = self._get_json(f"/api/ClientType/GetClientType/{tse_id}/1/0")
        ct = payload.get("clientType")
        if not isinstance(ct, dict):
            raise TsetmcDataError(f"Missing ClientType for TseId={tse_id}")
        buy_n = float(ct.get("buy_N_Volume") or 0)
        sell_n = float(ct.get("sell_N_Volume") or 0)
        return ClientTypeVolumes(buy_n_volume=buy_n, sell_n_volume=sell_n)


def create_client() -> TsetmcClient:
    return TsetmcClient()
