from __future__ import annotations

import os
import re
import time
from urllib.parse import unquote
from dataclasses import dataclass, field
from typing import Any

import requests
from dotenv import load_dotenv
from app.services.live_memory import director_brief, live_memory
from app.services.live_training_data import live_training_data

load_dotenv()

LATEST_EXTENSION_VERSION = os.getenv("LATEST_EXTENSION_VERSION", "0.1.3")


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


@dataclass
class LiveSessionState:
    latest_ingested_payload: dict[str, Any] | None = None
    latest_ingested_at: float = 0.0
    snapshots: list[LiveMetricSnapshot] = field(default_factory=list)
    action_history: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class LiveDataConnector:
    def __init__(self) -> None:
        self.api_url = os.getenv("LIVE_METRICS_API_URL", "").strip()
        self.api_token = os.getenv("LIVE_METRICS_API_TOKEN", "").strip()
        self.timeout = float(os.getenv("LIVE_METRICS_API_TIMEOUT", "5"))
        self.snapshots: list[LiveMetricSnapshot] = []
        self.action_history: list[dict[str, Any]] = []
        self.latest_ingested_payload: dict[str, Any] | None = None
        self.latest_ingested_at: float = 0.0
        self.sessions: dict[str, LiveSessionState] = {}

    def get_decision(
        self,
        payload: dict[str, Any] | None = None,
        products: list[dict[str, Any]] | None = None,
        host_id: str | None = None,
    ) -> LiveDecision:
        warnings: list[str] = []
        requested_host_id = _host_id_from_payload(payload, host_id)
        session = self._session(requested_host_id)
        data, source, resolved_host_id = self._fetch_live_payload(payload, warnings, requested_host_id)
        session = self._session(resolved_host_id)
        missing_metrics = _missing_required_metrics(data)
        snapshot = self._build_snapshot(data, source, resolved_host_id)
        self._store_snapshot(snapshot, session)
        trend_30s = self._trend_for(snapshot, 30, session)
        trend_60s = self._trend_for(snapshot, 60, session)
        decision = self._decide(snapshot, trend_30s, trend_60s, products or [], missing_metrics)
        self._record_action(decision, session)
        decision.warnings.extend(warnings)
        return decision

    def ingest_live_metrics(self, payload: dict[str, Any]) -> LiveDecision:
        normalized_payload = _normalize_ingested_payload(payload)
        host_id = _host_id_from_payload(normalized_payload, None)
        normalized_payload["host_id"] = host_id
        normalized_payload.setdefault("room_id", host_id)
        session = self._session(host_id)
        session.latest_ingested_payload = normalized_payload
        session.latest_ingested_at = time.time()
        self.latest_ingested_payload = normalized_payload
        self.latest_ingested_at = time.time()
        return self.get_decision(payload=self.latest_ingested_payload, host_id=host_id)

    def active_sessions(self, workspace_id: str | None = None) -> list[dict[str, Any]]:
        workspace_filter = _clean_workspace_id(workspace_id) if workspace_id else ""
        now = time.time()
        rows = []
        for host_id, session in self.sessions.items():
            latest_snapshot = session.snapshots[-1] if session.snapshots else None
            latest_action = next(
                (
                    action for action in reversed(session.action_history)
                    if action.get("event_type") != "host_feedback"
                ),
                session.action_history[-1] if session.action_history else {},
            )
            display_name = _session_display_name(host_id, session.metadata)
            row_workspace_id = _workspace_id_from_session_key(host_id, session.latest_ingested_payload)
            if workspace_filter and row_workspace_id != workspace_filter:
                continue
            feedback_stats = _host_feedback_stats(session.action_history)
            active_intervention = _active_boss_intervention(session)
            rows.append({
                "host_id": host_id,
                "workspace_id": row_workspace_id,
                "display_name": display_name,
                "metadata": dict(session.metadata),
                "live_id": _pick(session.latest_ingested_payload or {}, "liveId", "live_id", "room_id") or host_id,
                "last_updated": session.latest_ingested_at or (latest_snapshot.timestamp if latest_snapshot else 0),
                "age_seconds": round(now - (session.latest_ingested_at or 0), 1) if session.latest_ingested_at else None,
                "source": _pick(session.latest_ingested_payload or {}, "source") or (latest_snapshot.source if latest_snapshot else ""),
                "extension_version": _pick(session.latest_ingested_payload or {}, "extension_version", "extensionVersion") or "",
                "latest_extension_version": LATEST_EXTENSION_VERSION,
                "extension_update_available": _version_lt(
                    str(_pick(session.latest_ingested_payload or {}, "extension_version", "extensionVersion") or ""),
                    LATEST_EXTENSION_VERSION,
                ),
                "valid_live_metrics": _has_valid_live_metrics(latest_snapshot) if latest_snapshot else False,
                "current_action": latest_action.get("decision", ""),
                "host_feedback_count_5m": feedback_stats["count_5m"],
                "host_feedback_total": feedback_stats["total"],
                "last_host_feedback_at": feedback_stats["last_at"],
                "last_host_feedback_action": feedback_stats["last_action"],
                "last_host_feedback_sentence": feedback_stats["last_sentence"],
                "pending_boss_intervention": bool(active_intervention),
                "boss_intervention_message": active_intervention.get("message", "") if active_intervention else "",
                "boss_intervention_age_seconds": round(now - float(active_intervention.get("created_at") or now), 1) if active_intervention else None,
                "current_product": latest_snapshot.current_product if latest_snapshot else "",
                "online_uv": latest_snapshot.online_uv if latest_snapshot else 0,
                "total_viewers": (latest_snapshot.total_live_viewers or latest_snapshot.uv) if latest_snapshot else 0,
                "heat_score": latest_snapshot.heat_score if latest_snapshot else 0,
                "pay_amt": latest_snapshot.pay_amt if latest_snapshot else 0,
                "ipv_uv_rate": latest_snapshot.ipv_uv_rate if latest_snapshot else 0,
                "pay_byr_rate": latest_snapshot.pay_byr_rate if latest_snapshot else 0,
                "snapshot_count": len(session.snapshots),
                "product_level_connected": _has_product_level_metrics(latest_snapshot) if latest_snapshot else False,
                "metric_keys": sorted(
                    key for key, value in (session.latest_ingested_payload or {}).items()
                    if key not in {"source", "liveId", "live_id", "host_id", "workspace_id", "workspaceId", "binding_code", "bindingCode", "room_id", "timestamp", "captured_api", "extension_version", "interactSecKill"}
                    and value is not None
                ),
            })
        return sorted(rows, key=lambda item: item.get("last_updated") or 0, reverse=True)

    def session_history(self, host_id: str) -> dict[str, Any]:
        clean_host_id = _clean_host_id(host_id)
        session = self.sessions.get(clean_host_id)
        if session is None:
            return {
                "host_id": clean_host_id,
                "summary": None,
                "snapshots": [],
                "actions": [],
            }
        summary = next((item for item in self.active_sessions() if item.get("host_id") == clean_host_id), None)
        return {
            "host_id": clean_host_id,
            "summary": summary,
            "metadata": dict(session.metadata),
            "snapshots": [_snapshot_summary(snapshot) for snapshot in session.snapshots[-120:]],
            "actions": list(reversed(session.action_history[-30:])),
            "action_effects": _action_effects(session.action_history),
            "action_leaderboard": _action_leaderboard(session.action_history),
            "director_score_card": _director_score_card(session.action_history),
            "host_profile": live_memory.host_profile(clean_host_id),
            "product_profile": live_memory.product_profile(
                session.snapshots[-1].current_product if session.snapshots else ""
            ),
            "post_live_summary": _post_live_summary(
                [_snapshot_summary(snapshot) for snapshot in session.snapshots[-120:]],
                list(reversed(session.action_history[-30:])),
                session.metadata,
            ),
        }

    def boss_dashboard(self, workspace_id: str | None = None) -> dict[str, Any]:
        clean_workspace_id = _clean_workspace_id(workspace_id) if workspace_id else ""
        sessions = self.active_sessions(workspace_id=clean_workspace_id or None)
        active = [session for session in sessions if _session_age(session) <= 120]
        risks = [_boss_risk_card(session) for session in active]
        risks = [risk for risk in risks if risk["level"] != "ok"]
        ranked = sorted(active, key=lambda item: float(item.get("pay_amt") or 0), reverse=True)
        return {
            "workspace_id": clean_workspace_id or "all",
            "workspace_options": self.workspace_options(),
            "active_count": len(active),
            "valid_count": len([session for session in active if session.get("valid_live_metrics")]),
            "total_gmv": sum(float(session.get("pay_amt") or 0) for session in active),
            "risk_count": len(risks),
            "top_room": ranked[0] if ranked else None,
            "risk_rooms": risks[:8],
            "rooms": [_boss_room_card(session) for session in active[:20]],
        }

    def workspace_options(self) -> list[str]:
        workspaces = {
            _workspace_id_from_session_key(host_id, session.latest_ingested_payload)
            for host_id, session in self.sessions.items()
        }
        return sorted(workspace for workspace in workspaces if workspace)

    def record_host_feedback(self, host_id: str | None, payload: dict[str, Any]) -> dict[str, Any]:
        resolved_host_id = _host_id_from_payload(payload, host_id)
        session = self._session(resolved_host_id)
        action = str(payload.get("action") or "").strip()[:120] or "已执行 AI 建议"
        sentence = str(payload.get("sentence") or "").strip()[:240]
        product = str(payload.get("current_product") or payload.get("product") or "").strip()[:160]
        before_snapshot = session.snapshots[-1] if session.snapshots else None
        product_context = _product_context_for_feedback(session, product, before_snapshot)
        entry = {
            "timestamp": time.time(),
            "last_seen": time.time(),
            "decision": "主播已执行",
            "mode": "Host feedback",
            "reason": [action, product][:2],
            "next_action": sentence,
            "confidence": 1.0,
            "current_live_score": 0,
            "trend_signature": (),
            "repeat_count": 1,
            "event_type": "host_feedback",
            "action_label": action,
            "product": product,
            "product_position": product_context.get("product_position"),
            "product_elapsed_seconds": product_context.get("product_elapsed_seconds"),
            "effect_status": "pending" if before_snapshot else "waiting_for_metrics",
            "effect_window_seconds": 30,
            "before_metrics": _effect_metrics(before_snapshot),
            "after_metrics": {},
            "effect_delta": {},
            "effect_result": "等待数据",
            "effect_due_at": time.time() + 30,
        }
        session.action_history.append(entry)
        session.action_history = session.action_history[-120:]
        self.action_history = session.action_history
        return {"ok": True, "host_id": resolved_host_id, "feedback": entry}

    def set_boss_intervention(self, host_id: str | None, payload: dict[str, Any]) -> dict[str, Any]:
        resolved_host_id = _host_id_from_payload(payload, host_id)
        session = self._session(resolved_host_id)
        message = str(payload.get("message") or payload.get("action") or "").strip()[:160]
        if not message:
            message = "人工提醒：请结合 AI 数据建议调整讲解。"
        intervention = {
            "message": message,
            "created_at": time.time(),
            "created_by": str(payload.get("created_by") or "boss")[:80],
            "host_id": resolved_host_id,
            "status": "pending",
        }
        session.metadata["boss_intervention"] = intervention
        return {"ok": True, "host_id": resolved_host_id, "intervention": intervention}

    def get_boss_intervention(self, host_id: str | None, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        resolved_host_id = _host_id_from_payload(payload or {}, host_id)
        session = self.sessions.get(resolved_host_id)
        intervention = _active_boss_intervention(session) if session else None
        if not intervention:
            return {"ok": True, "host_id": resolved_host_id, "intervention": None}
        intervention = dict(intervention)
        age_seconds = time.time() - float(intervention.get("created_at") or 0)
        intervention["age_seconds"] = round(age_seconds, 1)
        return {"ok": True, "host_id": resolved_host_id, "intervention": intervention}

    def ack_boss_intervention(self, host_id: str | None, payload: dict[str, Any]) -> dict[str, Any]:
        resolved_host_id = _host_id_from_payload(payload, host_id)
        session = self.sessions.get(resolved_host_id)
        if not session:
            return {"ok": False, "host_id": resolved_host_id, "intervention": None}
        intervention = session.metadata.get("boss_intervention")
        if not isinstance(intervention, dict) or not intervention:
            return {"ok": False, "host_id": resolved_host_id, "intervention": None}
        intervention["status"] = "acknowledged"
        intervention["acknowledged_at"] = time.time()
        intervention["acknowledged_by"] = str(payload.get("acknowledged_by") or "host")[:80]
        session.metadata["boss_intervention"] = intervention
        message = str(intervention.get("message") or "人工提醒").strip()[:160]
        entry = {
            "timestamp": time.time(),
            "last_seen": time.time(),
            "decision": "主播已确认人工提醒",
            "mode": "Manual intervention ack",
            "reason": [message],
            "next_action": "已收到人工提醒，继续以实时数据指挥为主",
            "confidence": 1.0,
            "current_live_score": 0,
            "trend_signature": (),
            "repeat_count": 1,
            "event_type": "boss_intervention_ack",
        }
        session.action_history.append(entry)
        session.action_history = session.action_history[-120:]
        self.action_history = session.action_history
        return {"ok": True, "host_id": resolved_host_id, "intervention": dict(intervention)}

    def update_session_metadata(self, host_id: str, metadata: dict[str, Any]) -> dict[str, Any]:
        clean_host_id = _clean_host_id(host_id)
        session = self._session(clean_host_id)
        allowed_keys = {"session_name", "host_name", "owner", "target_gmv", "notes"}
        for key in allowed_keys:
            value = metadata.get(key)
            if value is None:
                continue
            session.metadata[key] = str(value).strip()[:160]
        return {"host_id": clean_host_id, "metadata": dict(session.metadata)}

    def _session(self, host_id: str) -> LiveSessionState:
        if host_id not in self.sessions:
            self.sessions[host_id] = LiveSessionState()
        return self.sessions[host_id]

    def _fresh_sessions(self) -> list[tuple[str, LiveSessionState]]:
        now = time.time()
        return [
            (host_id, session)
            for host_id, session in self.sessions.items()
            if session.latest_ingested_payload and now - session.latest_ingested_at <= 30
        ]

    def _record_action(self, decision: LiveDecision, session: LiveSessionState) -> None:
        entry = _timeline_entry(decision)
        last = next(
            (
                action for action in reversed(session.action_history)
                if action.get("event_type") not in {"host_feedback", "boss_intervention_ack"}
            ),
            None,
        )
        if last and not _should_append_timeline_entry(last, entry):
            last["last_seen"] = entry["timestamp"]
            last["repeat_count"] = int(last.get("repeat_count") or 1) + 1
            last["confidence"] = entry["confidence"]
            last["current_live_score"] = entry["current_live_score"]
            decision.timeline = [
                item for item in reversed(session.action_history)
                if item.get("event_type") not in {"host_feedback", "boss_intervention_ack"}
            ][:10]
            return

        session.action_history.append(entry)
        session.action_history = session.action_history[-120:]
        self.action_history = session.action_history
        decision.timeline = [
            item for item in reversed(session.action_history)
            if item.get("event_type") not in {"host_feedback", "boss_intervention_ack"}
        ][:10]

    def _fetch_live_payload(
        self,
        payload: dict[str, Any] | None,
        warnings: list[str],
        host_id: str,
    ) -> tuple[dict[str, Any], str, str]:
        session = self._session(host_id)
        if session.latest_ingested_payload and time.time() - session.latest_ingested_at <= 30:
            data = _overlay_live_context(_unwrap_payload(session.latest_ingested_payload), payload)
            return data, "chrome_extension", host_id

        fresh_sessions = self._fresh_sessions()
        if host_id == "default" and len(fresh_sessions) == 1:
            active_host_id, active_session = fresh_sessions[0]
            warnings.append(f"Using only active live room: {active_host_id}")
            data = _overlay_live_context(_unwrap_payload(active_session.latest_ingested_payload), payload)
            return data, "chrome_extension", active_host_id
        if host_id == "default" and len(fresh_sessions) > 1:
            warnings.append("Multiple active live rooms detected. Set host_id/liveId to choose one.")

        if payload:
            normalized_payload = _normalize_ingested_payload(payload)
            source = "chrome_extension" if normalized_payload.get("source") == "chrome_extension" else "page_payload"
            resolved_host_id = _host_id_from_payload(normalized_payload, host_id)
            return _unwrap_payload(normalized_payload), source, resolved_host_id

        if self.api_url:
            try:
                headers = {}
                if self.api_token:
                    headers["Authorization"] = f"Bearer {self.api_token}"
                response = requests.get(self.api_url, headers=headers, timeout=self.timeout)
                response.raise_for_status()
                normalized_payload = _normalize_ingested_payload(response.json())
                resolved_host_id = _host_id_from_payload(normalized_payload, host_id)
                return _unwrap_payload(normalized_payload), "real_api", resolved_host_id
            except Exception as exc:
                warnings.append(f"Live metrics API failed, using fallback data: {exc}")

        warnings.append("No live metrics connector data found.")
        return {}, "no_connector", host_id

    def _build_snapshot(self, data: dict[str, Any], source: str, host_id: str) -> LiveMetricSnapshot:
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
        look_uv_td_d_live = _to_number(_pick(data_region, "look_uv_td_d_live") or _pick(data, "look_uv_td_d_live"))
        look_uv_5min_d_live = _to_number(_pick(data_region, "look_uv_5min_d_live") or _pick(data, "look_uv_5min_d_live"))
        look_time_td_avg_d_live = _to_number(_pick(data_region, "look_time_td_avg_d_live") or _pick(data, "look_time_td_avg_d_live"))
        pay_amt_td_d_live = _to_number(_pick(data_region, "pay_amt_td_d_live") or _pick(data, "pay_amt_td_d_live"))
        pay_amt_5min_d_live = _to_number(_pick(data_region, "pay_amt_5min_d_live") or _pick(data, "pay_amt_5min_d_live"))
        pay_amt_td_d_shop = _to_number(_pick(data_region, "pay_amt_td_d_shop") or _pick(data, "pay_amt_td_d_shop"))
        pay_amt_5min_d_shop = _to_number(_pick(data_region, "pay_amt_5min_d_shop") or _pick(data, "pay_amt_5min_d_shop"))
        return LiveMetricSnapshot(
            timestamp=time.time(),
            host_id=host_id,
            workspace_id=_workspace_id_from_session_key(host_id, data),
            online_uv=_to_number(_pick(total_stats, "online_uv", "onlineUv")),
            uv=_to_number(_pick(total_stats, "uv")),
            pv=_to_number(_pick(total_stats, "pv")),
            stay_time_pu=_to_number(_pick(total_stats, "stay_time_pu", "stayTimePu")),
            heat_score=_to_number(_pick(total_stats, "heat_score", "heatScore")),
            ipv_uv_rate=ipv_uv_rate,
            pay_byr_rate=pay_byr_rate,
            pay_amt=_to_number(_pick(total_stats, "pay_amt", "payAmt")),
            pay_buyer_cnt=_to_number(_pick(total_stats, "pay_buyer_cnt", "payBuyerCnt")),
            pay_item_qty=_to_number(_pick(total_stats, "pay_item_qty", "payItemQty")),
            refund_amt=_to_number(_pick(total_stats, "refund_amt", "refundAmt")),
            comment_uv=_to_number(_pick(total_stats, "comment_uv", "commentUv")),
            atn_uv=_to_number(_pick(total_stats, "atn_uv", "atnUv")),
            look_uv_td_d_live=look_uv_td_d_live,
            look_time_td_avg_d_live=look_time_td_avg_d_live,
            pay_amt_td_d_live=pay_amt_td_d_live,
            look_uv_5min_d_live=look_uv_5min_d_live,
            look_time_5min_avg_d_live=_to_number(_pick(data_region, "look_time_5min_avg_d_live")),
            pay_amt_5min_d_live=pay_amt_5min_d_live,
            pay_amt_td_d_shop=pay_amt_td_d_shop,
            pay_amt_5min_d_shop=pay_amt_5min_d_shop,
            total_live_viewers=look_uv_td_d_live,
            recent_5min_viewers=look_uv_5min_d_live,
            avg_watch_duration=look_time_td_avg_d_live,
            live_pay_amt=pay_amt_td_d_live,
            live_pay_amt_5min=pay_amt_5min_d_live,
            shop_pay_amt=pay_amt_td_d_shop,
            shop_pay_amt_5min=pay_amt_5min_d_shop,
            item_click_rate=_normalize_rate(_pick(data, "item_click_rate", "itemClickRate")),
            item_conversion_rate=_normalize_rate(_pick(data, "item_conversion_rate", "itemConversionRate")),
            item_add_cart_rate=_normalize_rate(_pick(data, "item_add_cart_rate", "itemAddCartRate", "cart_rate")),
            item_gmv=_to_number(_pick(data, "item_gmv", "itemGmv")),
            current_product=str(_pick(data, "current_product", "item_name", "itemName") or _best_event_title(events)).strip(),
            product_level_connected=explicit_product_metrics,
            product_events=events,
            authenticity_comments=int(_to_number(_pick(data, "authenticity_comments", "authenticity_questions"))) or _count_authenticity_comments(comments),
            sizing_comments=int(_to_number(_pick(data, "sizing_comments", "sizing_questions"))) or _count_sizing_comments(comments),
            comment_text=comments,
            source=source,
        )

    def _store_snapshot(self, snapshot: LiveMetricSnapshot, session: LiveSessionState) -> None:
        session.snapshots.append(snapshot)
        cutoff = time.time() - 5 * 60
        session.snapshots = [item for item in session.snapshots if item.timestamp >= cutoff]
        self.snapshots.append(snapshot)
        self.snapshots = [item for item in self.snapshots if item.timestamp >= cutoff]
        _update_action_effects(session, snapshot)

    def _trend_for(self, current: LiveMetricSnapshot, seconds: int, session: LiveSessionState) -> dict[str, str]:
        previous = self._snapshot_ago(seconds, session)
        if previous is None:
            return {key: "stable" for key in _TREND_FIELDS}
        return {
            field: _trend_direction(getattr(current, field), getattr(previous, field))
            for field in _TREND_FIELDS
        }

    def _snapshot_ago(self, seconds: int, session: LiveSessionState) -> LiveMetricSnapshot | None:
        target = time.time() - seconds
        candidate = None
        for snapshot in session.snapshots:
            if snapshot.timestamp <= target:
                candidate = snapshot
        return candidate

    def _decide(
        self,
        snapshot: LiveMetricSnapshot,
        trend_30s: dict[str, str],
        trend_60s: dict[str, str],
        products: list[dict[str, Any]],
        missing_metrics: list[str],
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
        product_playbook = director_brief(snapshot.current_product)
        comment_clusters = _comment_clusters(snapshot, snapshot.comment_text or _comment_text_from_snapshot(snapshot))
        switch_recommendation = _switch_recommendation(snapshot, trend_60s, products)
        livestream_mode = _livestream_mode(snapshot, trend_30s, current_live_score)
        learned_recommendations = _learned_recommendations(snapshot, comment_clusters)

        if missing_metrics:
            current_action = "数据不完整，等待 totalStats / 插件补齐"
            next_action = "先观察，不要根据缺失指标切品，等待 totalStats / 插件补齐。"
            reason = [f"missing: {metric}" for metric in missing_metrics[:3]]
            confidence = 0.0
            livestream_mode = "Waiting for complete live metrics"
        elif not valid_live_metrics:
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

        next_action = _apply_product_playbook(next_action, product_playbook, current_action)

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
            product_playbook=product_playbook,
            switch_recommendation=switch_recommendation,
            comment_clusters=comment_clusters,
            learned_recommendations=learned_recommendations,
            reason=reason[:3],
            confidence=confidence,
            trend_30s=trend_30s,
            trend_60s=trend_60s,
            snapshot=snapshot,
            source=snapshot.source,
            host_id=snapshot.host_id,
            workspace_id=snapshot.workspace_id,
            missing_metrics=missing_metrics,
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


def _snapshot_summary(snapshot: LiveMetricSnapshot) -> dict[str, Any]:
    return {
        "timestamp": snapshot.timestamp,
        "host_id": snapshot.host_id,
        "workspace_id": snapshot.workspace_id,
        "source": snapshot.source,
        "current_product": snapshot.current_product,
        "online_uv": snapshot.online_uv,
        "total_viewers": snapshot.total_live_viewers or snapshot.uv,
        "uv": snapshot.uv,
        "pv": snapshot.pv,
        "heat_score": snapshot.heat_score,
        "pay_amt": snapshot.pay_amt,
        "pay_byr_rate": snapshot.pay_byr_rate,
        "ipv_uv_rate": snapshot.ipv_uv_rate,
        "stay_time_pu": snapshot.stay_time_pu,
        "comment_uv": snapshot.comment_uv,
        "pay_item_qty": snapshot.pay_item_qty,
        "pay_buyer_cnt": snapshot.pay_buyer_cnt,
        "item_click_rate": snapshot.item_click_rate,
        "item_conversion_rate": snapshot.item_conversion_rate,
        "item_add_cart_rate": snapshot.item_add_cart_rate,
        "item_gmv": snapshot.item_gmv,
        "product_level_connected": snapshot.product_level_connected,
    }


def _session_display_name(host_id: str, metadata: dict[str, Any]) -> str:
    parts = [
        str(metadata.get("host_name") or "").strip(),
        str(metadata.get("session_name") or "").strip(),
    ]
    text = "｜".join(part for part in parts if part)
    return text or host_id


def _post_live_summary(
    snapshots: list[dict[str, Any]],
    actions: list[dict[str, Any]],
    metadata: dict[str, Any],
) -> dict[str, Any]:
    if not snapshots:
        return {
            "status": "waiting",
            "headline": "暂无可复盘数据",
            "highlights": [],
            "risks": ["等待插件发送直播数据。"],
            "next_suggestions": ["先确认插件连接，再开始记录本场数据。"],
            "best_moment": "--",
            "weak_moment": "--",
        }

    first = snapshots[0]
    last = snapshots[-1]
    max_pay = max(snapshots, key=lambda item: float(item.get("pay_amt") or 0))
    max_heat = max(snapshots, key=lambda item: float(item.get("heat_score") or 0))
    max_ctr = max(snapshots, key=lambda item: float(item.get("ipv_uv_rate") or 0))
    pay_delta = float(last.get("pay_amt") or 0) - float(first.get("pay_amt") or 0)
    viewer_delta = float(last.get("total_viewers") or 0) - float(first.get("total_viewers") or 0)
    avg_cvr = sum(float(item.get("pay_byr_rate") or 0) for item in snapshots) / max(len(snapshots), 1)
    avg_ctr = sum(float(item.get("ipv_uv_rate") or 0) for item in snapshots) / max(len(snapshots), 1)
    action_counts: dict[str, int] = {}
    for action in actions:
        key = str(action.get("decision") or "unknown")
        action_counts[key] = action_counts.get(key, 0) + 1
    top_action = max(action_counts.items(), key=lambda item: item[1])[0] if action_counts else "--"

    highlights = [
        f"本段 GMV 增量约 ¥{pay_delta:,.0f}。",
        f"最高热度 {float(max_heat.get('heat_score') or 0):,.0f}，出现在 { _time_label(max_heat.get('timestamp')) }。",
        f"最高 CTR {float(max_ctr.get('ipv_uv_rate') or 0) * 100:.1f}%。",
    ]
    risks: list[str] = []
    if avg_ctr >= 0.08 and avg_cvr < 0.02:
        risks.append("点击不错但成交偏弱，下场要更早解释价格价值。")
    if avg_ctr < 0.04:
        risks.append("整体点击偏低，商品开场钩子和镜头展示需要更直接。")
    if pay_delta <= 0:
        risks.append("本段没有明显成交增长，建议缩短单品讲解并更快切换。")
    if viewer_delta < 0:
        risks.append("观看人数走低，出现疲劳时要更早换款或做互动。")
    if not risks:
        risks.append("整体数据健康，继续保持当前节奏。")

    next_suggestions = [
        "高 CTR 低 CVR 时，先讲价格价值，不要立刻换款。",
        "单品超过 90 秒仍无成交，准备收口切下一件。",
        "尺码/真假评论集中出现时，优先回答阻碍成交的问题。",
    ]
    if metadata.get("target_gmv"):
        next_suggestions.insert(0, f"围绕目标 GMV {metadata.get('target_gmv')}，优先保留成交效率高的款。")

    return {
        "status": "ready",
        "headline": f"本段累计 {len(snapshots)} 个快照，GMV 增量约 ¥{pay_delta:,.0f}",
        "highlights": highlights,
        "risks": risks,
        "next_suggestions": next_suggestions,
        "best_moment": f"{_time_label(max_pay.get('timestamp'))} · GMV ¥{float(max_pay.get('pay_amt') or 0):,.0f}",
        "weak_moment": f"常见动作：{top_action}",
        "avg_ctr": avg_ctr,
        "avg_cvr": avg_cvr,
        "pay_delta": pay_delta,
        "viewer_delta": viewer_delta,
    }


def _boss_room_card(session: dict[str, Any]) -> dict[str, Any]:
    score = _host_execution_score(session)
    return {
        "host_id": session.get("host_id", "default"),
        "workspace_id": session.get("workspace_id", "default"),
        "display_name": session.get("display_name") or session.get("host_id", "default"),
        "pay_amt": float(session.get("pay_amt") or 0),
        "online_uv": float(session.get("online_uv") or 0),
        "heat_score": float(session.get("heat_score") or 0),
        "ctr": float(session.get("ipv_uv_rate") or 0),
        "cvr": float(session.get("pay_byr_rate") or 0),
        "current_action": session.get("current_action") or "--",
        "current_product": session.get("current_product") or "--",
        "host_feedback_count_5m": int(session.get("host_feedback_count_5m") or 0),
        "host_feedback_total": int(session.get("host_feedback_total") or 0),
        "last_host_feedback_at": float(session.get("last_host_feedback_at") or 0),
        "last_host_feedback_action": session.get("last_host_feedback_action") or "",
        "last_host_feedback_sentence": session.get("last_host_feedback_sentence") or "",
        "pending_boss_intervention": bool(session.get("pending_boss_intervention")),
        "boss_intervention_message": session.get("boss_intervention_message") or "",
        "boss_intervention_age_seconds": session.get("boss_intervention_age_seconds"),
        "execution_score": score,
        "age_seconds": session.get("age_seconds"),
        "extension_update_available": bool(session.get("extension_update_available")),
    }


def _active_boss_intervention(session: LiveSessionState | None) -> dict[str, Any] | None:
    if not session:
        return None
    intervention = session.metadata.get("boss_intervention")
    if not isinstance(intervention, dict) or not intervention:
        return None
    if intervention.get("status") == "acknowledged" or intervention.get("acknowledged_at"):
        return None
    created_at = float(intervention.get("created_at") or 0)
    if not created_at or time.time() - created_at > 180:
        return None
    return dict(intervention)


def _boss_risk_card(session: dict[str, Any]) -> dict[str, Any]:
    ctr = float(session.get("ipv_uv_rate") or 0)
    cvr = float(session.get("pay_byr_rate") or 0)
    online_uv = float(session.get("online_uv") or 0)
    pay_amt = float(session.get("pay_amt") or 0)
    age = _session_age(session)
    level = "ok"
    reason = "数据健康"
    action = "继续观察"
    estimated_loss = 0.0
    if age > 60:
        level = "high"
        reason = "插件数据超过 60 秒未更新"
        action = "联系主播刷新淘宝中控或插件"
    elif ctr >= 0.08 and cvr < 0.02:
        level = "high"
        reason = "点击高但成交低，价格价值没有讲透"
        action = "立刻让主播解释价格、通勤场景和不吃灰价值"
        estimated_loss = max(300.0, online_uv * ctr * 80)
    elif online_uv >= 50 and pay_amt <= 0:
        level = "high"
        reason = "在线人数有量但没有成交"
        action = "切到更容易成交的商品，或做限时逼单"
        estimated_loss = max(500.0, online_uv * 20)
    elif session.get("extension_update_available"):
        level = "medium"
        reason = "主播插件不是最新版"
        action = "下播后让主播重新下载安装插件"
    elif float(session.get("online_uv") or 0) >= 20 and int(session.get("host_feedback_count_5m") or 0) <= 0:
        level = "medium"
        reason = "主播 5 分钟内没有标记执行 AI 建议"
        action = "提醒主播点“我已照做”，方便复盘执行力"
    elif ctr < 0.03 and online_uv > 20:
        level = "medium"
        reason = "点击偏低，商品开场吸引力不足"
        action = "换更强开场钩子，镜头拉近展示细节"
    return {
        "level": level,
        "host_id": session.get("host_id", "default"),
        "workspace_id": session.get("workspace_id", "default"),
        "display_name": session.get("display_name") or session.get("host_id", "default"),
        "reason": reason,
        "action": action,
        "estimated_loss": estimated_loss,
    }


def _host_execution_score(session: dict[str, Any]) -> int:
    score = 70
    ctr = float(session.get("ipv_uv_rate") or 0)
    cvr = float(session.get("pay_byr_rate") or 0)
    heat = float(session.get("heat_score") or 0)
    age = _session_age(session)
    if ctr >= 0.08:
        score += 10
    elif ctr < 0.03:
        score -= 10
    if cvr >= 0.025:
        score += 12
    elif ctr >= 0.08 and cvr < 0.02:
        score -= 12
    if heat >= 500:
        score += 6
    feedback_count = int(session.get("host_feedback_count_5m") or 0)
    if feedback_count >= 3:
        score += 10
    elif feedback_count >= 1:
        score += 5
    elif float(session.get("online_uv") or 0) >= 20:
        score -= 8
    if age > 60:
        score -= 15
    if session.get("extension_update_available"):
        score -= 4
    return max(0, min(100, score))


def _host_feedback_stats(actions: list[dict[str, Any]]) -> dict[str, Any]:
    now = time.time()
    feedback = [action for action in actions if action.get("event_type") == "host_feedback"]
    recent = [action for action in feedback if now - float(action.get("timestamp") or 0) <= 300]
    last = feedback[-1] if feedback else {}
    return {
        "count_5m": len(recent),
        "total": len(feedback),
        "last_at": float(last.get("timestamp") or 0),
        "last_action": " / ".join(str(item) for item in (last.get("reason") or []) if item)[:120],
        "last_sentence": str(last.get("next_action") or "")[:160],
    }


def _effect_metrics(snapshot: LiveMetricSnapshot | None) -> dict[str, float]:
    if snapshot is None:
        return {}
    return {
        "timestamp": snapshot.timestamp,
        "ctr": float(snapshot.item_click_rate or 0),
        "cvr": float(snapshot.item_conversion_rate or 0),
        "gmv": float(snapshot.item_gmv or 0),
        "heat": float(snapshot.heat_score or 0),
        "comments": float(snapshot.comment_uv or 0),
        "online_uv": float(snapshot.online_uv or 0),
    }


def _product_context_for_feedback(
    session: LiveSessionState,
    product_name: str,
    before_snapshot: LiveMetricSnapshot | None,
) -> dict[str, Any]:
    target = product_name or (before_snapshot.current_product if before_snapshot else "")
    if not target:
        return {"product_position": None, "product_elapsed_seconds": None}
    seen_order: list[str] = []
    first_seen_at = 0.0
    for snapshot in session.snapshots:
        name = snapshot.current_product or ""
        if name and name not in seen_order:
            seen_order.append(name)
        if name == target and not first_seen_at:
            first_seen_at = snapshot.timestamp
    position = seen_order.index(target) + 1 if target in seen_order else None
    elapsed = None
    if before_snapshot and first_seen_at:
        elapsed = max(0, round(before_snapshot.timestamp - first_seen_at))
    return {
        "product_position": position,
        "product_elapsed_seconds": elapsed,
    }


def _nearest_ai_decision(session: LiveSessionState, timestamp: float) -> dict[str, Any]:
    candidates = [
        action for action in session.action_history
        if action.get("event_type") not in {"host_feedback", "boss_intervention_ack"}
    ]
    if not candidates:
        return {}
    if timestamp <= 0:
        selected = candidates[-1]
    else:
        selected = min(candidates, key=lambda item: abs(float(item.get("timestamp") or 0) - timestamp))
    return {
        "timestamp": selected.get("timestamp"),
        "decision": selected.get("decision"),
        "mode": selected.get("mode"),
        "reason": selected.get("reason") or [],
        "next_action": selected.get("next_action"),
        "confidence": selected.get("confidence"),
        "current_live_score": selected.get("current_live_score"),
    }


def _product_switched_during_window(action: dict[str, Any], snapshot: LiveMetricSnapshot) -> bool:
    action_product = str(action.get("product") or "").strip()
    current_product = str(snapshot.current_product or "").strip()
    if not action_product or not current_product:
        return False
    return action_product != current_product


def _update_action_effects(session: LiveSessionState, snapshot: LiveMetricSnapshot) -> None:
    after = _effect_metrics(snapshot)
    if not after:
        return
    now = time.time()
    for action in session.action_history:
        if action.get("event_type") != "host_feedback":
            continue
        if action.get("effect_status") != "pending":
            continue
        if now < float(action.get("effect_due_at") or 0):
            continue
        before = action.get("before_metrics") if isinstance(action.get("before_metrics"), dict) else {}
        if not before:
            action["effect_status"] = "waiting_for_metrics"
            action["effect_result"] = "等待数据"
            continue
        delta = {
            "ctr": after["ctr"] - float(before.get("ctr") or 0),
            "cvr": after["cvr"] - float(before.get("cvr") or 0),
            "gmv": after["gmv"] - float(before.get("gmv") or 0),
            "heat": after["heat"] - float(before.get("heat") or 0),
            "comments": after["comments"] - float(before.get("comments") or 0),
            "online_uv": after["online_uv"] - float(before.get("online_uv") or 0),
        }
        score = _effect_score(delta)
        action["after_metrics"] = after
        action["effect_delta"] = delta
        action["effect_score"] = score
        action["effect_status"] = "done"
        action["effect_result"] = "有效" if score >= 0 else "无效"
        action["effect_summary"] = _effect_summary(delta)
        live_memory.record_action_effect(
            host_id=snapshot.host_id,
            product_name=str(action.get("product") or snapshot.current_product or ""),
            action=str(action.get("action_label") or action.get("reason", ["已执行 AI 建议"])[0]),
            delta=delta,
            result=str(action["effect_result"]),
            context={
                "after_cvr": after.get("cvr"),
                "product_position": action.get("product_position"),
                "product_elapsed_seconds": action.get("product_elapsed_seconds"),
                "product_switched_during_window": _product_switched_during_window(action, snapshot),
            },
        )
        live_training_data.record_sample(
            host_id=snapshot.host_id,
            product_name=str(action.get("product") or snapshot.current_product or ""),
            ai_decision=_nearest_ai_decision(session, float(action.get("timestamp") or 0)),
            host_action=action,
            before_metrics=before,
            after_metrics=after,
            delta=delta,
            result=str(action["effect_result"]),
            context={
                "product_position": action.get("product_position"),
                "product_elapsed_seconds": action.get("product_elapsed_seconds"),
                "comments": snapshot.comment_text,
                "traffic_source": snapshot.source,
                "product_switched_during_window": _product_switched_during_window(action, snapshot),
            },
        )


def _effect_score(delta: dict[str, float]) -> float:
    return (
        float(delta.get("gmv") or 0) / 100
        + float(delta.get("cvr") or 0) * 1000
        + float(delta.get("ctr") or 0) * 350
        + float(delta.get("heat") or 0) / 20
        + float(delta.get("comments") or 0) / 10
    )


def _effect_summary(delta: dict[str, float]) -> str:
    parts: list[str] = []
    gmv = float(delta.get("gmv") or 0)
    cvr = float(delta.get("cvr") or 0)
    ctr = float(delta.get("ctr") or 0)
    heat = float(delta.get("heat") or 0)
    if gmv:
        parts.append(f"GMV {'+' if gmv > 0 else ''}¥{gmv:,.0f}")
    if cvr:
        parts.append(f"CVR {'+' if cvr > 0 else ''}{cvr * 100:.1f}%")
    if ctr:
        parts.append(f"CTR {'+' if ctr > 0 else ''}{ctr * 100:.1f}%")
    if heat:
        parts.append(f"热度 {'+' if heat > 0 else ''}{heat:,.0f}")
    return " / ".join(parts[:3]) or "暂无明显变化"


def _action_effects(actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    feedback = [
        action for action in actions
        if action.get("event_type") == "host_feedback"
    ]
    return list(reversed(feedback[-20:]))


def _action_leaderboard(actions: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, dict[str, Any]] = {}
    for action in actions:
        if action.get("event_type") != "host_feedback" or action.get("effect_status") != "done":
            continue
        label = str(action.get("action_label") or "已执行 AI 建议")[:80]
        item = grouped.setdefault(label, {
            "action": label,
            "count": 0,
            "score_total": 0.0,
            "gmv_delta": 0.0,
            "cvr_delta": 0.0,
            "ctr_delta": 0.0,
            "heat_delta": 0.0,
            "last_summary": "",
        })
        delta = action.get("effect_delta") if isinstance(action.get("effect_delta"), dict) else {}
        item["count"] += 1
        item["score_total"] += float(action.get("effect_score") or 0)
        item["gmv_delta"] += float(delta.get("gmv") or 0)
        item["cvr_delta"] += float(delta.get("cvr") or 0)
        item["ctr_delta"] += float(delta.get("ctr") or 0)
        item["heat_delta"] += float(delta.get("heat") or 0)
        item["last_summary"] = str(action.get("effect_summary") or "")

    rows = []
    for item in grouped.values():
        count = max(int(item["count"]), 1)
        rows.append({
            "action": item["action"],
            "count": count,
            "avg_score": item["score_total"] / count,
            "avg_gmv_delta": item["gmv_delta"] / count,
            "avg_cvr_delta": item["cvr_delta"] / count,
            "avg_ctr_delta": item["ctr_delta"] / count,
            "avg_heat_delta": item["heat_delta"] / count,
            "summary": item["last_summary"],
        })
    return {
        "top": sorted(rows, key=lambda item: item["avg_score"], reverse=True)[:5],
        "worst": sorted(rows, key=lambda item: item["avg_score"])[:5],
    }


def _director_score_card(actions: list[dict[str, Any]]) -> dict[str, Any]:
    timeline_actions = [
        action for action in actions
        if action.get("event_type") not in {"host_feedback", "boss_intervention_ack"}
    ]
    feedback = [action for action in actions if action.get("event_type") == "host_feedback"]
    completed = [action for action in feedback if action.get("effect_status") == "done"]
    effective = [action for action in completed if action.get("effect_result") == "有效"]
    leaderboard = _action_leaderboard(actions)
    best = leaderboard["top"][0]["action"] if leaderboard["top"] else "--"
    worst = leaderboard["worst"][0]["action"] if leaderboard["worst"] else "--"
    execution_rate = len(feedback) / max(len(timeline_actions), 1)
    ai_hit_rate = len(effective) / max(len(completed), 1)
    return {
        "execution_rate": min(1.0, execution_rate),
        "ai_hit_rate": ai_hit_rate if completed else None,
        "executed_actions": len(feedback),
        "measured_actions": len(completed),
        "best_decision": best,
        "worst_decision": worst,
        "score": round((min(1.0, execution_rate) * 0.45 + (ai_hit_rate if completed else 0) * 0.55) * 100),
    }


def _session_age(session: dict[str, Any]) -> float:
    value = session.get("age_seconds")
    if value is None:
        return 9999.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 9999.0


def _time_label(timestamp: Any) -> str:
    try:
        return time.strftime("%H:%M:%S", time.localtime(float(timestamp)))
    except Exception:
        return "--"


def _version_lt(current: str, latest: str) -> bool:
    if not current or current == "unknown" or current == "page_hook":
        return True
    return _version_tuple(current) < _version_tuple(latest)


def _version_tuple(value: str) -> tuple[int, ...]:
    numbers = re.findall(r"\d+", str(value or ""))
    return tuple(int(number) for number in numbers[:4]) or (0,)


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


def _apply_product_playbook(next_action: str, playbook: dict[str, Any], current_action: str) -> str:
    if not playbook:
        return next_action
    action_text = current_action.lower()
    if "switch" in action_text or "no valid" in action_text or "数据不完整" in current_action:
        return next_action
    sequence = playbook.get("sequence") if isinstance(playbook.get("sequence"), list) else []
    first_step = str(sequence[0]) if sequence else ""
    conversion_line = str(playbook.get("conversion_line") or "").strip()
    if "sizing" in action_text or "尺码" in current_action:
        return f"按商品打法先讲{first_step or '尺码'}：{conversion_line or next_action}"
    if "push" in action_text:
        return conversion_line or next_action
    if "continue" in action_text:
        flow = " → ".join(str(item) for item in sequence[:4] if item)
        return f"按商品打法讲：{flow}。{conversion_line or next_action}"
    return next_action


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
        and snapshot.look_uv_td_d_live <= 0
        and snapshot.look_uv_5min_d_live <= 0
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
