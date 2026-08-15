"""TSETMC client — TseId-based last price, ETF NAV, legal (حقوقی) volume."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

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
class EtfNav:
    redemption: float
    issue: float | None


@dataclass(frozen=True)
class ClientTypeVolumes:
    buy_n_volume: float
    sell_n_volume: float
    source: str = "cdn_live"


class TsetmcClient:
    def __init__(
        self,
        *,
        base_url: str = config.TSETMC_BASE_URL,
        timeout: float = config.TSETMC_TIMEOUT_SECONDS,
        connect_timeout: float = config.TSETMC_CONNECT_TIMEOUT_SECONDS,
        max_retries: int = config.TSETMC_MAX_RETRIES,
        proxy: str | None = config.TSETMC_PROXY,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.max_retries = max(1, max_retries)
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
        else:
            logger.info("TSETMC proxy unset (direct)")
        self._client = httpx.Client(**kwargs)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> TsetmcClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _get_json(self, path: str) -> dict[str, Any]:
        @retry(
            reraise=True,
            stop=stop_after_attempt(self.max_retries),
            wait=wait_exponential(
                multiplier=config.TSETMC_RETRY_WAIT_SECONDS, min=1, max=8
            ),
            retry=retry_if_exception_type(TsetmcNetworkError),
        )
        def _once() -> dict[str, Any]:
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
                raise TsetmcDataError(
                    f"HTTP {response.status_code} for {path}"
                )
            try:
                payload = response.json()
            except ValueError as exc:
                raise TsetmcDataError(f"Non-JSON response for {path}") from exc
            if not isinstance(payload, dict):
                raise TsetmcDataError(f"Unexpected JSON root for {path}")
            return payload

        return _once()

    def get_last_trade_price(self, tse_id: str) -> float:
        """Last trade from ClosingPriceInfo; fall back to daily list."""
        errors: list[str] = []
        try:
            payload = self._get_json(
                f"/api/ClosingPrice/GetClosingPriceInfo/{tse_id}"
            )
            info = payload.get("closingPriceInfo")
            if isinstance(info, dict):
                raw = info.get("pDrCotVal") or info.get("pl") or info.get("pClosing")
                if raw is not None and float(raw) > 0:
                    return float(raw)
            errors.append("ClosingPriceInfo empty/invalid")
        except TsetmcError as exc:
            errors.append(str(exc))
            if config.FAIL_FAST_ON_TIMEOUT and "Timeout" in str(exc):
                raise TsetmcDataError(
                    f"Could not get last price for {tse_id}: {exc}"
                ) from exc

        try:
            payload = self._get_json(
                f"/api/ClosingPrice/GetClosingPriceDailyList/{tse_id}/0"
            )
            rows = payload.get("closingPriceDaily") or []
            if isinstance(rows, list) and rows:
                best = max(
                    (r for r in rows if isinstance(r, dict)),
                    key=lambda r: int(r.get("dEven") or 0),
                    default=None,
                )
                if best:
                    raw = best.get("pDrCotVal") or best.get("pClosing")
                    if raw is not None and float(raw) > 0:
                        return float(raw)
            errors.append("ClosingPriceDailyList empty/invalid")
        except TsetmcError as exc:
            errors.append(str(exc))

        # pytse instinfofast fallback
        try:
            return self._last_price_instinfofast(tse_id)
        except TsetmcError as exc:
            errors.append(str(exc))

        raise TsetmcDataError(
            f"Missing Last Trade Price for TseId={tse_id}: " + " | ".join(errors)
        )

    def _last_price_instinfofast(self, tse_id: str) -> float:
        import requests

        url = (
            "http://old.tsetmc.com/tsev2/data/instinfofast.aspx"
            f"?i={tse_id}&c=0&e=1"
        )
        try:
            session = requests.Session()
            session.trust_env = False
            if config.TSETMC_PROXY:
                session.proxies = {
                    "http": config.TSETMC_PROXY,
                    "https": config.TSETMC_PROXY,
                }
            response = session.get(
                url, timeout=config.TSETMC_LIB_TIMEOUT_SECONDS, headers=config.TSETMC_HEADERS
            )
            response.raise_for_status()
            price = float(response.text.split(";")[0].split(",")[2])
            if price <= 0:
                raise TsetmcDataError("non-positive last price from instinfofast")
            return price
        except Exception as exc:
            raise TsetmcDataError(f"instinfofast failed: {exc}") from exc

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
        return ClientTypeVolumes(
            buy_n_volume=buy_n, sell_n_volume=sell_n, source="cdn_live"
        )


def create_client() -> TsetmcClient:
    return TsetmcClient()
