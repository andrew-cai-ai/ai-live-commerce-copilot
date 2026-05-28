from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

load_dotenv()

DEFAULT_CAD_TO_CNY = 4.90
FX_CACHE_PATH = Path("data/fx_rate_cache.json")
FX_CACHE_TTL_SECONDS = 60 * 60 * 12


@dataclass(frozen=True)
class FxRate:
    base_currency: str
    target_currency: str
    rate: float
    source: str
    timestamp: float
    warning: str = ""


class FxRateService:
    def get_cad_to_cny_rate(self) -> FxRate:
        api_url = os.getenv("FX_API_URL", "").strip()
        api_key = os.getenv("FX_API_KEY", "").strip()

        if api_url:
            try:
                payload = self._fetch_payload(api_url, api_key)
                rate = _extract_rate(payload)
                if rate > 0:
                    fx_rate = FxRate("CAD", "CNY", round(rate, 6), "live_fx_api", time.time())
                    _write_cached_rate(fx_rate)
                    return fx_rate
                raise ValueError("FX response did not include a CAD to CNY rate.")
            except Exception as exc:
                cached = _read_cached_rate()
                if cached:
                    return FxRate(
                        "CAD",
                        "CNY",
                        cached.rate,
                        "cached_fx_rate",
                        cached.timestamp,
                        f"FX API failed, using cached CAD/CNY rate: {exc}",
                    )
                return FxRate(
                    "CAD",
                    "CNY",
                    DEFAULT_CAD_TO_CNY,
                    "manual_fallback",
                    time.time(),
                    f"FX API failed and no cached rate exists; using manual fallback 4.90: {exc}",
                )

        cached = _read_cached_rate()
        if cached:
            return FxRate("CAD", "CNY", cached.rate, "cached_fx_rate", cached.timestamp)
        return FxRate(
            "CAD",
            "CNY",
            DEFAULT_CAD_TO_CNY,
            "manual_fallback",
            time.time(),
            "FX_API_URL is not configured; using manual fallback CAD/CNY rate 4.90.",
        )

    def _fetch_payload(self, api_url: str, api_key: str) -> dict[str, Any]:
        url = (
            api_url.replace("{base}", "CAD")
            .replace("{target}", "CNY")
            .replace("{symbols}", "CNY")
            .replace("{api_key}", api_key)
        )
        params = {}
        if "{" not in api_url and api_key:
            params = {"base": "CAD", "symbols": "CNY", "apikey": api_key, "access_key": api_key}
        response = requests.get(url, params=params, timeout=8)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("FX API response was not a JSON object.")
        return payload


def _extract_rate(payload: dict[str, Any]) -> float:
    candidates = [
        payload.get("cad_to_cny"),
        payload.get("CADCNY"),
        payload.get("conversion_rate"),
        payload.get("result"),
        payload.get("rate"),
    ]
    rates = payload.get("rates")
    if isinstance(rates, dict):
        candidates.extend([rates.get("CNY"), rates.get("CADCNY")])
    data = payload.get("data")
    if isinstance(data, dict):
        candidates.extend([data.get("CNY"), data.get("CADCNY"), data.get("cad_to_cny")])
    for candidate in candidates:
        try:
            rate = float(candidate)
        except (TypeError, ValueError):
            continue
        if rate > 0:
            return rate
    return 0.0


def _read_cached_rate() -> FxRate | None:
    try:
        payload = json.loads(FX_CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if time.time() - float(payload.get("timestamp", 0)) > FX_CACHE_TTL_SECONDS:
        return None
    try:
        rate = float(payload["rate"])
    except (KeyError, TypeError, ValueError):
        return None
    return FxRate("CAD", "CNY", rate, "cached_fx_rate", float(payload.get("timestamp", 0)))


def _write_cached_rate(rate: FxRate) -> None:
    FX_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    FX_CACHE_PATH.write_text(
        json.dumps({"rate": rate.rate, "timestamp": time.time()}, ensure_ascii=False),
        encoding="utf-8",
    )
