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

    def save(
        self,
        products: list[Any],
        workspace_id: str = "",
        inventory_items: list[Any] | None = None,
    ) -> dict[str, Any]:
        inventory_lookup = _inventory_lookup(inventory_items or [])
        rows = [
            _product_row(product, index, inventory_lookup.get(_lookup_key(product)))
            for index, product in enumerate(products)
        ]
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


def _product_row(product: Any, index: int, source_item: Any | None = None) -> dict[str, Any]:
    name = _clean_text(_pick(product, "name", "product_name") or _pick(source_item, "name", "product_name"))
    score = _pick(product, "score")
    inventory = _pick(product, "inventory", "stock")
    if inventory is None:
        inventory = _pick(source_item, "inventory", "stock")
    target_price = _pick(product, "target_selling_price", "target_price", "price")
    if target_price is None:
        target_price = _pick(source_item, "target_selling_price", "target_price", "price")
    profit_margin = _pick(product, "profit_margin")
    gmv_level = _pick(product, "gmv_level")
    category = _pick(product, "category") or _pick(source_item, "category")
    sku = _clean_text(_pick(product, "sku", "source_product_id") or _pick(source_item, "sku", "source_product_id"))
    color = _clean_text(_pick(product, "color") or _pick(source_item, "color"))
    notes = _clean_text(_pick(product, "notes") or _pick(source_item, "notes"))
    knowledge = getattr(product, "knowledge", None)
    if not category and knowledge is not None:
        category = getattr(knowledge, "category", "")
    aliases = _product_aliases(name=name, sku=sku, color=color, notes=notes, category=str(category or ""))
    return {
        "name": name,
        "sku": sku,
        "color": color,
        "notes": notes,
        "aliases": aliases,
        "score": _float_or_default(score, max(0.0, 1 - index * 0.01)),
        "inventory": _float_or_default(inventory, 0),
        "profit_margin": _float_or_default(profit_margin, 0),
        "target_selling_price": _float_or_default(target_price, 0),
        "gmv_level": str(gmv_level or ""),
        "category": str(category or ""),
        "source": "excel_product_pool",
    }


def _pick(product: Any, *keys: str) -> Any:
    if product is None:
        return None
    for key in keys:
        if isinstance(product, dict) and key in product:
            return product.get(key)
        if hasattr(product, key):
            return getattr(product, key)
    return None


def _inventory_lookup(items: list[Any]) -> dict[str, Any]:
    lookup: dict[str, Any] = {}
    for item in items:
        key = _lookup_key(item)
        if key and key not in lookup:
            lookup[key] = item
    return lookup


def _lookup_key(product: Any) -> str:
    return _compact_key(_pick(product, "name", "product_name") or "")


def _compact_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fa5]+", "", str(value or "").lower())


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _product_aliases(name: str, sku: str, color: str, notes: str, category: str) -> list[str]:
    aliases: list[str] = []
    for value in [name, sku, _sku_tail(sku), color, notes, category]:
        cleaned = _clean_text(value)
        if cleaned and cleaned not in aliases:
            aliases.append(cleaned)
    return aliases[:12]


def _sku_tail(value: str) -> str:
    match = re.search(r"x0*(\d{4,6})", str(value or "").lower())
    if not match:
        return ""
    tail = match.group(1).lstrip("0")
    return tail or match.group(1)


def _float_or_default(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _clean_workspace_id(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_.:-]+", "-", str(value or "").strip())[:48]
    return cleaned or "default"


live_product_pool = LiveProductPoolStore()
