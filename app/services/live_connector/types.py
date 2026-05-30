from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

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
    host_id: str = "default"
    workspace_id: str = "default"
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
    pay_amt_td_d_shop: float = 0.0
    pay_amt_5min_d_shop: float = 0.0
    total_live_viewers: float = 0.0
    recent_5min_viewers: float = 0.0
    avg_watch_duration: float = 0.0
    live_pay_amt: float = 0.0
    live_pay_amt_5min: float = 0.0
    shop_pay_amt: float = 0.0
    shop_pay_amt_5min: float = 0.0
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
    action_code: str
    current_action: str
    next_action: str
    recommended_next_product: str
    recent_product_winners: list[ProductEvent]
    product_level_connected: bool
    livestream_mode: str
    product_health: dict[str, Any]
    product_playbook: dict[str, Any]
    switch_recommendation: dict[str, Any]
    comment_clusters: dict[str, Any]
    learned_recommendations: list[str]
    reason: list[str]
    confidence: float
    trend_30s: dict[str, str]
    trend_60s: dict[str, str]
    snapshot: LiveMetricSnapshot
    source: str
    host_id: str = "default"
    workspace_id: str = "default"
    missing_metrics: list[str] = field(default_factory=list)
    timeline: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    matched_current_product: str = ""
    current_product_match_confidence: float = 0.0


@dataclass
class LiveSessionState:
    latest_ingested_payload: dict[str, Any] | None = None
    latest_ingested_at: float = 0.0
    snapshots: list[LiveMetricSnapshot] = field(default_factory=list)
    action_history: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
