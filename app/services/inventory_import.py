from __future__ import annotations

import io
import json
from typing import Any

from app.providers.taobao_provider import parse_taobao_products
from scoring import InventoryItem, parse_inventory


def build_inventory_items(
    inventory_text: str,
    taobao_json_text: str = "",
    excel_bytes: bytes | None = None,
    excel_filename: str = "",
) -> list[InventoryItem]:
    if taobao_json_text.strip():
        taobao_items = parse_taobao_inventory_json(taobao_json_text)
        if taobao_items:
            return _dedupe_inventory_items(taobao_items)
        if not excel_bytes and not inventory_text.strip():
            raise ValueError("淘宝 JSON 中没有 targetProductStatus = 1 的有效商品。")

    items: list[InventoryItem] = []
    if excel_bytes:
        items.extend(parse_excel_inventory(excel_bytes, excel_filename))
    if inventory_text.strip():
        items.extend(parse_inventory(inventory_text))
    if not items:
        raise ValueError("请至少上传 Excel、粘贴淘宝 JSON，或填写一个库存商品。")
    return _dedupe_inventory_items(items)


def parse_excel_inventory(file_bytes: bytes, filename: str = "") -> list[InventoryItem]:
    if filename.lower().endswith(".csv"):
        text = file_bytes.decode("utf-8-sig")
        return parse_inventory(text)

    try:
        from openpyxl import load_workbook
    except ImportError as exc:
        raise ValueError("Excel 上传需要安装 openpyxl：请运行 pip install openpyxl。") from exc

    workbook = load_workbook(io.BytesIO(file_bytes), read_only=True, data_only=True)
    sheet = workbook.active
    rows = list(sheet.iter_rows(values_only=True))
    if not rows:
        return []

    headers = [_normalize_header(value) for value in rows[0]]
    required = {"product_name", "cost_price", "inventory"}
    if not required.issubset(set(headers)):
        raise ValueError("Excel 需要包含字段：SKU、product_name、cost_price、inventory。")

    index = {header: position for position, header in enumerate(headers)}
    items: list[InventoryItem] = []
    for row_number, row in enumerate(rows[1:], start=2):
        product_name = _cell(row, index.get("product_name"))
        if not product_name:
            continue
        cost = _float_cell(row, index.get("cost_price"), row_number, "cost_price")
        inventory = _int_cell(row, index.get("inventory"), row_number, "inventory")
        target_price = _optional_float_cell(row, index.get("target_selling_price")) or 0.0
        items.append(
            InventoryItem(
                product_name=product_name,
                cost=cost,
                stock=inventory,
                target_selling_price=target_price,
                sku=_cell(row, index.get("sku")),
                sku_count=inventory,
                category=_cell(row, index.get("category")),
                status=_cell(row, index.get("status")),
                source="excel",
            )
        )
    return items


def parse_taobao_inventory_json(raw_json: str) -> list[InventoryItem]:
    try:
        payload = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"淘宝 JSON 解析失败：第 {exc.lineno} 行第 {exc.colno} 列不是有效 JSON。") from exc

    products = parse_taobao_products(payload)
    return [
        InventoryItem(
            product_name=product.source_product_title,
            cost=product.price,
            stock=product.sku_number,
            target_selling_price=product.advise_sale_price_high or product.advise_sale_price_low or 0.0,
            sku=product.source_product_id,
            sku_count=product.sku_number,
            category=product.cat_name,
            status=product.target_product_status,
            source="taobao_json",
        )
        for product in products
    ]


def _dedupe_inventory_items(items: list[InventoryItem]) -> list[InventoryItem]:
    deduped: dict[str, InventoryItem] = {}
    for item in items:
        key = item.sku.strip() or item.product_name.strip().lower()
        deduped[key] = item
    return list(deduped.values())


def _normalize_header(value: Any) -> str:
    text = str(value or "").strip().lower()
    aliases = {
        "sku": "sku",
        "product": "product_name",
        "name": "product_name",
        "商品": "product_name",
        "商品名": "product_name",
        "product_name": "product_name",
        "cost": "cost_price",
        "cost_price": "cost_price",
        "成本": "cost_price",
        "inventory": "inventory",
        "stock": "inventory",
        "库存": "inventory",
        "target_price": "target_selling_price",
        "target_selling_price": "target_selling_price",
        "目标售价": "target_selling_price",
        "category": "category",
        "类目": "category",
        "status": "status",
        "状态": "status",
    }
    return aliases.get(text, text)


def _cell(row: tuple[Any, ...], index: int | None) -> str:
    if index is None or index >= len(row) or row[index] is None:
        return ""
    return str(row[index]).strip()


def _float_cell(row: tuple[Any, ...], index: int | None, row_number: int, field_name: str) -> float:
    value = _optional_float_cell(row, index)
    if value is None:
        raise ValueError(f"Excel 第 {row_number} 行：{field_name} 必须是数字。")
    return value


def _optional_float_cell(row: tuple[Any, ...], index: int | None) -> float | None:
    text = _cell(row, index).replace(",", "").replace("$", "").replace("¥", "").replace("￥", "")
    if not text:
        return None
    try:
        return round(float(text), 2)
    except ValueError:
        return None


def _int_cell(row: tuple[Any, ...], index: int | None, row_number: int, field_name: str) -> int:
    value = _optional_float_cell(row, index)
    if value is None:
        raise ValueError(f"Excel 第 {row_number} 行：{field_name} 必须是整数。")
    return int(value)
