from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

CACHE_DIR = Path(os.getenv("SERPAPI_CACHE_DIR", ".cache/serpapi"))
DEFAULT_TTL_SECONDS = int(os.getenv("SERPAPI_CACHE_TTL_SECONDS", str(60 * 60 * 24)))


def get_cached_payload(cache_key: str, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> dict[str, Any] | None:
    path = _cache_path(cache_key)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    created_at = float(payload.get("created_at", 0))
    if time.time() - created_at > ttl_seconds:
        return None
    data = payload.get("data")
    return data if isinstance(data, dict) else None


def set_cached_payload(cache_key: str, data: dict[str, Any]) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _cache_path(cache_key)
    payload = {"created_at": time.time(), "data": data}
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def serpapi_cache_key(params: dict[str, Any]) -> str:
    safe_params = {key: value for key, value in params.items() if key != "api_key"}
    raw = json.dumps(safe_params, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _cache_path(cache_key: str) -> Path:
    return CACHE_DIR / f"{cache_key}.json"
