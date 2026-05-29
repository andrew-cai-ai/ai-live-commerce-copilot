from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = BASE_DIR / "data"
TRAINING_PATH = DATA_DIR / "live_director_training.jsonl"
GRAPH_PATH = DATA_DIR / "live_knowledge_graph.json"


ACTION_LIBRARY: dict[str, dict[str, Any]] = {
    "A001": {"name": "讲尺码", "keywords": ["尺码", "sizing", "身高", "体重", "175", "kg"]},
    "A002": {"name": "展示吊牌", "keywords": ["吊牌", "洗标", "正品", "真假", "authenticity"]},
    "A003": {"name": "展示上身", "keywords": ["上身", "试穿", "版型", "fit"]},
    "A004": {"name": "价格对比", "keywords": ["价格", "value", "贵", "值", "对比"]},
    "A005": {"name": "加速逼单", "keywords": ["逼单", "push", "库存", "下单", "锁"]},
    "A006": {"name": "切换商品", "keywords": ["切", "switch", "下一件", "过款"]},
    "A007": {"name": "评论互动", "keywords": ["评论", "扣1", "互动", "抽奖", "红包"]},
    "A008": {"name": "讲场景", "keywords": ["通勤", "场景", "日常", "户外"]},
}


class LiveTrainingDataService:
    def __init__(self, training_path: Path = TRAINING_PATH, graph_path: Path = GRAPH_PATH) -> None:
        self.training_path = training_path
        self.graph_path = graph_path
        self.graph = self._load_graph()

    def record_sample(
        self,
        host_id: str,
        product_name: str,
        ai_decision: dict[str, Any],
        host_action: dict[str, Any],
        before_metrics: dict[str, Any],
        after_metrics: dict[str, Any],
        delta: dict[str, Any],
        result: str,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        context = context or {}
        action_code = classify_action(host_action.get("action_label") or host_action.get("decision") or "")
        product_dna = infer_product_dna(product_name, context)
        sample = {
            "schema_version": "director_training_v1",
            "timestamp": time.time(),
            "host_id": host_id or "default",
            "product_name": product_name or "当前商品",
            "product_dna": product_dna,
            "action_code": action_code,
            "action_name": ACTION_LIBRARY[action_code]["name"],
            "ai_decision": ai_decision,
            "host_execution": {
                "action": host_action.get("action_label") or host_action.get("decision") or "",
                "sentence": host_action.get("next_action") or "",
                "product_position": context.get("product_position"),
                "product_elapsed_seconds": context.get("product_elapsed_seconds"),
            },
            "before_metrics": before_metrics,
            "after_metrics": after_metrics,
            "result_delta": delta,
            "result": result,
            "traffic_source": context.get("traffic_source") or "unknown",
            "comment_topics": extract_comment_topics(context.get("comments") or ""),
        }
        self._append_jsonl(sample)
        self._update_graph(sample)
        return sample

    def action_library_summary(self) -> dict[str, Any]:
        actions = self.graph.get("actions", {})
        rows = []
        for code, meta in ACTION_LIBRARY.items():
            stats = actions.get(code, {})
            count = max(int(stats.get("count") or 0), 1)
            rows.append({
                "code": code,
                "name": meta["name"],
                "samples": int(stats.get("count") or 0),
                "avg_ctr_delta": float(stats.get("ctr_delta") or 0) / count,
                "avg_cvr_delta": float(stats.get("cvr_delta") or 0) / count,
                "avg_gmv_delta": float(stats.get("gmv_delta") or 0) / count,
                "effective_rate": float(stats.get("effective_count") or 0) / count,
            })
        return {"actions": rows}

    def _append_jsonl(self, sample: dict[str, Any]) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with self.training_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(sample, ensure_ascii=False) + "\n")

    def _update_graph(self, sample: dict[str, Any]) -> None:
        graph = self.graph
        graph["updated_at"] = time.time()
        action_code = sample["action_code"]
        product_key = _key(sample["product_name"])
        host_key = _key(sample["host_id"])
        score = _sample_score(sample.get("result_delta") or {})
        cvr = _to_float((sample.get("after_metrics") or {}).get("cvr"))

        product = graph.setdefault("products", {}).setdefault(product_key, {
            "name": sample["product_name"],
            "tags": sample["product_dna"]["tags"],
            "category": sample["product_dna"]["category"],
            "samples": 0,
            "actions": {},
            "hosts": {},
            "comment_topics": {},
        })
        host = graph.setdefault("hosts", {}).setdefault(host_key, {"name": sample["host_id"], "samples": 0, "actions": {}, "products": {}})
        action = graph.setdefault("actions", {}).setdefault(action_code, {"name": sample["action_name"], "count": 0})

        for node in (product, host):
            node["samples"] = int(node.get("samples") or 0) + 1
            node["last_seen_at"] = sample["timestamp"]
            _rollup_action(node.setdefault("actions", {}), action_code, sample["action_name"], sample, score, cvr)

        _rollup_relation(product.setdefault("hosts", {}), host_key, sample["host_id"], score, cvr)
        _rollup_relation(host.setdefault("products", {}), product_key, sample["product_name"], score, cvr)
        _rollup_action(graph.setdefault("actions", {}), action_code, sample["action_name"], sample, score, cvr)

        for topic in sample.get("comment_topics") or []:
            topics = product.setdefault("comment_topics", {})
            topics[topic] = int(topics.get(topic) or 0) + 1

        self._save_graph()

    def _load_graph(self) -> dict[str, Any]:
        if not self.graph_path.exists():
            return {"products": {}, "hosts": {}, "actions": {}, "updated_at": 0}
        try:
            data = json.loads(self.graph_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data.setdefault("products", {})
                data.setdefault("hosts", {})
                data.setdefault("actions", {})
                return data
        except Exception:
            pass
        return {"products": {}, "hosts": {}, "actions": {}, "updated_at": 0}

    def _save_graph(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.graph_path.write_text(json.dumps(self.graph, ensure_ascii=False, indent=2), encoding="utf-8")


def classify_action(action_text: str) -> str:
    text = str(action_text or "").lower()
    best_code = "A007"
    best_score = -1
    for code, meta in ACTION_LIBRARY.items():
        score = sum(1 for keyword in meta["keywords"] if str(keyword).lower() in text)
        if score > best_score:
            best_code = code
            best_score = score
    return best_code


def infer_product_dna(product_name: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
    context = context or {}
    text = str(product_name or "").lower()
    category = "通用商品"
    tags: list[str] = []
    season = "四季"
    if re.search(r"pant|pants|裤", text):
        category = "裤装"
        tags.extend(["通勤", "版型", "尺码"])
    if re.search(r"shirt|tee|t-shirt|ss|ls|短袖|长袖", text):
        category = "上衣/T恤"
        tags.extend(["日常", "价格敏感", "尺码"])
        season = "春夏"
    if re.search(r"rho|fleece|抓绒|保暖", text):
        category = "保暖内搭"
        tags.extend(["保暖", "叠穿", "秋冬"])
        season = "秋冬"
    if re.search(r"atom|cerium|down|羽绒", text):
        category = "保暖外套"
        tags.extend(["保暖", "轻量", "通勤"])
        season = "秋冬"
    if re.search(r"alpha|beta|shell|sv|硬壳", text):
        category = "硬壳/防护"
        tags.extend(["防护", "雨雪", "专业"])
    if re.search(r"shoe|gtx|norvan|kragg shoe|鞋", text):
        category = "鞋类"
        tags.extend(["脚型", "尺码", "场景"])
    price = _to_float(context.get("target_price") or context.get("price"))
    price_band = "unknown"
    if price:
        price_band = "low" if price < 600 else "mid" if price < 1500 else "high"
    return {
        "category": category,
        "tags": sorted(set(tags or ["价格价值", "尺码", "场景"])),
        "season": season,
        "price_band": price_band,
        "persona": _persona_for(category),
        "common_questions": _common_questions_for(category),
        "recommended_strategy": _strategy_for(category),
    }


def extract_comment_topics(comments: str) -> list[str]:
    text = str(comments or "")
    topics = []
    if re.search(r"尺码|穿啥|身高|体重|[1-2]\d{2}", text):
        topics.append("尺码")
    if re.search(r"真假|正品|吊牌|洗标", text):
        topics.append("正品")
    if re.search(r"价格|贵|值|多少钱|划算", text):
        topics.append("价格")
    if re.search(r"黑色|白色|颜色|色差", text):
        topics.append("颜色")
    if re.search(r"暖|冷|温度|冬天", text):
        topics.append("保暖")
    return topics


def _rollup_action(bucket: dict[str, Any], code: str, name: str, sample: dict[str, Any], score: float, cvr: float) -> None:
    item = bucket.setdefault(code, {"name": name, "count": 0, "score_total": 0.0, "effective_count": 0, "ctr_delta": 0.0, "cvr_delta": 0.0, "gmv_delta": 0.0, "cvr_total": 0.0})
    delta = sample.get("result_delta") or {}
    item["count"] = int(item.get("count") or 0) + 1
    item["score_total"] = float(item.get("score_total") or 0) + score
    item["effective_count"] = int(item.get("effective_count") or 0) + (1 if sample.get("result") == "有效" else 0)
    item["ctr_delta"] = float(item.get("ctr_delta") or 0) + _to_float(delta.get("ctr"))
    item["cvr_delta"] = float(item.get("cvr_delta") or 0) + _to_float(delta.get("cvr"))
    item["gmv_delta"] = float(item.get("gmv_delta") or 0) + _to_float(delta.get("gmv"))
    item["cvr_total"] = float(item.get("cvr_total") or 0) + cvr


def _rollup_relation(bucket: dict[str, Any], key: str, name: str, score: float, cvr: float) -> None:
    item = bucket.setdefault(key, {"name": name, "count": 0, "score_total": 0.0, "cvr_total": 0.0})
    item["count"] = int(item.get("count") or 0) + 1
    item["score_total"] = float(item.get("score_total") or 0) + score
    item["cvr_total"] = float(item.get("cvr_total") or 0) + cvr


def _sample_score(delta: dict[str, Any]) -> float:
    return _to_float(delta.get("gmv")) / 100 + _to_float(delta.get("cvr")) * 1000 + _to_float(delta.get("ctr")) * 350 + _to_float(delta.get("heat")) / 20


def _persona_for(category: str) -> str:
    if category == "鞋类":
        return "走路多、在意脚型和尺码准确性的客户"
    if category in {"硬壳/防护", "保暖外套"}:
        return "愿意为功能性和品牌定位付费的客户"
    if category == "上衣/T恤":
        return "想低门槛入手始祖鸟、重视日常穿着的客户"
    return "既看价格也看日常使用率的客户"


def _common_questions_for(category: str) -> list[str]:
    if category == "鞋类":
        return ["尺码偏大偏小", "脚型适不适合", "有没有防水"]
    if category == "保暖外套":
        return ["冬天够不够暖", "尺码怎么选", "值不值这个价格"]
    if category == "硬壳/防护":
        return ["是不是正品", "适合通勤吗", "和 Beta/Alpha 怎么选"]
    return ["尺码怎么选", "颜色还有吗", "值不值得买"]


def _strategy_for(category: str) -> list[str]:
    if category == "鞋类":
        return ["尺码脚型", "场景", "价格"]
    if category == "上衣/T恤":
        return ["尺码", "面料", "价格"]
    if category == "硬壳/防护":
        return ["天气防护", "正品细节", "价格锚点"]
    return ["适合谁", "尺码/场景", "价格价值"]


def _key(value: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fa5_.:-]+", "-", str(value or "unknown").lower())[:120] or "unknown"


def _to_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


live_training_data = LiveTrainingDataService()
