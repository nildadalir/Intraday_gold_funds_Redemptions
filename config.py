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
OUTPUT_DIR = _path("output_dir", "output")
LOG_DIR = _path("log_dir", "logs")
REPORT_DIR = _path("report_dir", "report")
EXCEL_PATH = _path("excel", "data/طلا.xlsx")
TEMPLATE_PATH = _path("report_template", "report/template.html")

_BATCH = _CFG.get("batch") or {}
BATCH_MAX_WORKERS = max(1, int(_BATCH.get("max_workers", 1)))

_LOG = _CFG.get("logging") or {}
LOG_MAX_BYTES = int(_LOG.get("max_bytes", 1_048_576))
LOG_BACKUP_COUNT = int(_LOG.get("backup_count", 5))

_API = _CFG.get("api") or {}
TSETMC_PROVIDER = str(_API.get("provider", "preferred"))
TSETMC_BASE_URL = str(_API.get("base_url", "https://cdn.tsetmc.com"))
TSETMC_TIMEOUT_SECONDS = float(_API.get("timeout_seconds", 15))
TSETMC_CONNECT_TIMEOUT_SECONDS = float(_API.get("connect_timeout_seconds", 8))
TSETMC_LIB_TIMEOUT_SECONDS = float(_API.get("lib_timeout_seconds", 8))
FAIL_FAST_ON_TIMEOUT = bool(_API.get("fail_fast_on_timeout", True))
_RETRY = _API.get("retry") or {}
TSETMC_MAX_RETRIES = int(_RETRY.get("max_attempts", 2))
TSETMC_RETRY_WAIT_SECONDS = float(_RETRY.get("backoff_seconds", 1))
TSETMC_PROXY: str | None = _API.get("proxy")
SAVE_RAW_RESPONSES = bool(_API.get("save_raw_responses", False))
TSETMC_HEADERS: dict[str, str] = dict(_API.get("headers") or {})

_POC = _CFG.get("poc") or {}
POC_ASSET_ID = str(_POC.get("asset_id", "30018"))
POC_INSTRUMENT = str(_POC.get("instrument", "آتش"))
POC_MAIN_TSE_ID = str(_POC.get("main_tse_id", "56987424987755487"))
POC_MARKET_TSE_ID = str(_POC.get("market_tse_id", "32651481214999246"))


def reload() -> None:
    global _CFG
    _CFG = _load_yaml()
