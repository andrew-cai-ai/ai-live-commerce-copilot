from __future__ import annotations

import re
import time
from typing import Any

from app.services.live_connector.constants import LATEST_EXTENSION_VERSION
from app.services.live_connector.parsing import _clean_host_id, _clean_workspace_id, _pick
from app.services.live_connector.types import LiveMetricSnapshot, LiveSessionState

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
        "current_action_code": session.get("current_action_code") or "--",
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
