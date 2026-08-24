"""Load application settings from config.yaml (with ${ENV_VAR} expansion)."""

from __future__ import annotations

import logging
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


OUTPUT_DIR = _path("output_dir", "output")
LOG_DIR = _path("log_dir", "logs")
REPORT_DIR = _path("report_dir", "report")
EXCEL_PATH = _path("excel", "data/طلا.xlsx")
TEMPLATE_PATH = _path("report_template", "report/template.html")

_DB = _CFG.get("db") or {}
DB_SERVER = str(_DB.get("server") or "")
DB_DATABASE = str(_DB.get("database", ""))
DB_DRIVER = str(_DB.get("driver", "ODBC Driver 17 for SQL Server"))
DB_TRUSTED_CONNECTION = bool(_DB.get("trusted_connection", False))
DB_USE_DB = bool(_DB.get("use_db", False))
DB_USERNAME: str | None = _DB.get("username")
DB_PASSWORD: str | None = _DB.get("password")
_query_rel = str(_DB.get("query_file", "src/SQL/FundsList.sql"))
_query_path = Path(_query_rel)
DB_QUERY_PATH = _query_path if _query_path.is_absolute() else PROJECT_ROOT / _query_path

_BATCH = _CFG.get("batch") or {}
BATCH_MAX_WORKERS = max(1, int(_BATCH.get("max_workers", 1)))

_LOG = _CFG.get("logging") or {}
LOG_MAX_BYTES = int(_LOG.get("max_bytes", 1_048_576))
LOG_BACKUP_COUNT = int(_LOG.get("backup_count", 5))
_http_level = str(_LOG.get("http_level", "WARNING")).upper()
LOG_HTTP_LEVEL = getattr(logging, _http_level, logging.WARNING)

_API = _CFG.get("api") or {}
TSETMC_BASE_URL = str(_API.get("base_url", "https://cdn.tsetmc.com"))
TSETMC_TIMEOUT_SECONDS = float(_API.get("timeout_seconds", 15))
TSETMC_CONNECT_TIMEOUT_SECONDS = float(_API.get("connect_timeout_seconds", 8))
_RETRY = _API.get("retry") or {}
TSETMC_MAX_RETRIES = int(_RETRY.get("max_attempts", 2))
TSETMC_RETRY_WAIT_SECONDS = float(_RETRY.get("backoff_seconds", 1))
TSETMC_PROXY: str | None = _API.get("proxy")
TSETMC_HEADERS: dict[str, str] = dict(_API.get("headers") or {})
