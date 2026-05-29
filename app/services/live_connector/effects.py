from __future__ import annotations

import time
from typing import Any

from app.services.live_connector.parsing import _pick
from app.services.live_connector.types import LiveMetricSnapshot, LiveSessionState
from app.services.live_training_data import live_training_data

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
        "action_code": selected.get("action_code"),
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


