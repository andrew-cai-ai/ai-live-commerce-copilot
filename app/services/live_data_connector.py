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
class ProductEvent:
    imageUrl: str = ""
    price: float = 0.0
    payBuyerCnt: int = 0
    startTime: str = ""
    startTimeFormat: str = ""
    title: str = ""
    status: str = ""


@dataclass
class LiveMetricSnapshot:
    timestamp: float
    online_uv: float = 0.0
    uv: float = 0.0
    pv: float = 0.0
    stay_time_pu: float = 0.0
    heat_score: float = 0.0
    ipv_uv_rate: float = 0.0
    pay_byr_rate: float = 0.0
    pay_amt: float = 0.0
    pay_buyer_cnt: float = 0.0
    pay_item_qty: float = 0.0
    refund_amt: float = 0.0
    comment_uv: float = 0.0
    atn_uv: float = 0.0
    look_uv_td_d_live: float = 0.0
    look_time_td_avg_d_live: float = 0.0
    pay_amt_td_d_live: float = 0.0
    look_uv_5min_d_live: float = 0.0
    look_time_5min_avg_d_live: float = 0.0
    pay_amt_5min_d_live: float = 0.0
    pay_amt_5min_d_shop: float = 0.0
    item_click_rate: float = 0.0
    item_conversion_rate: float = 0.0
    item_add_cart_rate: float = 0.0
    item_gmv: float = 0.0
    current_product: str = ""
    product_level_connected: bool = False
    product_events: list[ProductEvent] = field(default_factory=list)
    authenticity_comments: int = 0
    sizing_comments: int = 0
    comment_text: str = ""
    source: str = "mock"


@dataclass
class LiveDecision:
    valid_live_metrics: bool
    current_live_score: float
    current_action: str
    next_action: str
    recommended_next_product: str
    recent_product_winners: list[ProductEvent]
    product_level_connected: bool
    livestream_mode: str
    product_health: dict[str, Any]
    switch_recommendation: dict[str, Any]
    comment_clusters: dict[str, Any]
    learned_recommendations: list[str]
    reason: list[str]
    confidence: float
    trend_30s: dict[str, str]
    trend_60s: dict[str, str]
    snapshot: LiveMetricSnapshot
    source: str
    timeline: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class LiveDataConnector:
    def __init__(self) -> None:
        self.api_url = os.getenv("LIVE_METRICS_API_URL", "").strip()
        self.api_token = os.getenv("LIVE_METRICS_API_TOKEN", "").strip()
        self.timeout = float(os.getenv("LIVE_METRICS_API_TIMEOUT", "5"))
        self.snapshots: list[LiveMetricSnapshot] = []
        self.action_history: list[dict[str, Any]] = []
        self.latest_ingested_payload: dict[str, Any] | None = None
        self.latest_ingested_at: float = 0.0

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
        self._record_action(decision)
        decision.warnings.extend(warnings)
        return decision

    def ingest_live_metrics(self, payload: dict[str, Any]) -> LiveDecision:
        self.latest_ingested_payload = payload
        self.latest_ingested_at = time.time()
        return self.get_decision(payload=payload)

    def _record_action(self, decision: LiveDecision) -> None:
        entry = _timeline_entry(decision)
        last = self.action_history[-1] if self.action_history else None
        if last and not _should_append_timeline_entry(last, entry):
            last["last_seen"] = entry["timestamp"]
            last["repeat_count"] = int(last.get("repeat_count") or 1) + 1
            last["confidence"] = entry["confidence"]
            last["current_live_score"] = entry["current_live_score"]
            decision.timeline = list(reversed(self.action_history))
            return

        self.action_history.append(entry)
        self.action_history = self.action_history[-10:]
        decision.timeline = list(reversed(self.action_history))

    def _fetch_live_payload(
        self,
        payload: dict[str, Any] | None,
        warnings: list[str],
    ) -> tuple[dict[str, Any], str]:
        if payload:
            return _unwrap_payload(payload), "page_payload"

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

        if self.latest_ingested_payload and time.time() - self.latest_ingested_at <= 30:
            return _unwrap_payload(self.latest_ingested_payload), "chrome_extension"

        warnings.append("No live metrics connector data found.")
        return {}, "no_connector"

    def _build_snapshot(self, data: dict[str, Any], source: str) -> LiveMetricSnapshot:
        total_stats = _find_dict(data, "totalStats") or data
        data_region = _find_dict(data, "dataRegion") or {}
        events = _parse_product_events(_find_value(data, "interactSecKill"))
        comments = _comment_text(data)
        ipv_uv_rate = _normalize_rate(_pick(total_stats, "ipv_uv_rate", "ipvUvRate"))
        pay_byr_rate = _normalize_rate(_pick(total_stats, "pay_byr_rate", "payByrRate"))
        explicit_product_metrics = any(
            _pick(data, key) is not None
            for key in (
                "item_name",
                "itemName",
                "item_click_rate",
                "itemClickRate",
                "item_conversion_rate",
                "itemConversionRate",
                "item_add_cart_rate",
                "itemAddCartRate",
                "item_gmv",
                "itemGmv",
                "jiangJieEffect",
            )
        )
        return LiveMetricSnapshot(
            timestamp=time.time(),
            online_uv=_to_number(_pick(total_stats, "online_uv", "onlineUv") or _pick(total_stats, "uv")),
            uv=_to_number(_pick(total_stats, "uv")),
            pv=_to_number(_pick(total_stats, "pv")),
            stay_time_pu=_to_number(_pick(total_stats, "stay_time_pu", "stayTimePu", "watch_duration") or _pick(data_region, "look_time_5min_avg_d_live")),
            heat_score=_to_number(_pick(total_stats, "heat_score", "heatScore")),
            ipv_uv_rate=ipv_uv_rate,
            pay_byr_rate=pay_byr_rate,
            pay_amt=_to_number(_pick(total_stats, "pay_amt", "payAmt")),
            pay_buyer_cnt=_to_number(_pick(total_stats, "pay_buyer_cnt", "payBuyerCnt")),
            pay_item_qty=_to_number(_pick(total_stats, "pay_item_qty", "payItemQty")),
            refund_amt=_to_number(_pick(total_stats, "refund_amt", "refundAmt")),
            comment_uv=_to_number(_pick(total_stats, "comment_uv", "commentUv")),
            atn_uv=_to_number(_pick(total_stats, "atn_uv", "atnUv")),
            look_uv_td_d_live=_to_number(_pick(data_region, "look_uv_td_d_live")),
            look_time_td_avg_d_live=_to_number(_pick(data_region, "look_time_td_avg_d_live")),
            pay_amt_td_d_live=_to_number(_pick(data_region, "pay_amt_td_d_live")),
            look_uv_5min_d_live=_to_number(_pick(data_region, "look_uv_5min_d_live")),
            look_time_5min_avg_d_live=_to_number(_pick(data_region, "look_time_5min_avg_d_live")),
            pay_amt_5min_d_live=_to_number(_pick(data_region, "pay_amt_5min_d_live")),
            pay_amt_5min_d_shop=_to_number(_pick(data_region, "pay_amt_5min_d_shop")),
            item_click_rate=_normalize_rate(_pick(data, "item_click_rate", "itemClickRate") or ipv_uv_rate),
            item_conversion_rate=_normalize_rate(_pick(data, "item_conversion_rate", "itemConversionRate") or pay_byr_rate),
            item_add_cart_rate=_normalize_rate(_pick(data, "item_add_cart_rate", "itemAddCartRate", "cart_rate")),
            item_gmv=_to_number(_pick(data, "item_gmv", "itemGmv") or _pick(data_region, "pay_amt_5min_d_live") or _pick(total_stats, "pay_amt")),
            current_product=str(_pick(data, "current_product", "item_name", "itemName") or _best_event_title(events)).strip(),
            product_level_connected=explicit_product_metrics,
            product_events=events,
            authenticity_comments=int(_to_number(_pick(data, "authenticity_comments", "authenticity_questions"))) or _count_authenticity_comments(comments),
            sizing_comments=int(_to_number(_pick(data, "sizing_comments", "sizing_questions"))) or _count_sizing_comments(comments),
            comment_text=comments,
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
        current_live_score = _current_live_score(snapshot)
        valid_live_metrics = _has_valid_live_metrics(snapshot)
        recent_winners = _recent_product_winners(snapshot.product_events)
        product_level_connected = _has_product_level_metrics(snapshot)
        recommended_next_product = _recommend_next_product(snapshot.current_product, products, current_action)
        product_health = _product_health(snapshot, trend_30s, trend_60s)
        comment_clusters = _comment_clusters(snapshot, snapshot.comment_text or _comment_text_from_snapshot(snapshot))
        switch_recommendation = _switch_recommendation(snapshot, trend_60s, products)
        livestream_mode = _livestream_mode(snapshot, trend_30s, current_live_score)
        learned_recommendations = _learned_recommendations(snapshot, comment_clusters)

        if not valid_live_metrics:
            current_action = "No valid live metrics detected"
            next_action = "Please paste valid Taobao mtop payload or configure live connector."
            reason = ["online_uv=0", "heat_score=0", "pay_amt=0"]
            confidence = 0.0
            livestream_mode = "No valid live metrics"
        elif snapshot.sizing_comments > 0:
            current_action = "switch to sizing explanation"
            next_action = "集中讲尺码、身高体重和内搭建议"
            reason = ["comment keywords include size/尺码/身高体重", "sizing is blocking conversion", "answer sizing now"]
            confidence = 0.86
        elif snapshot.authenticity_comments > 0:
            current_action = "show authenticity proof"
            next_action = "展示吊牌、洗标、拉链和细节"
            reason = ["comment keywords include 真假/正品", "trust is blocking conversion", "show proof now"]
            confidence = 0.86
        elif trend_30s["online_uv"] == "up" and trend_30s["heat_score"] == "up" and trend_30s["pay_amt"] == "up":
            current_action = "continue product"
            next_action = "继续讲当前商品，别拉长解释，直接承接成交势能"
            reason = ["online_uv up", "heat_score up", "pay_amt up"]
            confidence = 0.86
        elif snapshot.pay_amt_5min_d_live <= 0 and trend_30s["online_uv"] == "down" and trend_30s["stay_time_pu"] == "down":
            current_action = "switch product"
            next_action = "切到下一件更容易成交的商品"
            reason = ["pay_amt_5min_d_live is 0", "online_uv is falling", "stay_time_pu is falling"]
            confidence = 0.88
        elif snapshot.heat_score > 600 and snapshot.ipv_uv_rate > 0.15:
            current_action = "push harder"
            next_action = "热度和点击都起来了，直接加速逼单"
            reason = ["heat_score > 600", "ipv_uv_rate > 15%", "traffic intent is strong"]
            confidence = 0.90
        elif snapshot.pay_amt_5min_d_live > 0 and snapshot.heat_score > 500:
            current_action = "push harder"
            next_action = "刚有成交，继续讲卖点并制造尺码紧迫感"
            reason = ["pay_amt_5min_d_live > 0", "heat_score > 500", "recent live sales confirmed"]
            confidence = 0.87
        elif snapshot.pay_byr_rate < 0.01 and snapshot.ipv_uv_rate > 0.15:
            current_action = "explain value"
            next_action = "解释价格、价值和使用场景"
            reason = ["pay_byr_rate < 1%", "ipv_uv_rate > 15%", "users are clicking but not paying"]
            confidence = 0.86
        elif recent_winners and recent_winners[0].payBuyerCnt >= 5:
            current_action = "continue product"
            next_action = "复盘刚成交的款式，顺势推荐同类商品"
            reason = ["recent product event has high payBuyerCnt", f"winner: {recent_winners[0].title}", "recommend similar product next"]
            recommended_next_product = _recommend_similar_product(recent_winners[0].title, products) or recommended_next_product
            confidence = 0.82
        elif trend_30s["online_uv"] == "down" and trend_30s["item_add_cart_rate"] == "down":
            current_action = "switch product"
            next_action = "切到下一件更容易成交的商品"
            reason = ["online_uv 30s down", "item_add_cart_rate 30s down", "traffic and cart intent are weakening"]
            confidence = 0.82
        elif trend_30s["item_click_rate"] == "up" and trend_30s["item_conversion_rate"] == "down":
            current_action = "explain value"
            next_action = "解释价格、价值和使用场景"
            reason = ["item_click_rate 30s up", "item_conversion_rate 30s down", "users are interested but not paying"]
            confidence = 0.80
        else:
            reason = _top_metric_reasons(snapshot, trend_30s, trend_60s)

        return LiveDecision(
            valid_live_metrics=valid_live_metrics,
            current_live_score=current_live_score,
            current_action=current_action,
            next_action=next_action,
            recommended_next_product=recommended_next_product,
            recent_product_winners=recent_winners,
            product_level_connected=product_level_connected,
            livestream_mode=livestream_mode,
            product_health=product_health,
            switch_recommendation=switch_recommendation,
            comment_clusters=comment_clusters,
            learned_recommendations=learned_recommendations,
            reason=reason[:3],
            confidence=confidence,
            trend_30s=trend_30s,
            trend_60s=trend_60s,
            snapshot=snapshot,
            source=snapshot.source,
        )


_TREND_FIELDS = [
    "online_uv",
    "uv",
    "pv",
    "stay_time_pu",
    "heat_score",
    "ipv_uv_rate",
    "pay_byr_rate",
    "pay_amt",
    "pay_buyer_cnt",
    "pay_item_qty",
    "comment_uv",
    "atn_uv",
    "look_uv_5min_d_live",
    "look_time_5min_avg_d_live",
    "pay_amt_5min_d_live",
    "item_click_rate",
    "item_conversion_rate",
    "item_add_cart_rate",
    "item_gmv",
]

_TIMELINE_TREND_FIELDS = [
    "online_uv",
    "stay_time_pu",
    "heat_score",
    "ipv_uv_rate",
    "pay_byr_rate",
    "comment_uv",
    "pay_amt_5min_d_live",
    "item_click_rate",
    "item_conversion_rate",
    "item_add_cart_rate",
]


def _timeline_entry(decision: LiveDecision) -> dict[str, Any]:
    trend_signature = _timeline_trend_signature(decision.trend_30s, decision.trend_60s)
    return {
        "timestamp": decision.snapshot.timestamp,
        "last_seen": decision.snapshot.timestamp,
        "decision": decision.current_action,
        "mode": decision.livestream_mode,
        "reason": decision.reason,
        "next_action": decision.next_action,
        "confidence": decision.confidence,
        "current_live_score": decision.current_live_score,
        "trend_signature": trend_signature,
        "repeat_count": 1,
        "event_type": _timeline_event_type(decision.current_action, decision.livestream_mode, trend_signature),
    }


def _should_append_timeline_entry(previous: dict[str, Any], current: dict[str, Any]) -> bool:
    if previous.get("decision") != current.get("decision"):
        return True
    if previous.get("mode") != current.get("mode"):
        return True
    if abs(float(previous.get("confidence") or 0) - float(current.get("confidence") or 0)) >= 0.08:
        return True
    if previous.get("trend_signature") != current.get("trend_signature") and _has_major_trend(current.get("trend_signature")):
        return True
    return False


def _timeline_trend_signature(trend_30s: dict[str, str], trend_60s: dict[str, str]) -> tuple[tuple[str, str, str], ...]:
    signature = []
    for field in _TIMELINE_TREND_FIELDS:
        direction_30 = trend_30s.get(field, "stable")
        direction_60 = trend_60s.get(field, "stable")
        if direction_30 != "stable" or direction_60 != "stable":
            signature.append((field, direction_30, direction_60))
    return tuple(signature)


def _has_major_trend(signature: Any) -> bool:
    return any(
        direction in {"up", "down"}
        for item in (signature or [])
        for direction in item[1:]
    )


def _timeline_event_type(decision: str, mode: str, signature: tuple[tuple[str, str, str], ...]) -> str:
    decision_text = decision.lower()
    if "no valid live metrics" in decision_text:
        return "danger"
    if "switch" in decision_text or mode == "Rescue mode":
        return "danger"
    if "push" in decision_text or any(field == "pay_amt_5min_d_live" and "up" in item for item in signature for field in item[:1]):
        return "positive"
    if "explain" in decision_text or "show" in decision_text:
        return "warning"
    return "stable"


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


def _recommend_similar_product(winning_title: str, products: list[dict[str, Any]]) -> str:
    winning_tokens = set(_product_tokens(winning_title))
    if not winning_tokens:
        return ""
    best_name = ""
    best_overlap = 0
    for product in products:
        name = str(product.get("name") or "")
        if name == winning_title:
            continue
        overlap = len(winning_tokens.intersection(_product_tokens(name)))
        if overlap > best_overlap:
            best_overlap = overlap
            best_name = name
    return best_name


def _product_tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+|[\u4e00-\u9fa5]+", text.lower())


def _best_event_title(events: list[ProductEvent]) -> str:
    winners = _recent_product_winners(events)
    return winners[0].title if winners else ""


def _recent_product_winners(events: list[ProductEvent]) -> list[ProductEvent]:
    return sorted(
        [event for event in events if event.title],
        key=lambda event: (event.payBuyerCnt, event.price),
        reverse=True,
    )[:5]


def _current_live_score(snapshot: LiveMetricSnapshot) -> float:
    if not _has_valid_live_metrics(snapshot):
        return 0.0
    heat = min(snapshot.heat_score / 800, 1.0)
    click = min(snapshot.ipv_uv_rate / 0.2, 1.0)
    conversion = min(snapshot.pay_byr_rate / 0.05, 1.0)
    pay = min(snapshot.pay_amt / 50000, 1.0)
    recent_pay = min((snapshot.pay_amt_5min_d_live or snapshot.item_gmv) / 20000, 1.0)
    online = min(snapshot.online_uv / 1000, 1.0)
    stay = min(snapshot.stay_time_pu / 180, 1.0)
    event_sales = min(sum(event.payBuyerCnt for event in snapshot.product_events[:5]) / 30, 1.0)
    return round(
        pay * 0.22
        + conversion * 0.20
        + click * 0.18
        + heat * 0.16
        + online * 0.10
        + stay * 0.07
        + recent_pay * 0.05
        + event_sales * 0.02,
        4,
    )


def _has_valid_live_metrics(snapshot: LiveMetricSnapshot) -> bool:
    return not (
        snapshot.online_uv <= 0
        and snapshot.heat_score <= 0
        and snapshot.pay_amt <= 0
        and snapshot.pay_amt_5min_d_live <= 0
        and sum(event.payBuyerCnt for event in snapshot.product_events) <= 0
    )


def _has_product_level_metrics(snapshot: LiveMetricSnapshot) -> bool:
    return snapshot.product_level_connected


def _product_health(
    snapshot: LiveMetricSnapshot,
    trend_30s: dict[str, str],
    trend_60s: dict[str, str],
) -> dict[str, Any]:
    heat = round(min(snapshot.heat_score / 800, 1.0) * 100)
    conversion = round(min(snapshot.pay_byr_rate / 0.05, 1.0) * 100)
    engagement = round(min((snapshot.comment_uv + snapshot.atn_uv) / max(snapshot.online_uv, 1), 1.0) * 100)
    fatigue = 20
    if trend_60s.get("item_click_rate") == "down" or trend_60s.get("ipv_uv_rate") == "down":
        fatigue += 25
    if trend_60s.get("comment_uv") == "down":
        fatigue += 20
    if snapshot.pay_amt_5min_d_live <= 0:
        fatigue += 25
    if trend_30s.get("stay_time_pu") == "down":
        fatigue += 15
    fatigue = min(fatigue, 100)
    if fatigue >= 70:
        status = "⚠ Current product has been shown too long. Recommended switch soon."
    elif conversion >= 60 and heat >= 60:
        status = "Current product is healthy. Keep pushing."
    else:
        status = "Watch closely. Need stronger interaction."
    return {
        "product": snapshot.current_product or "当前商品",
        "heat_score": heat,
        "conversion_score": conversion,
        "engagement_score": engagement,
        "fatigue_score": fatigue,
        "status": status,
    }


def _switch_recommendation(
    snapshot: LiveMetricSnapshot,
    trend_60s: dict[str, str],
    products: list[dict[str, Any]],
) -> dict[str, Any]:
    sales_60s = snapshot.pay_amt_5min_d_live
    viewer_drop = trend_60s.get("online_uv") == "down"
    comment_drop = trend_60s.get("comment_uv") == "down"
    current_expected = max(snapshot.pay_amt_5min_d_live, snapshot.item_gmv * 0.3)
    recommended = _recommend_next_product(snapshot.current_product, products, "switch product")
    recommended_product = next((item for item in products if item.get("name") == recommended), {})
    recommended_expected = max(
        current_expected * 1.4 if viewer_drop or comment_drop else current_expected,
        float(recommended_product.get("score") or 0) * 1600,
    )
    switch_now = sales_60s <= 0 and viewer_drop and comment_drop
    return {
        "switch_now": switch_now,
        "current_expected_gmv": round(current_expected),
        "recommended_expected_gmv": round(recommended_expected),
        "recommendation": "Switch now" if switch_now else "Hold and monitor",
    }


def _comment_clusters(snapshot: LiveMetricSnapshot, comments: str) -> dict[str, Any]:
    counts = {
        "Sizing questions": max(snapshot.sizing_comments, len(re.findall(r"尺码|穿啥|身高|体重|[1-2]\d{2}", comments))),
        "Authenticity questions": max(snapshot.authenticity_comments, len(re.findall(r"真假|正品|吊牌|洗标|鉴定", comments))),
        "Color questions": len(re.findall(r"黑色|白色|颜色|色差|有码", comments)),
        "Price questions": len(re.findall(r"价格|贵|便宜|划算|值不值|多少钱", comments)),
    }
    total = sum(counts.values()) or 1
    percentages = {key: round(value / total * 100) for key, value in counts.items()}
    ordered = sorted(percentages.items(), key=lambda item: item[1], reverse=True)
    action_map = {
        "Sizing questions": "Explain sizing",
        "Authenticity questions": "Show authenticity tags",
        "Color questions": "Show color options",
        "Price questions": "Explain price/value",
    }
    return {
        "clusters": percentages,
        "suggested_order": [action_map[name] for name, value in ordered if value > 0],
    }


def _livestream_mode(
    snapshot: LiveMetricSnapshot,
    trend_30s: dict[str, str],
    live_score: float,
) -> str:
    if snapshot.pay_amt_5min_d_live > 0 and live_score >= 0.55:
        return "Hot selling mode"
    if trend_30s.get("online_uv") == "up" and trend_30s.get("heat_score") == "up":
        return "Traffic growth mode"
    if trend_30s.get("online_uv") == "down" and trend_30s.get("stay_time_pu") == "down":
        return "Rescue mode"
    if snapshot.online_uv < 80:
        return "Opening mode"
    if snapshot.pay_amt_5min_d_live > 0 and snapshot.pay_byr_rate >= 0.03:
        return "Closing mode"
    return "Traffic dropping mode" if trend_30s.get("heat_score") == "down" else "Traffic growth mode"


def _learned_recommendations(snapshot: LiveMetricSnapshot, clusters: dict[str, Any]) -> list[str]:
    recommendations = []
    cluster_values = clusters.get("clusters", {})
    if cluster_values.get("Sizing questions", 0) >= 35:
        recommendations.append("AI learned: size explanation during the first minute often improves conversion.")
    if snapshot.pay_amt_5min_d_live > 0 and snapshot.sizing_comments > 0:
        recommendations.append("AI learned: answer sizing before price objection on this room.")
    if cluster_values.get("Authenticity questions", 0) >= 25:
        recommendations.append("AI learned: show tags early when authenticity questions rise.")
    return recommendations or ["AI learned: keep actions short and update every 30 seconds."]


def _comment_text_from_snapshot(snapshot: LiveMetricSnapshot) -> str:
    return "\n".join(
        ["尺码"] * int(snapshot.sizing_comments)
        + ["真假"] * int(snapshot.authenticity_comments)
    )


def _top_metric_reasons(
    snapshot: LiveMetricSnapshot,
    trend_30s: dict[str, str],
    trend_60s: dict[str, str],
) -> list[str]:
    return [
        f"online_uv {int(snapshot.online_uv)} / 30s {trend_30s['online_uv']}",
        f"heat_score {int(snapshot.heat_score)} / 30s {trend_30s['heat_score']}",
        f"pay_amt_5min_d_live ¥{int(snapshot.pay_amt_5min_d_live)} / 60s {trend_60s['pay_amt_5min_d_live']}",
    ]


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
