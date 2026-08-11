"""Persistent symbol → insCode cache to skip repeated TSETMC searches."""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

import config

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_CACHE: dict[str, str] | None = None

# Seed known MAIN-board codes (Excel float often corrupts the last digits).
# Retail (خرده فروشی) codes are cached as "<symbol>#retail" after first resolve.
_SEED: dict[str, str] = {
    "آتش": "56987424987755487",
}


def _cache_path() -> Path:
    return config.DATA_DIR / "inscode_cache.json"


def _load() -> dict[str, str]:
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    path = _cache_path()
    data: dict[str, str] = dict(_SEED)
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                for key, value in raw.items():
                    if isinstance(key, str) and value is not None:
                        text = str(value).strip()
                        if text.isdigit():
                            data[key.strip()] = text
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Could not read insCode cache %s: %s", path, exc)
    _CACHE = data
    return _CACHE


def get_cached_ins_code(symbol: str) -> str | None:
    with _LOCK:
        return _load().get(symbol.strip())


def put_cached_ins_code(symbol: str, ins_code: str) -> None:
    code = str(ins_code).strip()
    if not code.isdigit():
        return
    key = symbol.strip()
    with _LOCK:
        cache = _load()
        if cache.get(key) == code:
            return
        cache[key] = code
        path = _cache_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(cache, ensure_ascii=False, indent=2, sort_keys=True)
                + "\n",
                encoding="utf-8",
            )
        except OSError as exc:
            logger.warning("Could not write insCode cache %s: %s", path, exc)
