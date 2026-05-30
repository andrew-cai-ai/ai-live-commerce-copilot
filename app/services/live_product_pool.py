from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_PRODUCT_POOL_PATH = ROOT_DIR / "data" / "live_product_pool.json"


class LiveProductPoolStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or DEFAULT_PRODUCT_POOL_PATH

    def save(self, products: list[Any], workspace_id: str = "") -> dict[str, Any]:
        rows = [_product_row(product, index) for index, product in enumerate(products)]
        rows = [row for row in rows if row.get("name")]
        record = {
            "workspace_id": _clean_workspace_id(workspace_id),
            "updated_at": time.time(),
            "count": len(rows),
            "products": rows[:200],
        }
        payload = self._read()
        payload[record["workspace_id"]] = record
        payload["default"] = record
        self._write(payload)
        return record

    def get(self, workspace_id: str = "") -> dict[str, Any]:
        payload = self._read()
        workspace = _clean_workspace_id(workspace_id)
        record = payload.get(workspace) or payload.get("default")
        if not isinstance(record, dict):
            records = [value for value in payload.values() if isinstance(value, dict)]
            record = max(records, key=lambda item: float(item.get("updated_at") or 0), default={})
        products = record.get("products") if isinstance(record, dict) else []
        return {
            "workspace_id": record.get("workspace_id") or workspace,
            "updated_at": float(record.get("updated_at") or 0),
            "count": len(products) if isinstance(products, list) else 0,
            "products": products if isinstance(products, list) else [],
        }

    def _read(self) -> dict[str, Any]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}

    def _write(self, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _product_row(product: Any, index: int) -> dict[str, Any]:
    name = re.sub(r"\s+", " ", str(_pick(product, "name", "product_name") or "")).strip()
    score = _pick(product, "score")
    inventory = _pick(product, "inventory", "stock")
    target_price = _pick(product, "target_selling_price", "target_price", "price")
    profit_margin = _pick(product, "profit_margin")
    gmv_level = _pick(product, "gmv_level")
    category = _pick(product, "category")
    knowledge = getattr(product, "knowledge", None)
    if not category and knowledge is not None:
        category = getattr(knowledge, "category", "")
    return {
        "name": name,
        "score": _float_or_default(score, max(0.0, 1 - index * 0.01)),
        "inventory": _float_or_default(inventory, 0),
        "profit_margin": _float_or_default(profit_margin, 0),
        "target_selling_price": _float_or_default(target_price, 0),
        "gmv_level": str(gmv_level or ""),
        "category": str(category or ""),
        "source": "excel_product_pool",
    }


def _pick(product: Any, *keys: str) -> Any:
    for key in keys:
        if isinstance(product, dict) and key in product:
            return product.get(key)
        if hasattr(product, key):
            return getattr(product, key)
    return None


def _float_or_default(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _clean_workspace_id(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_.:-]+", "-", str(value or "").strip())[:48]
    return cleaned or "default"


live_product_pool = LiveProductPoolStore()
