from __future__ import annotations

import json
import logging
import os
from typing import Any

import requests
from dotenv import load_dotenv

from app.providers.base import PriceResult, SocialSignal, TaobaoProduct

load_dotenv()

logger = logging.getLogger(__name__)

TAOBAO_PRODUCT_API_URL = os.getenv("TAOBAO_PRODUCT_API_URL", "")
TAOBAO_PRODUCT_API_KEY = os.getenv("TAOBAO_PRODUCT_API_KEY", "")
TAOBAO_PRODUCT_API_TIMEOUT = float(os.getenv("TAOBAO_PRODUCT_API_TIMEOUT", "10"))


class TaobaoProvider:
    name = "taobao"

    @property
    def enabled(self) -> bool:
        return os.getenv("ENABLE_TAOBAO_PROVIDER", "true").lower() == "true"

    def search_product(self, product_name: str, brand: str = "Arc'teryx") -> list[PriceResult]:
        try:
            products = self.search_taobao_products(f"{brand} {product_name}")
        except Exception as exc:
            logger.warning("Fallback to mock data: Taobao product API failed: %s", exc)
            return []

        results: list[PriceResult] = []
        for product in products[:5]:
            results.append(
                PriceResult(
                    title=product.source_product_title,
                    platform="淘宝",
                    price=product.price,
                    currency="CNY",
                    url="",
                    image_url=product.source_product_image,
                    source=product.supplier_name or "淘宝商品池",
                    confidence=0.9 if product.target_product_status else 0.78,
                )
            )
        return results

    def parse_pasted_json(self, pasted_json: str) -> list[TaobaoProduct]:
        if not pasted_json.strip():
            return []
        payload = json.loads(pasted_json)
        return parse_taobao_products(payload)

    def search_taobao_products(self, query: str = "Arc'teryx") -> list[TaobaoProduct]:
        if not self.enabled:
            return []
        if not TAOBAO_PRODUCT_API_URL:
            raise RuntimeError("TAOBAO_PRODUCT_API_URL is not configured.")

        headers = {"Accept": "application/json"}
        if TAOBAO_PRODUCT_API_KEY:
            headers["Authorization"] = f"Bearer {TAOBAO_PRODUCT_API_KEY}"

        response = requests.get(
            TAOBAO_PRODUCT_API_URL,
            params={"q": query, "keyword": query},
            headers=headers,
            timeout=TAOBAO_PRODUCT_API_TIMEOUT,
        )
        response.raise_for_status()
        logger.info("Using real Taobao product API results")
        return parse_taobao_products(response.json())

    def search_social_signals(self, product_name: str, brand: str = "Arc'teryx") -> list[SocialSignal]:
        return []


def parse_taobao_products(payload: Any) -> list[TaobaoProduct]:
    records = _extract_product_records(payload)
    products: list[TaobaoProduct] = []
    for record in records:
        product = _map_taobao_product(record)
        if product:
            products.append(product)
    return products


def _extract_product_records(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []

    data = payload.get("data")
    if isinstance(data, dict) and isinstance(data.get("dataSource"), list):
        return [item for item in data["dataSource"] if isinstance(item, dict)]

    for key in ("data", "items", "products", "result", "list", "records"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            nested = _extract_product_records(value)
            if nested:
                return nested
    return []


def _map_taobao_product(record: dict[str, Any]) -> TaobaoProduct | None:
    title = _clean_text(record.get("sourceProductTitle"))
    if not title:
        return None
    status = _clean_text(record.get("targetProductStatus"))
    if not _is_active_status(record.get("targetProductStatus")):
        return None

    return TaobaoProduct(
        source_product_title=title,
        source_product_image=_clean_text(record.get("sourceProductImage")),
        source_product_id=_clean_text(record.get("sourceProductId")),
        price=_to_float(record.get("price")),
        advise_sale_price_high=_optional_float(record.get("adviseSalePriceHigh")),
        advise_sale_price_low=_optional_float(record.get("adviseSalePriceLow")),
        sku_number=_to_int(record.get("skuNumber")),
        target_product_status=status,
        cat_name=_clean_text(record.get("catName")),
        supplier_name=_clean_text(record.get("supplierName")),
    )


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _is_active_status(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return int(value) == 1
    return _clean_text(value) == "1"


def _to_float(value: Any) -> float:
    if value is None or value == "":
        return 0.0
    try:
        return float(str(value).replace(",", "").replace("¥", "").replace("￥", "").strip())
    except ValueError:
        return 0.0


def _optional_float(value: Any) -> float | None:
    parsed = _to_float(value)
    return parsed if parsed > 0 else None


def _to_int(value: Any) -> int:
    if value is None or value == "":
        return 0
    try:
        return int(float(str(value).replace(",", "").strip()))
    except ValueError:
        return 0
