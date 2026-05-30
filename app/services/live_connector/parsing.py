from __future__ import annotations

import re
import time
from typing import Any
from urllib.parse import unquote

from app.services.live_connector.constants import (
    _DATA_REGION_REQUIRED,
    _ENCODED_VALUE_TYPE_MAP,
    _TOTAL_STATS_REQUIRED,
)
from app.services.live_connector.types import ProductEvent

def _unwrap_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    encoded_metrics = _extract_taobao_encoded_metrics(payload)
    data = payload.get("data")
    if isinstance(data, dict):
        nested = data.get("data")
        if isinstance(nested, dict):
            return {**nested, **encoded_metrics}
        result = data.get("result")
        if isinstance(result, dict):
            return {**result, **encoded_metrics}
        return {**data, **encoded_metrics}
    return {**payload, **encoded_metrics}


def _host_id_from_payload(payload: Any, explicit_host_id: str | None = None) -> str:
    if explicit_host_id and str(explicit_host_id).strip():
        return _session_key(_workspace_id_from_payload(payload), _clean_host_id(explicit_host_id))
    if not isinstance(payload, dict):
        return "default"
    value = (
        _pick(payload, "host_id", "hostId", "room_id", "roomId", "liveId", "live_id")
        or _find_value(payload, "host_id")
        or _find_value(payload, "hostId")
        or _find_value(payload, "room_id")
        or _find_value(payload, "roomId")
        or _find_value(payload, "liveId")
        or _find_value(payload, "live_id")
    )
    host_id = _clean_host_id(value)
    workspace_id = _workspace_id_from_payload(payload)
    return _session_key(workspace_id, host_id)


def _workspace_id_from_payload(payload: Any) -> str:
    if not isinstance(payload, dict):
        return "default"
    value = (
        _pick(payload, "workspace_id", "workspaceId", "workspace", "binding_code", "bindingCode")
        or _find_value(payload, "workspace_id")
        or _find_value(payload, "workspaceId")
        or _find_value(payload, "binding_code")
        or _find_value(payload, "bindingCode")
    )
    return _clean_workspace_id(value)


def _workspace_id_from_session_key(host_id: str, payload: Any = None) -> str:
    workspace_id = _workspace_id_from_payload(payload)
    if workspace_id != "default":
        return workspace_id
    text = str(host_id or "")
    if ":" in text:
        workspace, _room = text.split(":", 1)
        return _clean_workspace_id(workspace)
    return "default"


def _session_key(workspace_id: str, host_id: str) -> str:
    clean_workspace = _clean_workspace_id(workspace_id)
    clean_host = _clean_host_id(host_id)
    if clean_workspace == "default":
        return clean_host
    if clean_host.startswith(f"{clean_workspace}:"):
        return clean_host
    return f"{clean_workspace}:{clean_host}"


def _clean_workspace_id(value: Any) -> str:
    text = str(value or "").strip()
    if not text or text in {"None", "null", "undefined"}:
        return "default"
    text = re.sub(r"[^A-Za-z0-9_.:-]+", "-", text)
    return text[:48] or "default"


def _clean_host_id(value: Any) -> str:
    text = str(value or "").strip()
    if not text or text in {"None", "null", "undefined"}:
        return "default"
    text = re.sub(r"[^A-Za-z0-9_.:-]+", "-", text)
    return text[:80] or "default"

def _normalize_ingested_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    metrics = payload.get("metrics")
    if not isinstance(metrics, dict):
        return payload

    normalized: dict[str, Any] = {**metrics}
    if isinstance(metrics.get("dataRegion"), dict):
        normalized["dataRegion"] = metrics["dataRegion"]
    events = payload.get("events")
    if isinstance(events, list):
        normalized["interactSecKill"] = events
    for key in (
        "source",
        "liveId",
        "live_id",
        "host_id",
        "hostId",
        "workspace_id",
        "workspaceId",
        "workspace",
        "binding_code",
        "bindingCode",
        "room_id",
        "roomId",
        "timestamp",
        "captured_at",
        "captured_api",
        "payload_sections",
        "payloadSections",
        "extension_version",
        "extensionVersion",
    ):
        if payload.get(key) is not None:
            normalized[key] = payload[key]
    normalized["source"] = payload.get("source") or "chrome_extension"
    return normalized


def _overlay_live_context(base_payload: dict[str, Any], context_payload: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(context_payload, dict):
        return base_payload
    normalized_context = _unwrap_payload(_normalize_ingested_payload(context_payload))
    overlay: dict[str, Any] = {}
    for key in (
        "viewer_comments",
        "comments",
        "comment_text",
        "authenticity_comments",
        "authenticity_questions",
        "sizing_comments",
        "sizing_questions",
        "current_product",
        "item_name",
        "itemName",
    ):
        value = normalized_context.get(key)
        if value not in (None, ""):
            overlay[key] = value
    if not overlay:
        return base_payload
    return {**base_payload, **overlay}


_TOTAL_STATS_REQUIRED = (
    "heat_score",
    "ipv_uv_rate",
    "pay_byr_rate",
    "online_uv",
    "uv",
    "pv",
    "comment_uv",
    "pay_amt",
)

_DATA_REGION_REQUIRED = (
    "look_uv_td_d_live",
    "look_uv_5min_d_live",
    "pay_amt_td_d_live",
    "pay_amt_5min_d_live",
    "look_time_td_avg_d_live",
)


def _missing_required_metrics(data: dict[str, Any]) -> list[str]:
    if _has_dom_fallback_metrics(data):
        return []
    total_stats = _find_dict(data, "totalStats")
    if total_stats is None and any(_pick(data, field, _camelize(field)) is not None for field in _TOTAL_STATS_REQUIRED):
        total_stats = data
    data_region = _find_dict(data, "dataRegion")
    if data_region is None and any(_pick(data, field) is not None for field in _DATA_REGION_REQUIRED):
        data_region = data
    missing: list[str] = []
    if total_stats is None:
        missing.extend(f"totalStats.{field}" for field in _TOTAL_STATS_REQUIRED)
    else:
        missing.extend(f"totalStats.{field}" for field in _TOTAL_STATS_REQUIRED if _pick(total_stats, field, _camelize(field)) is None)
    # dataRegion enriches trend context, but totalStats alone is enough for live decisions.
    if total_stats is None and data_region is None:
        missing.extend(f"dataRegion.{field}" for field in _DATA_REGION_REQUIRED)
    elif total_stats is None and data_region is not None:
        missing.extend(f"dataRegion.{field}" for field in _DATA_REGION_REQUIRED if _pick(data_region, field) is None)
    return missing


def _has_dom_fallback_metrics(data: dict[str, Any]) -> bool:
    sections = _pick(data, "payload_sections", "payloadSections")
    has_dom_section = isinstance(sections, dict) and bool(_pick(sections, "domFallback", "dom_fallback"))
    if _pick(data, "captured_api", "capturedApi") != "dom_live_dashboard" and not has_dom_section:
        return False
    return any(
        _to_number(_pick(data, field)) > 0
        for field in ("pay_amt", "online_uv", "uv", "pv")
    )


def _camelize(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part[:1].upper() + part[1:] for part in tail)


_ENCODED_VALUE_TYPE_MAP: dict[str, str] = {
    "uv": "uv",
    "pv": "pv",
    "online_uv": "online_uv",
    "heat_score": "heat_score",
    "pay_amt": "pay_amt",
    "pay_byr_rate": "pay_byr_rate",
    "ipv_uv_rate": "ipv_uv_rate",
    "stay_time_pu": "stay_time_pu",
    "comment_uv": "comment_uv",
    "pay_item_qty": "pay_item_qty",
    "pay_buyer_cnt": "pay_buyer_cnt",
    "look_uv_td_d_live": "look_uv_td_d_live",
    "look_uv_5min_d_live": "look_uv_5min_d_live",
    "look_time_td_avg_d_live": "look_time_td_avg_d_live",
    "look_time_5min_avg_d_live": "look_time_5min_avg_d_live",
    "pay_amt_td_d_live": "pay_amt_td_d_live",
    "pay_amt_5min_d_live": "pay_amt_5min_d_live",
    "pay_amt_td_d_shop": "pay_amt_td_d_shop",
    "pay_amt_5min_d_shop": "pay_amt_5min_d_shop",
}


def _extract_taobao_encoded_metrics(payload: Any) -> dict[str, float]:
    metrics: dict[str, float] = {}
    for data_list in _collect_data_lists(payload):
        for section in data_list:
            if not isinstance(section, dict):
                continue
            rows = section.get("data")
            if not isinstance(rows, list):
                continue
            for row in rows:
                parsed = _parse_encoded_metric_row(row)
                if not parsed:
                    continue
                field = _metric_field_from_value_type(parsed["value_type"])
                if field and field not in metrics:
                    metrics[field] = parsed["numeric_value"]
    return metrics


def _collect_data_lists(node: Any) -> list[list[Any]]:
    lists: list[list[Any]] = []
    if isinstance(node, dict):
        data_list = node.get("dataList")
        if isinstance(data_list, list):
            lists.append(data_list)
        for value in node.values():
            lists.extend(_collect_data_lists(value))
    elif isinstance(node, list):
        for item in node:
            lists.extend(_collect_data_lists(item))
    return lists


def _parse_encoded_metric_row(row: Any) -> dict[str, Any] | None:
    if not isinstance(row, dict):
        return None
    raw_value = row.get("value") or row.get("dataValue") or row.get("metricValue")
    if not raw_value:
        return None
    decoded = unquote(str(raw_value))
    parts = decoded.split(",")
    label_parts = [
        str(row.get(key) or "")
        for key in ("key", "name", "title", "code", "fieldName")
        if row.get(key)
    ]
    if parts:
        label_parts.append(parts[0])
    numeric_value = _to_number(parts[3] if len(parts) > 3 else parts[2] if len(parts) > 2 else "")
    return {
        "label": " ".join(label_parts),
        "value_type": str(row.get("valueType") or row.get("value_type") or (parts[1] if len(parts) > 1 else "")),
        "display_value": parts[2] if len(parts) > 2 else "",
        "numeric_value": numeric_value,
    }


def _metric_field_from_value_type(value_type: str) -> str:
    return _ENCODED_VALUE_TYPE_MAP.get(str(value_type or "").strip())


def _pick(data: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in data:
            return data[key]
    lowered = {str(key).lower(): value for key, value in data.items()}
    for key in keys:
        value = lowered.get(key.lower())
        if value is not None:
            return value
    return None


def _find_value(node: Any, target_key: str) -> Any:
    if isinstance(node, dict):
        for key, value in node.items():
            if str(key) == target_key:
                return value
        for value in node.values():
            found = _find_value(value, target_key)
            if found is not None:
                return found
    elif isinstance(node, list):
        for item in node:
            found = _find_value(item, target_key)
            if found is not None:
                return found
    return None


def _find_dict(node: Any, target_key: str) -> dict[str, Any] | None:
    value = _find_value(node, target_key)
    return value if isinstance(value, dict) else None


def _parse_product_events(raw: Any) -> list[ProductEvent]:
    if isinstance(raw, dict):
        candidates = raw.get("list") or raw.get("items") or raw.get("data") or raw.get("result") or raw.get("products")
        if candidates is None and any(key in raw for key in ("title", "payBuyerCnt", "imageUrl")):
            candidates = [raw]
    else:
        candidates = raw
    if not isinstance(candidates, list):
        return []

    events: list[ProductEvent] = []
    for item in candidates:
        if not isinstance(item, dict):
            continue
        events.append(
            ProductEvent(
                imageUrl=str(_pick(item, "imageUrl", "image_url", "picUrl") or ""),
                price=_to_number(_pick(item, "price")),
                payBuyerCnt=int(_to_number(_pick(item, "payBuyerCnt", "pay_buyer_cnt"))),
                startTime=str(_pick(item, "startTime", "start_time") or ""),
                startTimeFormat=str(_pick(item, "startTimeFormat", "start_time_format") or ""),
                title=str(_pick(item, "title", "itemTitle", "productTitle") or ""),
                status=str(_pick(item, "status") or ""),
            )
        )
    return events


def _to_number(value: Any) -> float:
    if value is None or value == "":
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace(",", "").replace("%", "").replace("¥", "").replace("￥", "").replace("元", "").strip()
    try:
        number = float(text)
    except ValueError:
        return 0.0
    return number / 100 if "%" in str(value) else number


def _normalize_rate(value: Any) -> float:
    number = _to_number(value)
    if number > 1:
        number = number / 100
    return max(0.0, min(1.0, number))


def _trend_direction(current: float, previous: float) -> str:
    diff = current - previous
    threshold = max(0.003, abs(previous) * 0.05)
    if diff > threshold:
        return "up"
    if diff < -threshold:
        return "down"
    return "stable"


def _comment_text(data: dict[str, Any]) -> str:
    raw = data.get("comments") or data.get("viewer_comments") or data.get("comment_text") or ""
    if isinstance(raw, list):
        return "\n".join(str(item) for item in raw)
    return str(raw)


def _mock_payload() -> dict[str, Any]:
    now = int(time.time())
    return {
        "online_uv": 420 + (now % 30),
        "stay_time_pu": 48 + (now % 20),
        "pay_amt": 9000 + (now % 10) * 600,
        "heat_score": 520 + (now % 8) * 18,
        "ipv_uv_rate": 0.08 + (now % 4) * 0.03,
        "pay_byr_rate": 0.012 + (now % 3) * 0.006,
        "pay_buyer_cnt": 10 + (now % 5),
        "pay_item_qty": 12 + (now % 6),
        "comment_uv": 20 + (now % 9),
        "atn_uv": 18 + (now % 10),
        "dataRegion": {
            "look_uv_td_d_live": 2600,
            "look_time_td_avg_d_live": 58,
            "pay_amt_td_d_live": 36000,
            "look_uv_5min_d_live": 420,
            "look_time_5min_avg_d_live": 52 + (now % 10),
            "pay_amt_5min_d_live": 1800 + (now % 6) * 500,
            "pay_amt_5min_d_shop": 2200 + (now % 5) * 400,
        },
        "item_click_rate": 0.06 + (now % 4) * 0.01,
        "item_conversion_rate": 0.018 + (now % 3) * 0.004,
        "item_add_cart_rate": 0.04 + (now % 5) * 0.006,
        "item_gmv": 4200 + (now % 8) * 500,
        "current_product": "当前商品",
        "interactSecKill": [
            {
                "imageUrl": "",
                "price": 899,
                "payBuyerCnt": 6 + (now % 4),
                "startTime": str(now),
                "startTimeFormat": "刚刚",
                "title": "成交款商品",
                "status": "active",
            }
        ],
    }
