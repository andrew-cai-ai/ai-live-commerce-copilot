from __future__ import annotations

import re
from typing import Any

from app.services.live_memory import director_brief
from app.services.live_training_data import (
    ACTION_LIBRARY,
    extract_comment_topics,
    infer_product_dna,
    live_training_data,
)
import app.services.live_connector.constants as live_constants
from app.services.live_connector.types import LiveDecision, LiveMetricSnapshot, ProductEvent

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
    if "light push" in action_text or "有限实时数据" in current_action:
        return next_action
    if "push" in action_text:
        return conversion_line or next_action
    if "continue" in action_text:
        flow = " → ".join(str(item) for item in sequence[:4] if item)
        return f"按商品打法讲：{flow}。{conversion_line or next_action}"
    return next_action


def _script_variant_index(snapshot: LiveMetricSnapshot, action_code: str, variants: int) -> int:
    if variants <= 1:
        return 0
    seed = int(snapshot.timestamp // 25)
    seed += int(snapshot.pay_amt // 500) + int(snapshot.online_uv // 10)
    seed += sum(ord(char) for char in (snapshot.current_product or action_code)[:12])
    return seed % variants


def _playbook_sequence(playbook: dict[str, Any]) -> list[str]:
    sequence = playbook.get("sequence") if isinstance(playbook, dict) else []
    return [str(item).strip() for item in sequence if str(item).strip()] if isinstance(sequence, list) else []


def _playbook_line(playbook: dict[str, Any], key: str, fallback: str) -> str:
    value = str(playbook.get(key) or "").strip() if isinstance(playbook, dict) else ""
    return value or fallback


def _host_readable_sentence(
    next_action: str,
    action_code: str,
    current_action: str,
    snapshot: LiveMetricSnapshot,
    playbook: dict[str, Any],
    comment_clusters: dict[str, Any],
) -> str:
    """Return only words a host can read aloud; keep system reasoning in `reason`."""
    if action_code == "A007" and ("数据不完整" in current_action or "No valid" in current_action):
        return next_action

    product = snapshot.current_product or "这件"
    sequence = _playbook_sequence(playbook)
    opening = _playbook_line(playbook, "opening", f"宝子们先看{product}适不适合自己，适合再拍。")
    conversion_line = _playbook_line(playbook, "conversion_line", "合适的先锁，热门尺码等下不一定还有。")
    online = int(snapshot.online_uv or 0)
    has_sales = snapshot.pay_amt > 0 or snapshot.pay_amt_5min_d_live > 0 or snapshot.item_gmv > 0
    sizing_count = 0
    if isinstance(comment_clusters, dict):
        clusters = comment_clusters.get("clusters") if isinstance(comment_clusters.get("clusters"), dict) else {}
        sizing_count = int(clusters.get("尺码问题") or comment_clusters.get("尺码问题") or comment_clusters.get("sizing") or 0)

    if action_code == "A001" or sizing_count > 0:
        variants = [
            "宝子们先把身高体重打出来，我按正常穿、里面加内搭、想宽松三种给你们对尺码。",
            f"这件先别盲拍，尺码最重要。想贴身按平时码，里面要加一层就往大一码看。",
            "刚问尺码的我先集中回，报身高体重和想修身还是宽松，我直接给你对号。",
        ]
        return variants[_script_variant_index(snapshot, action_code, len(variants))]

    if action_code == "A002":
        variants = [
            "宝子们真假问题我直接给你看细节，镜头拉近看吊牌、洗标、拉链和走线。",
            "正品别听我空口说，细节给你们看清楚，吊牌洗标和做工都过一遍。",
            "担心真假的先别急，我把标、拉链和走线拉近给你们看，自己判断最踏实。",
        ]
        return variants[_script_variant_index(snapshot, action_code, len(variants))]

    if action_code == "A006":
        return "这件大家已经看得差不多了，我马上给你们上下一件更好抢的，想要这件的最后看一眼尺码。"

    if action_code == "A005":
        variants = [
            f"{conversion_line} 现在别纠结参数，先看颜色和尺码，合适的直接锁。",
            "这波不用听太多参数，重点看价格、颜色和尺码，合适就先拍，后面热门码不一定稳。",
            f"已经有人在拍了，{sequence[-1] if sequence else '热门码'}先确认一下，能穿的别等到断码。",
        ]
        return variants[_script_variant_index(snapshot, action_code, len(variants))]

    if action_code == "A004":
        variants = [
            f"{opening} 先别光看价格，看它是不是你能经常穿的场景。",
            f"这件值不值，重点看使用频率。{conversion_line}",
            "我不让你们盲拍，先看它能不能通勤、内搭或者日常反复穿，能穿上才值得。",
        ]
        return variants[_script_variant_index(snapshot, action_code, len(variants))]

    if action_code == "A008" or "light push" in current_action.lower():
        focus = sequence[_script_variant_index(snapshot, action_code, max(len(sequence), 1))] if sequence else "使用场景"
        variants = [
            f"{opening} {'现在还有' + str(online) + '人在看，' if online else ''}先看你适不适合这个场景。",
            f"这件先看{focus}，{conversion_line}",
            f"已经有人在看也有人在拍了，这件我先讲清楚{focus}，合适的再下单。",
            f"这件不拉长参数，直接讲大家最关心的：{(' → '.join(sequence[:3]) if sequence else '场景、尺码、价格')}。",
        ]
        if has_sales:
            variants.append(f"这件现在有成交，先别急着划走。我把{focus}讲清楚，能穿的再直接拍。")
        return variants[_script_variant_index(snapshot, action_code, len(variants))]

    if action_code == "A007":
        return "宝子们想看上身扣 1，想问尺码直接打身高体重，我先按评论区问题讲。"

    if "继续" in next_action or "continue" in current_action.lower():
        return f"{opening} 我按{(' → '.join(sequence[:3]) if sequence else '场景、尺码、价格')}给你们快速过一遍。"
    return next_action


def _director_model_state(snapshot: LiveMetricSnapshot, comment_clusters: dict[str, Any]) -> dict[str, Any]:
    dna = infer_product_dna(snapshot.current_product or "当前商品", {
        "price": snapshot.item_gmv or snapshot.pay_amt_5min_d_live or snapshot.pay_amt,
    })
    return {
        "heat": snapshot.heat_score,
        "ctr": snapshot.item_click_rate,
        "cvr": snapshot.item_conversion_rate,
        "gmv": snapshot.item_gmv,
        "comments": snapshot.comment_uv,
        "online_uv": snapshot.online_uv,
        "product_category": dna.get("category"),
        "product_tags": dna.get("tags") or [],
        "season": dna.get("season"),
        "price_band": dna.get("price_band"),
        "comment_topics": _model_comment_topics(snapshot, comment_clusters),
        "host_id": snapshot.host_id,
    }


def _model_comment_topics(snapshot: LiveMetricSnapshot, comment_clusters: dict[str, Any]) -> list[str]:
    topics = extract_comment_topics(snapshot.comment_text or "")
    if topics:
        return topics
    clustered = comment_clusters.get("clusters") if isinstance(comment_clusters, dict) else None
    if not isinstance(clustered, list):
        return []
    output = []
    for item in clustered:
        if not isinstance(item, dict):
            continue
        label = str(item.get("topic") or item.get("name") or "").strip()
        if label:
            output.append(label)
    return output[:5]


def _action_code_directive(action_code: str) -> tuple[str, str]:
    directives = {
        "A001": ("switch to sizing explanation", "直接回答尺码：身高体重、内搭、修身还是宽松，讲完马上引导下单。"),
        "A002": ("show authenticity proof", "镜头拉近展示吊牌、洗标、拉链和走线，先把真假顾虑打掉。"),
        "A003": ("show fit", "马上展示上身效果，讲版型、长度、肩宽和日常搭配。"),
        "A004": ("explain value", "别继续堆参数，开始讲价格优势、使用频率和为什么值。"),
        "A005": ("push harder", "少讲参数，强调库存、颜色尺码和现在下单的确定性。"),
        "A006": ("switch product", "这件收一下，马上切到下一件更容易成交的商品。"),
        "A007": ("engage comments", "让评论区扣 1 或报身高体重，把互动先拉起来。"),
        "A008": ("explain use case", "开始讲通勤、日常、户外场景，让用户知道买回去怎么穿。"),
    }
    if action_code not in directives and action_code in ACTION_LIBRARY:
        return (ACTION_LIBRARY[action_code]["name"], ACTION_LIBRARY[action_code]["name"])
    return directives.get(action_code, directives["A007"])


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
        "recommendation": "现在切品" if switch_now else "先不切，继续观察",
    }


def _comment_clusters(snapshot: LiveMetricSnapshot, comments: str) -> dict[str, Any]:
    counts = {
        "尺码问题": max(snapshot.sizing_comments, len(re.findall(r"尺码|穿啥|身高|体重|[1-2]\d{2}", comments))),
        "正品问题": max(snapshot.authenticity_comments, len(re.findall(r"真假|正品|吊牌|洗标|鉴定", comments))),
        "颜色库存": len(re.findall(r"黑色|白色|颜色|色差|有码", comments)),
        "价格问题": len(re.findall(r"价格|贵|便宜|划算|值不值|多少钱", comments)),
    }
    total = sum(counts.values()) or 1
    percentages = {key: round(value / total * 100) for key, value in counts.items()}
    ordered = sorted(percentages.items(), key=lambda item: item[1], reverse=True)
    action_map = {
        "尺码问题": "先讲尺码",
        "正品问题": "展示吊牌洗标",
        "颜色库存": "展示颜色库存",
        "价格问题": "解释价格价值",
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


def decide(
    snapshot: LiveMetricSnapshot,
    trend_30s: dict[str, str],
    trend_60s: dict[str, str],
    products: list[dict[str, Any]],
    missing_metrics: list[str],
) -> LiveDecision:
    reason: list[str] = []
    confidence = 0.64
    action_code = "A007"
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
        action_code = "A007"
        current_action = "数据不完整，等待 totalStats / 插件补齐"
        next_action = "先观察，不要根据缺失指标切品，等待 totalStats / 插件补齐。"
        reason = [f"missing: {metric}" for metric in missing_metrics[:3]]
        confidence = 0.0
        livestream_mode = "Waiting for complete live metrics"
    elif not valid_live_metrics:
        action_code = "A007"
        current_action = "No valid live metrics detected"
        next_action = "Please paste valid Taobao mtop payload or configure live connector."
        reason = ["online_uv=0", "heat_score=0", "pay_amt=0"]
        confidence = 0.0
        livestream_mode = "No valid live metrics"
    elif snapshot.sizing_comments > 0:
        action_code = "A001"
        current_action = "switch to sizing explanation"
        next_action = "集中讲尺码、身高体重和内搭建议"
        reason = ["comment keywords include size/尺码/身高体重", "sizing is blocking conversion", "answer sizing now"]
        confidence = 0.86
    elif snapshot.authenticity_comments > 0:
        action_code = "A002"
        current_action = "show authenticity proof"
        next_action = "展示吊牌、洗标、拉链和细节"
        reason = ["comment keywords include 真假/正品", "trust is blocking conversion", "show proof now"]
        confidence = 0.86
    elif snapshot.pay_amt > 0 and snapshot.online_uv > 0 and snapshot.heat_score <= 0:
        action_code = "A008"
        current_action = "continue with light push"
        next_action = "继续讲当前商品，轻推价格、尺码和使用场景。"
        reason = [
            f"页面可见成交额 ¥{int(snapshot.pay_amt)}",
            f"当前在线 {int(snapshot.online_uv)} 人",
            "当前为页面兜底数据，精细 CTR/CVR 待接口补齐",
        ]
        confidence = 0.58
        livestream_mode = "有限实时数据模式"
    elif snapshot.uv > 0 and snapshot.pv > 0 and snapshot.heat_score <= 0:
        action_code = "A004"
        current_action = "explain value"
        next_action = "讲价格价值和使用场景，先承接商品点击。"
        reason = [
            f"页面可见进房 {int(snapshot.uv)} 人",
            f"页面可见商品点击 {int(snapshot.pv)} 次",
            "当前为页面兜底数据，精细 CTR/CVR 待接口补齐",
        ]
        confidence = 0.58
        livestream_mode = "有限实时数据模式"
    elif trend_30s["online_uv"] == "up" and trend_30s["heat_score"] == "up" and trend_30s["pay_amt"] == "up":
        action_code = "A005"
        current_action = "continue product"
        next_action = "继续讲当前商品，别拉长解释，直接承接成交势能"
        reason = ["online_uv up", "heat_score up", "pay_amt up"]
        confidence = 0.86
    elif snapshot.pay_amt_5min_d_live <= 0 and trend_30s["online_uv"] == "down" and trend_30s["stay_time_pu"] == "down":
        action_code = "A006"
        current_action = "switch product"
        next_action = "切到下一件更容易成交的商品"
        reason = ["pay_amt_5min_d_live is 0", "online_uv is falling", "stay_time_pu is falling"]
        confidence = 0.88
    elif snapshot.heat_score > 600 and snapshot.ipv_uv_rate > 0.15:
        action_code = "A005"
        current_action = "push harder"
        next_action = "热度和点击都起来了，直接加速逼单"
        reason = ["heat_score > 600", "ipv_uv_rate > 15%", "traffic intent is strong"]
        confidence = 0.90
    elif snapshot.pay_amt_5min_d_live > 0 and snapshot.heat_score > 500:
        action_code = "A005"
        current_action = "push harder"
        next_action = "刚有成交，继续讲卖点并制造尺码紧迫感"
        reason = ["pay_amt_5min_d_live > 0", "heat_score > 500", "recent live sales confirmed"]
        confidence = 0.87
    elif snapshot.pay_byr_rate < 0.01 and snapshot.ipv_uv_rate > 0.15:
        action_code = "A004"
        current_action = "explain value"
        next_action = "解释价格、价值和使用场景"
        reason = ["pay_byr_rate < 1%", "ipv_uv_rate > 15%", "users are clicking but not paying"]
        confidence = 0.86
    elif recent_winners and recent_winners[0].payBuyerCnt >= 5:
        action_code = "A005"
        current_action = "continue product"
        next_action = "复盘刚成交的款式，顺势推荐同类商品"
        reason = ["recent product event has high payBuyerCnt", f"winner: {recent_winners[0].title}", "recommend similar product next"]
        recommended_next_product = _recommend_similar_product(recent_winners[0].title, products) or recommended_next_product
        confidence = 0.82
    elif trend_30s["online_uv"] == "down" and trend_30s["item_add_cart_rate"] == "down":
        action_code = "A006"
        current_action = "switch product"
        next_action = "切到下一件更容易成交的商品"
        reason = ["online_uv 30s down", "item_add_cart_rate 30s down", "traffic and cart intent are weakening"]
        confidence = 0.82
    elif trend_30s["item_click_rate"] == "up" and trend_30s["item_conversion_rate"] == "down":
        action_code = "A004"
        current_action = "explain value"
        next_action = "解释价格、价值和使用场景"
        reason = ["item_click_rate 30s up", "item_conversion_rate 30s down", "users are interested but not paying"]
        confidence = 0.80
    else:
        model_prediction = live_training_data.predict_director_model_v0(
            _director_model_state(snapshot, comment_clusters)
        )
        if model_prediction.get("status") == "predicted":
            model_confidence = float(model_prediction.get("confidence") or 0.0)
            if model_confidence >= live_constants.MODEL_V0_MIN_CONFIDENCE:
                action_code = str(model_prediction.get("action_code") or "A007")
                current_action, next_action = _action_code_directive(action_code)
                confidence = max(confidence, model_confidence)
                reason = [
                    f"model_v0 action: {action_code} {model_prediction.get('action_name') or ''}".strip(),
                    f"model confidence: {model_confidence:.2f}",
                    f"model accuracy: {model_prediction.get('model_accuracy')}",
                ]
            else:
                action_code = "A007"
                current_action, next_action = _action_code_directive(action_code)
                reason = [
                    f"model_v0 low confidence: {model_confidence:.2f} < {live_constants.MODEL_V0_MIN_CONFIDENCE:.2f}",
                    *_top_metric_reasons(snapshot, trend_30s, trend_60s)[:2],
                ]
        else:
            action_code = "A007"
            current_action, next_action = _action_code_directive(action_code)
            reason = _top_metric_reasons(snapshot, trend_30s, trend_60s)

    next_action = _apply_product_playbook(next_action, product_playbook, current_action)
    next_action = _host_readable_sentence(
        next_action,
        action_code,
        current_action,
        snapshot,
        product_playbook,
        comment_clusters,
    )

    decision_warnings: list[str] = []
    used_model = bool(reason) and str(reason[0]).startswith("model_v0")
    if valid_live_metrics and not missing_metrics and not used_model:
        model_hint = live_training_data.predict_director_model_v0(
            _director_model_state(snapshot, comment_clusters)
        )
        if model_hint.get("status") == "predicted":
            hint_code = str(model_hint.get("action_code") or "")
            hint_confidence = float(model_hint.get("confidence") or 0.0)
            if hint_code and hint_code != action_code and hint_confidence >= live_constants.MODEL_V0_MIN_CONFIDENCE:
                decision_warnings.append(
                    "model_v0 suggests "
                    f"{hint_code} ({model_hint.get('action_name') or ''}) "
                    f"with confidence {hint_confidence:.2f}; current rule decision is {action_code}"
                )

    return LiveDecision(
        valid_live_metrics=valid_live_metrics,
        current_live_score=current_live_score,
        action_code=action_code,
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
        warnings=decision_warnings,
    )
