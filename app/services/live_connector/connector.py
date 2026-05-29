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
from app.services.live_training_data import _clean_action_code

load_dotenv()

from app.services.live_memory import live_memory
from app.services.live_connector.analytics import (
    _active_boss_intervention,
    _boss_risk_card,
    _boss_room_card,
    _host_feedback_stats,
    _post_live_summary,
    _session_display_name,
    _snapshot_summary,
    _version_lt,
)
from app.services.live_connector.constants import LATEST_EXTENSION_VERSION, _TREND_FIELDS
from app.services.live_connector.director import (
    _best_event_title,
    _count_authenticity_comments,
    _count_sizing_comments,
    decide,
    _has_product_level_metrics,
    _has_valid_live_metrics,
)
from app.services.live_connector.parsing import (
    _clean_host_id,
    _comment_text,
    _find_dict,
    _find_value,
    _host_id_from_payload,
    _missing_required_metrics,
    _mock_payload,
    _normalize_ingested_payload,
    _normalize_rate,
    _overlay_live_context,
    _parse_product_events,
    _pick,
    _to_number,
    _trend_direction,
    _unwrap_payload,
    _workspace_id_from_session_key,
)
from app.services.live_connector.effects import (
    _action_effects,
    _action_leaderboard,
    _director_score_card,
    _effect_metrics,
    _nearest_ai_decision,
    _product_context_for_feedback,
    _update_action_effects,
)
from app.services.live_connector.timeline import (
    _should_append_timeline_entry,
    _timeline_entry,
)
from app.services.live_connector.types import (
    LiveDecision,
    LiveMetricSnapshot,
    LiveSessionState,
    ProductEvent,
)

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
                "current_action_code": str(latest_action.get("action_code") or ""),
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
        action_code = _clean_action_code(payload.get("action_code"))
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
            "action_code": action_code,
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
        return decide(snapshot, trend_30s, trend_60s, products, missing_metrics)


