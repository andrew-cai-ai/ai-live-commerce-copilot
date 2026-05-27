from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from typing import Any

import requests
from dotenv import load_dotenv

load_dotenv()


@dataclass
class ProductOption:
    name: str
    score: float = 0.0
    inventory: int = 0
    profit_margin: float = 0.0


@dataclass
class LiveMetricSnapshot:
    timestamp: float
    online_uv: float = 0.0
    stay_time_pu: float = 0.0
    pay_amt: float = 0.0
    pay_buyer_cnt: float = 0.0
    item_click_rate: float = 0.0
    item_conversion_rate: float = 0.0
    item_add_cart_rate: float = 0.0
    item_gmv: float = 0.0
    current_product: str = ""
    authenticity_comments: int = 0
    sizing_comments: int = 0
    source: str = "mock"


@dataclass
class LiveDecision:
    current_action: str
    next_action: str
    recommended_next_product: str
    reason: list[str]
    confidence: float
    trend_30s: dict[str, str]
    trend_60s: dict[str, str]
    snapshot: LiveMetricSnapshot
    source: str
    warnings: list[str] = field(default_factory=list)


class LiveDataConnector:
    def __init__(self) -> None:
        self.api_url = os.getenv("LIVE_METRICS_API_URL", "").strip()
        self.api_token = os.getenv("LIVE_METRICS_API_TOKEN", "").strip()
        self.timeout = float(os.getenv("LIVE_METRICS_API_TIMEOUT", "5"))
        self.snapshots: list[LiveMetricSnapshot] = []

    def get_decision(
        self,
        payload: dict[str, Any] | None = None,
        products: list[dict[str, Any]] | None = None,
    ) -> LiveDecision:
        warnings: list[str] = []
        data, source = self._fetch_live_payload(payload, warnings)
        snapshot = self._build_snapshot(data, source)
        self._store_snapshot(snapshot)
        trend_30s = self._trend_for(snapshot, 30)
        trend_60s = self._trend_for(snapshot, 60)
        decision = self._decide(snapshot, trend_30s, trend_60s, products or [])
        decision.warnings.extend(warnings)
        return decision

    def _fetch_live_payload(
        self,
        payload: dict[str, Any] | None,
        warnings: list[str],
    ) -> tuple[dict[str, Any], str]:
        if self.api_url:
            try:
                headers = {}
                if self.api_token:
                    headers["Authorization"] = f"Bearer {self.api_token}"
                response = requests.get(self.api_url, headers=headers, timeout=self.timeout)
                response.raise_for_status()
                return _unwrap_payload(response.json()), "real_api"
            except Exception as exc:
                warnings.append(f"Live metrics API failed, using fallback data: {exc}")

        if payload:
            return _unwrap_payload(payload), "page_payload"

        warnings.append("LIVE_METRICS_API_URL is not configured; using mock live data.")
        return _mock_payload(), "mock"

    def _build_snapshot(self, data: dict[str, Any], source: str) -> LiveMetricSnapshot:
        comments = _comment_text(data)
        return LiveMetricSnapshot(
            timestamp=time.time(),
            online_uv=_to_number(data.get("online_uv") or data.get("uv")),
            stay_time_pu=_to_number(data.get("stay_time_pu") or data.get("watch_duration")),
            pay_amt=_to_number(data.get("pay_amt")),
            pay_buyer_cnt=_to_number(data.get("pay_buyer_cnt")),
            item_click_rate=_normalize_rate(data.get("item_click_rate") or data.get("ipv_uv_rate")),
            item_conversion_rate=_normalize_rate(data.get("item_conversion_rate") or data.get("pay_byr_rate")),
            item_add_cart_rate=_normalize_rate(data.get("item_add_cart_rate") or data.get("cart_rate")),
            item_gmv=_to_number(data.get("item_gmv") or data.get("pay_amt")),
            current_product=str(data.get("current_product") or data.get("item_name") or data.get("itemName") or "").strip(),
            authenticity_comments=int(_to_number(data.get("authenticity_comments") or data.get("authenticity_questions"))) or _count_authenticity_comments(comments),
            sizing_comments=int(_to_number(data.get("sizing_comments") or data.get("sizing_questions"))) or _count_sizing_comments(comments),
            source=source,
        )

    def _store_snapshot(self, snapshot: LiveMetricSnapshot) -> None:
        self.snapshots.append(snapshot)
        cutoff = time.time() - 5 * 60
        self.snapshots = [item for item in self.snapshots if item.timestamp >= cutoff]

    def _trend_for(self, current: LiveMetricSnapshot, seconds: int) -> dict[str, str]:
        previous = self._snapshot_ago(seconds)
        if previous is None:
            return {key: "stable" for key in _TREND_FIELDS}
        return {
            field: _trend_direction(getattr(current, field), getattr(previous, field))
            for field in _TREND_FIELDS
        }

    def _snapshot_ago(self, seconds: int) -> LiveMetricSnapshot | None:
        target = time.time() - seconds
        candidate = None
        for snapshot in self.snapshots:
            if snapshot.timestamp <= target:
                candidate = snapshot
        return candidate

    def _decide(
        self,
        snapshot: LiveMetricSnapshot,
        trend_30s: dict[str, str],
        trend_60s: dict[str, str],
        products: list[dict[str, Any]],
    ) -> LiveDecision:
        reason: list[str] = []
        confidence = 0.64
        current_action = "continue product"
        next_action = "继续讲当前商品，观察 30 秒趋势"

        if trend_30s["online_uv"] == "down" and trend_30s["item_add_cart_rate"] == "down":
            current_action = "switch product"
            next_action = "切到下一件更容易成交的商品"
            reason = ["online_uv 30s down", "item_add_cart_rate 30s down", "traffic and cart intent are weakening"]
            confidence = 0.82
        elif trend_30s["item_click_rate"] == "up" and trend_30s["item_conversion_rate"] == "down":
            current_action = "explain value"
            next_action = "解释价格、价值和使用场景"
            reason = ["item_click_rate 30s up", "item_conversion_rate 30s down", "users are interested but not paying"]
            confidence = 0.80
        elif snapshot.authenticity_comments > 3:
            current_action = "show authenticity proof"
            next_action = "展示吊牌、洗标、拉链和细节"
            reason = ["authenticity comments above threshold", "trust is blocking conversion", "show real evidence now"]
            confidence = 0.86
        elif snapshot.sizing_comments > 3:
            current_action = "switch to sizing explanation"
            next_action = "集中讲尺码、身高体重和内搭建议"
            reason = ["sizing comments above threshold", "fit questions are increasing", "answer before pushing order"]
            confidence = 0.84
        else:
            reason = _top_metric_reasons(snapshot, trend_30s, trend_60s)

        return LiveDecision(
            current_action=current_action,
            next_action=next_action,
            recommended_next_product=_recommend_next_product(snapshot.current_product, products, current_action),
            reason=reason[:3],
            confidence=confidence,
            trend_30s=trend_30s,
            trend_60s=trend_60s,
            snapshot=snapshot,
            source=snapshot.source,
        )


_TREND_FIELDS = [
    "online_uv",
    "stay_time_pu",
    "pay_amt",
    "pay_buyer_cnt",
    "item_click_rate",
    "item_conversion_rate",
    "item_add_cart_rate",
    "item_gmv",
]


def _unwrap_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    data = payload.get("data")
    if isinstance(data, dict):
        nested = data.get("data")
        if isinstance(nested, dict):
            return nested
        result = data.get("result")
        if isinstance(result, dict):
            return result
        return data
    return payload


def _to_number(value: Any) -> float:
    if value is None or value == "":
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace(",", "").replace("%", "").strip()
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


def _count_authenticity_comments(text: str) -> int:
    return len(re.findall(r"真假|真的假的|正品|吊牌|洗标|保真|鉴定", text))


def _count_sizing_comments(text: str) -> int:
    return len(re.findall(r"尺码|穿啥|多大|身高|体重|[1-2]\d{2}\s*[/ ]?\s*\d{2,3}", text))


def _recommend_next_product(current_product: str, products: list[dict[str, Any]], action: str) -> str:
    if not products:
        return "暂无推荐商品"
    sorted_products = sorted(
        products,
        key=lambda item: (
            float(item.get("score") or 0),
            float(item.get("profit_margin") or 0),
            float(item.get("inventory") or 0),
        ),
        reverse=True,
    )
    if action in {"switch product", "skip product"}:
        for product in sorted_products:
            if product.get("name") != current_product:
                return str(product.get("name") or "下一件商品")
    return str(sorted_products[0].get("name") or "下一件商品")


def _top_metric_reasons(
    snapshot: LiveMetricSnapshot,
    trend_30s: dict[str, str],
    trend_60s: dict[str, str],
) -> list[str]:
    return [
        f"online_uv {int(snapshot.online_uv)} / 30s {trend_30s['online_uv']}",
        f"cart_rate {snapshot.item_add_cart_rate:.1%} / 30s {trend_30s['item_add_cart_rate']}",
        f"item_gmv ¥{int(snapshot.item_gmv)} / 60s {trend_60s['item_gmv']}",
    ]


def _mock_payload() -> dict[str, Any]:
    now = int(time.time())
    return {
        "online_uv": 420 + (now % 30),
        "stay_time_pu": 48 + (now % 20),
        "pay_amt": 9000 + (now % 10) * 600,
        "pay_buyer_cnt": 10 + (now % 5),
        "item_click_rate": 0.06 + (now % 4) * 0.01,
        "item_conversion_rate": 0.018 + (now % 3) * 0.004,
        "item_add_cart_rate": 0.04 + (now % 5) * 0.006,
        "item_gmv": 4200 + (now % 8) * 500,
        "current_product": "当前商品",
    }
