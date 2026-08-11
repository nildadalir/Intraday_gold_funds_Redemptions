"""Load application settings from config.yaml (with ${ENV_VAR} expansion)."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent / ".env")
except ImportError:
    pass

PROJECT_ROOT = Path(__file__).resolve().parent
CONFIG_YAML_PATH = PROJECT_ROOT / "config.yaml"

_ENV_PATTERN = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")


def _expand(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _expand(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand(v) for v in value]
    if isinstance(value, str):
        match = _ENV_PATTERN.match(value.strip())
        if match:
            return os.environ.get(match.group(1)) or None
        return value
    return value


def _load_yaml() -> dict[str, Any]:
    if not CONFIG_YAML_PATH.exists():
        raise FileNotFoundError(f"Missing config.yaml at {CONFIG_YAML_PATH}")
    with CONFIG_YAML_PATH.open(encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    if not isinstance(raw, dict):
        raise ValueError("config.yaml root must be a mapping")
    return _expand(raw)


_CFG = _load_yaml()


def _path(key: str, default: str) -> Path:
    relative = (_CFG.get("paths") or {}).get(key, default)
    path = Path(relative)
    return path if path.is_absolute() else PROJECT_ROOT / path


DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = _path("raw_dir", "data/raw")
DEBUG_DIR = _path("debug_dir", "data/debug")
OUTPUT_DIR = _path("output_dir", "output")
LOG_DIR = _path("log_dir", "logs")
REPORT_DIR = _path("report_dir", "report")
EXCEL_PATH = _path("excel", "data/طلا.xlsx")
TEMPLATE_PATH = _path("report_template", "report/template.html")
REPORT_TEMPLATE_NAME = Path(
    (_CFG.get("paths") or {}).get("report_template", "report/template.html")
).name

_BATCH = _CFG.get("batch") or {}
BATCH_ENABLED = bool(_BATCH.get("enabled", True))
BATCH_MAX_WORKERS = max(1, int(_BATCH.get("max_workers", 4)))

_API = _CFG.get("api") or {}
TSETMC_PROVIDER = str(_API.get("provider", "preferred"))
TSETMC_BASE_URL = str(_API.get("base_url", "https://cdn.tsetmc.com"))
TSETMC_TIMEOUT_SECONDS = float(_API.get("timeout_seconds", 15))
TSETMC_CONNECT_TIMEOUT_SECONDS = float(_API.get("connect_timeout_seconds", 8))
TSETMC_LIB_TIMEOUT_SECONDS = float(_API.get("lib_timeout_seconds", 8))
SEARCH_LEGACY_FALLBACK = bool(_API.get("search_legacy_fallback", True))
FAIL_FAST_ON_TIMEOUT = bool(_API.get("fail_fast_on_timeout", True))
_RETRY = _API.get("retry") or {}
TSETMC_MAX_RETRIES = int(_RETRY.get("max_attempts", 2))
TSETMC_RETRY_WAIT_SECONDS = float(_RETRY.get("backoff_seconds", 1))
TSETMC_PROXY: str | None = _API.get("proxy")
SAVE_RAW_RESPONSES = bool(_API.get("save_raw_responses", False))
TSETMC_HEADERS: dict[str, str] = dict(_API.get("headers") or {})

_FEATURES = _CFG.get("features") or {}
USE_MOCK_DATA = bool(_FEATURES.get("use_mock_data", False))

_MH = _CFG.get("market_hours") or {}
MARKET_TIMEZONE = str(_MH.get("timezone", "Asia/Tehran"))
MARKET_OPEN_WEEKDAYS: list[str] = list(
    _MH.get("open_weekdays")
    or ["saturday", "sunday", "monday", "tuesday", "wednesday"]
)
MARKET_OPEN_TIME = str(_MH.get("open_time", "11:45"))
MARKET_CLOSE_TIME = str(_MH.get("close_time", "18:00"))

_POC = _CFG.get("poc") or {}
POC_SYMBOL = str(_POC.get("symbol", "آتش"))
POC_TSE_ID = int(float(str(_POC.get("tse_id", "56987424987755400"))))

_MOCK = (_CFG.get("mock") or {}).get("atash") or {}
MOCK_ATASH = dict(_MOCK)

_VALIDATION = _CFG.get("validation") or {}
FORBIDDEN_MOCK_MARKET_TSE_IDS = frozenset(
    str(x) for x in (_VALIDATION.get("forbidden_mock_market_tse_ids") or [])
)

SYMBOL_SLUGS: dict[str, str] = dict(
    ((_CFG.get("symbols") or {}).get("slugs") or {})
)


def reload() -> None:
    """Reload config.yaml (mainly for tests)."""
    global _CFG
    _CFG = _load_yaml()
