"""TSETMC CDN HTTP client with retries, timeouts, proxy, and raw dumps."""

from __future__ import annotations

import json
import logging
import re
import socket
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

import httpx
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

import config

logger = logging.getLogger(__name__)


class TsetmcError(Exception):
    """Base error for TSETMC client failures."""


class TsetmcNetworkError(TsetmcError):
    """Network / timeout / transport failure."""


class TsetmcResponseError(TsetmcError):
    """Unexpected HTTP status or non-JSON / blocked body."""


class TsetmcDataError(TsetmcError):
    """Required field missing or unusable in a successful response."""


@dataclass(frozen=True)
class SearchHit:
    ins_code: str
    symbol: str
    name: str
    flow: int | None
    flow_title: str
    market_title: str
    last_date: int | None
    is_active: bool


@dataclass(frozen=True)
class EtfNav:
    redemption: float
    issue: float | None
    deven: int | None
    heven: int | None


@dataclass(frozen=True)
class ClientTypeVolumes:
    buy_n_volume: float
    sell_n_volume: float
    buy_i_volume: float
    sell_i_volume: float
    as_of_date: int | None = None
    source: str = "live"


@dataclass(frozen=True)
class HealthCheckResult:
    dns_ok: bool
    dns_ips: tuple[str, ...]
    dns_error: str | None
    https_ok: bool
    https_error: str | None
    api_ok: bool
    api_status_code: int | None
    api_latency_ms: float | None
    api_error: str | None
    proxy_configured: bool

    @property
    def ok(self) -> bool:
        return self.dns_ok and self.https_ok and self.api_ok


def symbol_slug(symbol: str) -> str:
    if symbol in config.SYMBOL_SLUGS:
        return config.SYMBOL_SLUGS[symbol]
    ascii_only = re.sub(r"[^A-Za-z0-9_-]+", "_", symbol).strip("_").lower()
    return ascii_only or "unknown"


def create_client() -> TsetmcClient:
    """Factory: mock, CDN-only, or preferred libs (pytse/finpy) + CDN."""
    if config.USE_MOCK_DATA:
        logger.warning("USE_MOCK_DATA=True — using mock TSETMC responses")
        return MockTsetmcClient()
    provider = getattr(config, "TSETMC_PROVIDER", "preferred")
    if provider == "cdn":
        return TsetmcClient()
    from tsetmc_libs import PreferredLibsClient

    logger.info(
        "TSETMC provider=preferred (pytse_client + finpy_tse; CDN for ETF NAV)"
    )
    return PreferredLibsClient()


class TsetmcClient:
    """Thin wrapper over known cdn.tsetmc.com JSON endpoints."""

    def __init__(
        self,
        base_url: str = config.TSETMC_BASE_URL,
        timeout: float = config.TSETMC_TIMEOUT_SECONDS,
        connect_timeout: float = config.TSETMC_CONNECT_TIMEOUT_SECONDS,
        max_retries: int = config.TSETMC_MAX_RETRIES,
        proxy: str | None = config.TSETMC_PROXY,
        save_raw: bool = config.SAVE_RAW_RESPONSES,
        raw_dir: Path = config.RAW_DIR,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.max_retries = max_retries
        self.save_raw = save_raw
        self.raw_dir = raw_dir
        self.proxy = proxy
        self.raw_label: str | None = None
        self._timeout = httpx.Timeout(timeout, connect=connect_timeout)
        client_kwargs: dict[str, Any] = {
            "base_url": self.base_url,
            "headers": config.TSETMC_HEADERS,
            "timeout": self._timeout,
            "follow_redirects": True,
            # Only use env proxies when TSETMC_PROXY is unset? Prefer explicit.
            "trust_env": False,
        }
        if proxy:
            client_kwargs["proxy"] = proxy
            logger.info("TSETMC client using proxy: %s", _redact_proxy(proxy))
        else:
            logger.info(
                "TSETMC proxy unset (direct). Set TSETMC_PROXY only if "
                "Iranian hosts are blocked (e.g. Cursor VPN)."
            )
        self._client = httpx.Client(**client_kwargs)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> TsetmcClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def get_last_trade_price(self, ins_code: int | str) -> float:
        """
        Last trade / last close for an instrument.

        Gold ETFs often return HTTP 500 on GetClosingPriceInfo; cascade through
        daily history and the legacy instinfofast board.
        """
        errors: list[str] = []
        sources = (
            ("ClosingPriceInfo", self._last_price_from_closing_info),
            ("ClosingPriceDailyList", self._last_price_from_daily_list),
            ("instinfofast", self._last_price_from_instinfofast),
        )
        for name, fetch in sources:
            try:
                value = fetch(ins_code)
                logger.info(
                    "last_price via %s for %s = %s", name, ins_code, value
                )
                return value
            except TsetmcError as exc:
                errors.append(f"{name}: {exc}")
                logger.warning(
                    "last_price %s failed for %s: %s", name, ins_code, exc
                )
        raise TsetmcDataError(
            f"Could not get last price for {ins_code}; tried: "
            + " | ".join(errors)
        )

    def _positive_price(self, raw: Any, *, ins_code: int | str, field: str) -> float:
        if raw is None:
            raise TsetmcDataError(
                f"{field} missing for insCode={ins_code}"
            )
        value = float(raw)
        if value <= 0:
            raise TsetmcDataError(
                f"Invalid {field} ({value}) for insCode={ins_code}"
            )
        return value

    def _last_price_from_closing_info(self, ins_code: int | str) -> float:
        label = self.raw_label or str(ins_code)
        payload = self._get_json(
            f"/api/ClosingPrice/GetClosingPriceInfo/{ins_code}",
            raw_name=f"closing_price_{label}",
        )
        info = payload.get("closingPriceInfo")
        if not isinstance(info, dict):
            raise TsetmcDataError(
                f"closingPriceInfo missing for insCode={ins_code}"
            )
        price = info.get("pDrCotVal")
        if price is None:
            price = info.get("pl")
        if price is None:
            price = info.get("pClosing")
        return self._positive_price(
            price, ins_code=ins_code, field="last trade price"
        )

    def _last_price_from_daily_list(self, ins_code: int | str) -> float:
        """Most recent daily close — works when ClosingPriceInfo 500s."""
        label = self.raw_label or str(ins_code)
        payload = self._get_json(
            f"/api/ClosingPrice/GetClosingPriceDailyList/{ins_code}/0",
            raw_name=f"closing_daily_{label}",
        )
        rows = payload.get("closingPriceDaily")
        if not isinstance(rows, list) or not rows:
            raise TsetmcDataError(
                f"closingPriceDaily empty for insCode={ins_code}"
            )

        def row_date(row: dict[str, Any]) -> int:
            try:
                return int(row.get("dEven") or 0)
            except (TypeError, ValueError):
                return 0

        best = max(
            (r for r in rows if isinstance(r, dict)),
            key=row_date,
            default=None,
        )
        if best is None:
            raise TsetmcDataError(
                f"closingPriceDaily has no dict rows for insCode={ins_code}"
            )
        price = best.get("pDrCotVal")
        if price is None:
            price = best.get("pClosing")
        return self._positive_price(
            price, ins_code=ins_code, field="daily last/close"
        )

    def _last_price_from_instinfofast(self, ins_code: int | str) -> float:
        """Legacy TSETMC board endpoint used by pytse_client."""
        url = (
            "http://old.tsetmc.com/tsev2/data/instinfofast.aspx"
            f"?i={ins_code}&c=0&e=1"
        )
        try:
            response = self._client.get(url)
        except httpx.HTTPError as exc:
            raise TsetmcNetworkError(
                f"instinfofast failed for {ins_code}: {exc}"
            ) from exc
        if response.status_code >= 400:
            raise TsetmcResponseError(
                f"instinfofast HTTP {response.status_code} for {ins_code}"
            )
        text = (response.text or "").strip()
        preview = text[:160].replace("\n", " ")
        if not text:
            raise TsetmcDataError(
                f"Empty instinfofast body for {ins_code}"
            )
        try:
            price_section = text.split(";")[0].split(",")
            value = float(price_section[2])
        except (IndexError, ValueError) as exc:
            raise TsetmcDataError(
                f"Could not parse last price from instinfofast for {ins_code} "
                f"(fields={len(text.split(';')[0].split(','))}, "
                f"preview={preview!r})"
            ) from exc
        return self._positive_price(
            value, ins_code=ins_code, field="instinfofast last price"
        )

    def get_etf_nav(self, ins_code: int | str) -> EtfNav:
        label = self.raw_label or str(ins_code)
        payload = self._get_json(
            f"/api/Fund/GetETFByInsCode/{ins_code}",
            raw_name=f"fund_{label}",
        )
        etf = payload.get("etf")
        if not isinstance(etf, dict):
            raise TsetmcDataError(f"etf payload missing for insCode={ins_code}")
        redemption = etf.get("pRedTran")
        issue = etf.get("pSubTran")
        if redemption is None:
            raise TsetmcDataError(
                f"NAV redemption (pRedTran) missing for insCode={ins_code}"
            )
        redemption_f = float(redemption)
        if redemption_f <= 0:
            raise TsetmcDataError(
                f"Invalid NAV redemption ({redemption_f}) for insCode={ins_code}"
            )
        issue_f: float | None
        if issue is None:
            issue_f = None
        else:
            issue_f = float(issue)
            if issue_f <= 0:
                raise TsetmcDataError(
                    f"Invalid NAV issue ({issue_f}) for insCode={ins_code}"
                )
        return EtfNav(
            redemption=redemption_f,
            issue=issue_f,
            deven=etf.get("deven"),
            heven=etf.get("hEven"),
        )

    def get_client_type(self, ins_code: int | str) -> ClientTypeVolumes:
        """
        Legal/individual volumes for an instrument (retail board).

        Intraday GetClientType is often all-zero before/after the gold session
        (Sat–Wed 11:45–18:00 Tehran); fall back to history.
        """
        from market_hours import gold_market_is_open, gold_market_status_message

        if not gold_market_is_open():
            logger.info("%s", gold_market_status_message())
            try:
                hist = self._client_type_from_cdn_history(ins_code)
                if hist.buy_n_volume > 0:
                    logger.info(
                        "client_type history (market closed) for %s date=%s "
                        "buy_N=%s",
                        ins_code,
                        hist.as_of_date,
                        hist.buy_n_volume,
                    )
                    return hist
            except TsetmcError as exc:
                logger.warning(
                    "ClientType history failed for %s while market closed: %s",
                    ins_code,
                    exc,
                )

        live = self._client_type_live(ins_code)
        if live.buy_n_volume > 0:
            return live
        logger.warning(
            "Live clientType buy_N_Volume=0 for %s; trying history",
            ins_code,
        )
        try:
            hist = self._client_type_from_cdn_history(ins_code)
        except TsetmcError as exc:
            logger.warning(
                "ClientType history failed for %s: %s", ins_code, exc
            )
            return live
        if hist.buy_n_volume > 0:
            logger.info(
                "client_type history for %s date=%s buy_N=%s sell_N=%s",
                ins_code,
                hist.as_of_date,
                hist.buy_n_volume,
                hist.sell_n_volume,
            )
            return hist
        return live

    def _client_type_live(self, ins_code: int | str) -> ClientTypeVolumes:
        label = self.raw_label or str(ins_code)
        payload = self._get_json(
            f"/api/ClientType/GetClientType/{ins_code}/1/0",
            raw_name=f"client_type_{label}",
        )
        ct = payload.get("clientType")
        if not isinstance(ct, dict):
            raise TsetmcDataError(f"clientType missing for insCode={ins_code}")
        return self._volumes_from_client_type_dict(ct, source="live")

    def _client_type_from_cdn_history(
        self, ins_code: int | str
    ) -> ClientTypeVolumes:
        """Most recent history row with buy_N_Volume > 0 (finpy-style endpoint)."""
        label = self.raw_label or str(ins_code)
        payload = self._get_json(
            f"/api/ClientType/GetClientTypeHistory/{ins_code}",
            raw_name=f"client_type_hist_{label}",
        )
        rows = payload.get("clientType")
        if isinstance(rows, dict):
            rows = [rows]
        if not isinstance(rows, list) or not rows:
            raise TsetmcDataError(
                f"clientType history empty for insCode={ins_code}"
            )

        parsed: list[tuple[int, ClientTypeVolumes]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            date_raw = (
                row.get("recDate")
                or row.get("RecDate")
                or row.get("dEven")
                or row.get("date")
                or row.get("Date")
                or 0
            )
            try:
                date_i = int(str(date_raw).replace("-", "")[:8])
            except (TypeError, ValueError):
                date_i = 0
            vols = self._volumes_from_client_type_dict(
                row, source="cdn_history", as_of_date=date_i or None
            )
            parsed.append((date_i, vols))

        if not parsed:
            raise TsetmcDataError(
                f"clientType history unreadable for insCode={ins_code}"
            )

        with_buy = [(d, v) for d, v in parsed if v.buy_n_volume > 0]
        pool = with_buy or parsed
        pool.sort(key=lambda item: item[0], reverse=True)
        return pool[0][1]

    def _volumes_from_client_type_dict(
        self,
        ct: dict[str, Any],
        *,
        source: str,
        as_of_date: int | None = None,
    ) -> ClientTypeVolumes:
        def pick(*keys: str) -> Any:
            for key in keys:
                if key in ct and ct[key] is not None:
                    return ct[key]
            return None

        try:
            buy_n = float(
                pick("buy_N_Volume", "Buy_N_Volume", "buy_N_Vol") or 0
            )
            sell_n = float(
                pick("sell_N_Volume", "Sell_N_Volume", "sell_N_Vol") or 0
            )
            buy_i = float(
                pick("buy_I_Volume", "Buy_I_Volume", "buy_I_Vol") or 0
            )
            sell_i = float(
                pick("sell_I_Volume", "Sell_I_Volume", "sell_I_Vol") or 0
            )
        except (TypeError, ValueError) as exc:
            raise TsetmcDataError(
                f"clientType volumes unreadable ({source}): {exc}"
            ) from exc
        return ClientTypeVolumes(
            buy_n_volume=buy_n,
            sell_n_volume=sell_n,
            buy_i_volume=buy_i,
            sell_i_volume=sell_i,
            as_of_date=as_of_date,
            source=source,
        )

    def search_instruments(self, query: str) -> list[SearchHit]:
        encoded = quote(query, safe="")
        payload = self._get_json(
            f"/api/Instrument/GetInstrumentSearch/{encoded}",
            raw_name=f"search_{symbol_slug(query)}",
        )
        rows = payload.get("instrumentSearch")
        if rows is None:
            return []
        if not isinstance(rows, list):
            raise TsetmcDataError(
                f"instrumentSearch has unexpected type for query={query!r}"
            )
        hits: list[SearchHit] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            ins_code = str(row.get("insCode") or "").strip()
            symbol = str(row.get("lVal18AFC") or "").strip()
            if not ins_code or not symbol:
                continue
            last_date = row.get("lastDate")
            try:
                last_date_i = int(last_date) if last_date is not None else None
            except (TypeError, ValueError):
                last_date_i = None
            flow_raw = row.get("flow")
            try:
                flow = int(flow_raw) if flow_raw is not None else None
            except (TypeError, ValueError):
                flow = None
            hits.append(
                SearchHit(
                    ins_code=ins_code,
                    symbol=symbol,
                    name=str(row.get("lVal30") or "").strip(),
                    flow=flow,
                    flow_title=str(row.get("flowTitle") or "").strip(),
                    market_title=str(row.get("cgrValCotTitle") or "").strip(),
                    last_date=last_date_i,
                    is_active=bool(last_date_i and last_date_i != 0),
                )
            )
        return hits

    def get_instrument_info(self, ins_code: int | str) -> dict[str, Any]:
        payload = self._get_json(
            f"/api/Instrument/GetInstrumentInfo/{ins_code}",
            raw_name=f"instrument_info_{ins_code}",
        )
        info = payload.get("instrumentInfo")
        if not isinstance(info, dict):
            raise TsetmcDataError(
                f"instrumentInfo missing for insCode={ins_code}"
            )
        return info

    def health_check(self, probe_ins_code: int | str = config.POC_TSE_ID) -> HealthCheckResult:
        host = urlparse(self.base_url).hostname or "cdn.tsetmc.com"
        dns_ok = False
        dns_ips: tuple[str, ...] = ()
        dns_error: str | None = None
        try:
            infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
            dns_ips = tuple(sorted({item[4][0] for item in infos}))
            dns_ok = bool(dns_ips)
        except OSError as exc:
            dns_error = str(exc)

        https_ok = False
        https_error: str | None = None
        api_ok = False
        api_status: int | None = None
        api_latency_ms: float | None = None
        api_error: str | None = None

        # Prefer lightweight / reliable probes. ClosingPrice for a single
        # commodity ETF can return HTTP 500 even when the CDN is healthy.
        probes = [
            "/api/StaticData/GetTime",
            "/api/Instrument/GetInstrumentSearch/%D9%81%D9%88%D9%84%D8%A7%D8%AF",
            f"/api/Instrument/GetInstrumentInfo/{probe_ins_code}",
            f"/api/ClosingPrice/GetClosingPriceInfo/{probe_ins_code}",
        ]

        if dns_ok:
            last_err: str | None = None
            for path in probes:
                t0 = time.perf_counter()
                try:
                    response = self._client.get(path)
                    latency = (time.perf_counter() - t0) * 1000.0
                    https_ok = True
                    api_status = response.status_code
                    api_latency_ms = latency
                    if response.status_code == 200:
                        try:
                            payload = response.json()
                            if isinstance(payload, dict) and payload:
                                api_ok = True
                                api_error = None
                                logger.info(
                                    "Health probe OK: %s (%.0f ms)",
                                    path,
                                    latency,
                                )
                                break
                            last_err = f"{path}: empty JSON"
                        except ValueError as exc:
                            last_err = f"{path}: non-JSON ({exc})"
                    else:
                        last_err = f"{path}: HTTP {response.status_code}"
                        logger.warning("Health probe soft-fail: %s", last_err)
                except httpx.TimeoutException as exc:
                    https_error = f"Timeout: {exc}"
                    last_err = https_error
                except httpx.HTTPError as exc:
                    https_error = str(exc)
                    last_err = https_error
            if not api_ok:
                api_error = last_err or "All probes failed"
        else:
            https_error = "Skipped (DNS failed)"
            api_error = "Skipped (DNS failed)"

        return HealthCheckResult(
            dns_ok=dns_ok,
            dns_ips=dns_ips,
            dns_error=dns_error,
            https_ok=https_ok,
            https_error=https_error,
            api_ok=api_ok,
            api_status_code=api_status,
            api_latency_ms=api_latency_ms,
            api_error=api_error,
            proxy_configured=bool(self.proxy),
        )

    def _get_json(self, path: str, *, raw_name: str) -> dict[str, Any]:
        attempt = retry(
            reraise=True,
            stop=stop_after_attempt(self.max_retries),
            wait=wait_exponential(
                multiplier=config.TSETMC_RETRY_WAIT_SECONDS,
                min=config.TSETMC_RETRY_WAIT_SECONDS,
                max=20,
            ),
            retry=retry_if_exception_type(
                (TsetmcNetworkError, TsetmcResponseError)
            ),
            before_sleep=before_sleep_log(logger, logging.WARNING),
        )(self._get_json_once)
        data = attempt(path)
        self._maybe_save_raw(raw_name, data)
        return data

    def _get_json_once(self, path: str) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        logger.debug("GET %s", url)
        try:
            response = self._client.get(path)
        except httpx.TimeoutException as exc:
            raise TsetmcNetworkError(f"Timeout calling {url}: {exc}") from exc
        except httpx.HTTPError as exc:
            raise TsetmcNetworkError(f"HTTP error calling {url}: {exc}") from exc

        text = response.text or ""
        if response.status_code >= 500:
            raise TsetmcResponseError(
                f"Server error {response.status_code} for {url}"
            )
        if response.status_code >= 400:
            raise TsetmcResponseError(
                f"Client error {response.status_code} for {url}: {text[:200]}"
            )
        lowered = text.lower()
        if "مسدود" in text or "دسترسی شما" in text or "general error detected" in lowered:
            raise TsetmcResponseError(f"TSETMC blocked response for {url}")
        try:
            data = response.json()
        except ValueError as exc:
            raise TsetmcResponseError(
                f"Non-JSON response for {url}: {text[:200]!r}"
            ) from exc
        if not isinstance(data, dict):
            raise TsetmcResponseError(
                f"Unexpected JSON root type for {url}: {type(data).__name__}"
            )
        return data

    def _maybe_save_raw(self, raw_name: str, data: dict[str, Any]) -> None:
        if not self.save_raw:
            return
        try:
            self.raw_dir.mkdir(parents=True, exist_ok=True)
            safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", raw_name)
            path = self.raw_dir / f"{safe}.json"
            path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            logger.debug("Saved raw response: %s", path)
        except OSError as exc:
            logger.warning("Failed to save raw response %s: %s", raw_name, exc)


class MockTsetmcClient(TsetmcClient):
    """Offline client returning config.MOCK_ATASH for calculation tests."""

    def __init__(self) -> None:
        # Do not open a live httpx session.
        self.base_url = config.TSETMC_BASE_URL
        self.max_retries = 1
        self.save_raw = False
        self.raw_dir = config.RAW_DIR
        self.proxy = None
        self.raw_label = None
        self._client = None  # type: ignore[assignment]
        self._mock = config.MOCK_ATASH

    def close(self) -> None:
        return None

    def get_last_trade_price(self, ins_code: int | str) -> float:
        self._require_atash(ins_code)
        return float(self._mock["last_price"])

    def get_etf_nav(self, ins_code: int | str) -> EtfNav:
        self._require_atash(ins_code)
        issue = self._mock["nav_issue_redemption"]
        return EtfNav(
            redemption=float(self._mock["nav_redemption"]),
            issue=None if issue is None else float(issue),
            deven=None,
            heven=None,
        )

    def get_client_type(self, ins_code: int | str) -> ClientTypeVolumes:
        return ClientTypeVolumes(
            buy_n_volume=float(self._mock["legal_buy_volume"]),
            sell_n_volume=float(self._mock["legal_sell_volume"]),
            buy_i_volume=0.0,
            sell_i_volume=0.0,
        )

    def search_instruments(self, query: str) -> list[SearchHit]:
        market = str(self._mock["market_symbol"])
        fund = str(self._mock["symbol"])
        market_code = str(self._mock["market_tse_id"])
        fund_code = str(self._mock["tse_id"])
        hits = [
            SearchHit(
                ins_code=fund_code,
                symbol=fund,
                name=f"صندوق {fund}",
                flow=3,
                flow_title="خرده فروشی",
                market_title="خرده فروشی",
                last_date=20260810,
                is_active=True,
            ),
            SearchHit(
                ins_code=market_code,
                symbol=market,
                name=f"بازار {fund}",
                flow=1,
                flow_title="بورس کالا",
                market_title="بازار معاملات اصلی",
                last_date=20260810,
                is_active=True,
            ),
        ]
        q = query.strip()
        return [h for h in hits if q in h.symbol or h.symbol in q or q == fund]

    def get_instrument_info(self, ins_code: int | str) -> dict[str, Any]:
        if str(ins_code) == str(self._mock["market_tse_id"]):
            return {"lVal18AFC": self._mock["market_symbol"], "insCode": str(ins_code)}
        if str(ins_code) == str(self._mock["tse_id"]):
            return {"lVal18AFC": self._mock["symbol"], "insCode": str(ins_code)}
        raise TsetmcDataError(f"Mock instrumentInfo missing for {ins_code}")

    def health_check(self, probe_ins_code: int | str = config.POC_TSE_ID) -> HealthCheckResult:
        return HealthCheckResult(
            dns_ok=True,
            dns_ips=("127.0.0.1",),
            dns_error=None,
            https_ok=True,
            https_error=None,
            api_ok=True,
            api_status_code=200,
            api_latency_ms=0.0,
            api_error=None,
            proxy_configured=False,
        )

    def _require_atash(self, ins_code: int | str) -> None:
        if str(ins_code) not in {str(self._mock["tse_id"]), str(config.POC_TSE_ID)}:
            raise TsetmcDataError(
                f"Mock mode only supports آتش (got insCode={ins_code})"
            )


def _redact_proxy(proxy: str) -> str:
    """Hide credentials when logging proxy URL."""
    try:
        parsed = urlparse(proxy)
        if parsed.username or parsed.password:
            host = parsed.hostname or ""
            port = f":{parsed.port}" if parsed.port else ""
            return f"{parsed.scheme}://***:***@{host}{port}"
    except Exception:
        return "***"
    return proxy
