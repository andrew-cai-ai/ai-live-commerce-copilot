from __future__ import annotations

from typing import Any

from app.services.live_connector.constants import _TIMELINE_TREND_FIELDS
from app.services.live_connector.types import LiveDecision

def _timeline_entry(decision: LiveDecision) -> dict[str, Any]:
    trend_signature = _timeline_trend_signature(decision.trend_30s, decision.trend_60s)
    return {
        "timestamp": decision.snapshot.timestamp,
        "last_seen": decision.snapshot.timestamp,
        "action_code": decision.action_code,
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

