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
MODEL_V0_PATH = DATA_DIR / "director_model_v0.json"


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
    def __init__(
        self,
        training_path: Path = TRAINING_PATH,
        graph_path: Path = GRAPH_PATH,
        model_path: Path = MODEL_V0_PATH,
    ) -> None:
        self.training_path = training_path
        self.graph_path = graph_path
        self.model_path = model_path
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
        action_code = _clean_action_code(host_action.get("action_code")) or classify_action(host_action.get("action_label") or host_action.get("decision") or "")
        product_dna = infer_product_dna(product_name, context)
        quality = score_sample_quality(product_name, action_code, ai_decision, host_action, before_metrics, after_metrics, delta, context)
        reward = director_reward(delta, quality["score"])
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
                "action_code": action_code,
                "action": host_action.get("action_label") or host_action.get("decision") or "",
                "sentence": host_action.get("next_action") or "",
                "product_position": context.get("product_position"),
                "product_elapsed_seconds": context.get("product_elapsed_seconds"),
            },
            "before_metrics": before_metrics,
            "after_metrics": after_metrics,
            "result_delta": delta,
            "reward": reward,
            "result": result,
            "sample_quality_score": quality["score"],
            "sample_quality_reasons": quality["reasons"],
            "use_for_training": quality["score"] >= 70,
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
                "elo": round(float(stats.get("elo") or 1500)),
                "samples": int(stats.get("count") or 0),
                "training_samples": int(stats.get("training_count") or 0),
                "avg_ctr_delta": float(stats.get("ctr_delta") or 0) / count,
                "avg_cvr_delta": float(stats.get("cvr_delta") or 0) / count,
                "avg_gmv_delta": float(stats.get("gmv_delta") or 0) / count,
                "effective_rate": float(stats.get("effective_count") or 0) / count,
            })
        return {"actions": rows}

    def director_replay(self, host_id: str | None = None, limit: int = 80) -> list[dict[str, Any]]:
        rows = self._read_recent_samples(limit=limit * 3)
        if host_id:
            rows = [row for row in rows if row.get("host_id") == host_id]
        return [_replay_event(row) for row in rows[-limit:]]

    def best_practices(self, product_name: str | None = None) -> dict[str, Any]:
        products = self.graph.get("products", {})
        if product_name:
            product = products.get(_key(product_name), {})
            return {"product_name": product_name, "best_practices": _best_practice_from_product(product)}
        return {
            "products": [
                {
                    "product_name": product.get("name") or key,
                    "best_practices": _best_practice_from_product(product),
                }
                for key, product in products.items()
            ]
        }

    def training_dataset_v1(self, min_quality: int = 70, limit: int = 5000) -> list[dict[str, Any]]:
        rows = self._read_recent_samples(limit=limit)
        return [
            {
                "state": _training_state(row),
                "action": row.get("action_code"),
                "reward": row.get("reward"),
                "quality": row.get("sample_quality_score"),
                "metadata": {
                    "host_id": row.get("host_id"),
                    "product_name": row.get("product_name"),
                    "timestamp": row.get("timestamp"),
                },
            }
            for row in rows
            if int(row.get("sample_quality_score") or 0) >= min_quality and row.get("use_for_training")
        ]

    def training_data_dashboard(self) -> dict[str, Any]:
        rows = self._read_recent_samples(limit=100000)
        high_quality = [row for row in rows if int(row.get("sample_quality_score") or 0) >= 70]
        training = [row for row in rows if row.get("use_for_training")]
        rejected = [row for row in rows if not row.get("use_for_training")]
        return {
            "total_samples": len(rows),
            "high_quality_samples": len(high_quality),
            "training_samples": len(training),
            "rejected_samples": len(rejected),
            "by_product": _group_sample_counts(rows, "product_name"),
            "by_host": _group_sample_counts(rows, "host_id"),
            "by_action": _group_sample_counts(rows, "action_code", action_names=True),
            "avg_quality": round(sum(int(row.get("sample_quality_score") or 0) for row in rows) / max(len(rows), 1), 1),
        }

    def coverage_dashboard(self, min_product_samples: int = 30, min_host_samples: int = 30) -> dict[str, Any]:
        graph = self.graph
        products = [
            _coverage_row(key, item, min_product_samples)
            for key, item in graph.get("products", {}).items()
        ]
        hosts = [
            _coverage_row(key, item, min_host_samples)
            for key, item in graph.get("hosts", {}).items()
        ]
        products.sort(key=lambda item: (item["sample_gap"], item["samples"]), reverse=True)
        hosts.sort(key=lambda item: (item["sample_gap"], item["samples"]), reverse=True)
        return {
            "product_threshold": min_product_samples,
            "host_threshold": min_host_samples,
            "products_needing_samples": [item for item in products if item["sample_gap"] > 0],
            "hosts_needing_samples": [item for item in hosts if item["sample_gap"] > 0],
            "product_coverage_rate": _coverage_rate(products),
            "host_coverage_rate": _coverage_rate(hosts),
        }

    def cold_start_recommendation(self, product_name: str, top_k: int = 3) -> dict[str, Any]:
        target_dna = infer_product_dna(product_name)
        candidates = []
        for key, product in self.graph.get("products", {}).items():
            dna = {
                "category": product.get("category"),
                "tags": product.get("tags") or [],
            }
            score = _dna_similarity(target_dna, dna)
            if score <= 0:
                continue
            candidates.append({
                "product_key": key,
                "product_name": product.get("name") or key,
                "similarity": round(score, 3),
                "samples": int(product.get("samples") or 0),
                "best_practices": _best_practice_from_product(product),
            })
        candidates.sort(key=lambda item: (item["similarity"], item["samples"]), reverse=True)
        inherited = candidates[0]["best_practices"] if candidates else _cold_start_default(target_dna)
        return {
            "product_name": product_name,
            "product_dna": target_dna,
            "similar_products": candidates[:top_k],
            "inherited_playbook": inherited,
            "cold_start_confidence": round(candidates[0]["similarity"], 3) if candidates else 0.35,
        }

    def offline_evaluation(self, min_quality: int = 70) -> dict[str, Any]:
        rows = [
            row for row in self._read_recent_samples(limit=100000)
            if int(row.get("sample_quality_score") or 0) >= min_quality
        ]
        evaluated = []
        for row in rows:
            best = _best_action_for_sample(row, self.graph)
            ai_action = classify_action(" ".join(str(item) for item in [
                (row.get("ai_decision") or {}).get("decision"),
                (row.get("ai_decision") or {}).get("next_action"),
            ]))
            host_action = row.get("action_code")
            evaluated.append({
                "ai_match": ai_action == best,
                "host_match": host_action == best,
                "best_action": best,
                "ai_action": ai_action,
                "host_action": host_action,
            })
        total = len(evaluated)
        return {
            "evaluated_samples": total,
            "director_accuracy": round(sum(1 for item in evaluated if item["ai_match"]) / max(total, 1), 4),
            "host_execution_best_action_rate": round(sum(1 for item in evaluated if item["host_match"]) / max(total, 1), 4),
            "by_best_action": _evaluation_by_action(evaluated),
        }

    def model_readiness_summary(
        self,
        product_threshold: int = 30,
        host_threshold: int = 30,
        min_quality: int = 70,
    ) -> dict[str, Any]:
        return {
            "dashboard": self.training_data_dashboard(),
            "coverage": self.coverage_dashboard(product_threshold, host_threshold),
            "evaluation": self.offline_evaluation(min_quality=min_quality),
            "artifact": self.model_artifact_status(),
            "action_library": self.action_library_summary(),
        }

    def model_artifact_status(self) -> dict[str, Any]:
        if not self.model_path.exists():
            return {
                "status": "missing",
                "model_path": str(self.model_path),
                "accuracy": None,
                "training_samples": 0,
            }
        try:
            artifact = json.loads(self.model_path.read_text(encoding="utf-8"))
        except Exception as exc:
            return {
                "status": "invalid",
                "model_path": str(self.model_path),
                "error": str(exc),
            }
        if not isinstance(artifact, dict):
            return {"status": "invalid", "model_path": str(self.model_path)}
        return {
            "status": "ready",
            "model_path": str(self.model_path),
            "schema_version": artifact.get("schema_version"),
            "accuracy": artifact.get("accuracy"),
            "training_samples": artifact.get("training_samples"),
            "test_samples": artifact.get("test_samples"),
            "created_at": artifact.get("created_at"),
            "action_distribution": (artifact.get("model") or {}).get("action_counts", {}),
        }

    def train_director_model_v0(self, min_quality: int = 70) -> dict[str, Any]:
        dataset = self.training_dataset_v1(min_quality=min_quality, limit=100000)
        dataset = [item for item in dataset if item.get("action")]
        if len(dataset) < 5:
            return {
                "status": "not_enough_data",
                "training_samples": len(dataset),
                "min_required": 5,
                "accuracy": None,
                "model_path": str(self.model_path),
            }
        split = max(1, int(len(dataset) * 0.8))
        train_rows = dataset[:split]
        test_rows = dataset[split:] or dataset[-1:]
        model = _fit_director_model(train_rows)
        predictions = [_predict_director_action(model, row["state"]) for row in test_rows]
        correct = sum(1 for prediction, row in zip(predictions, test_rows) if prediction == row["action"])
        accuracy = correct / max(len(test_rows), 1)
        artifact = {
            "schema_version": "director_model_v0",
            "created_at": time.time(),
            "min_quality": min_quality,
            "training_samples": len(train_rows),
            "test_samples": len(test_rows),
            "accuracy": round(accuracy, 4),
            "model_type": "feature_weighted_action_frequency",
            "model": model,
        }
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.model_path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
        return {
            "status": "trained",
            "training_samples": len(train_rows),
            "test_samples": len(test_rows),
            "accuracy": round(accuracy, 4),
            "model_path": str(self.model_path),
            "action_distribution": model["action_counts"],
        }

    def predict_director_model_v0(self, state: dict[str, Any]) -> dict[str, Any]:
        if not self.model_path.exists():
            return {"status": "missing_model", "action_code": "", "confidence": 0.0}
        try:
            artifact = json.loads(self.model_path.read_text(encoding="utf-8"))
        except Exception as exc:
            return {"status": "invalid_model", "action_code": "", "confidence": 0.0, "error": str(exc)}
        model = artifact.get("model") if isinstance(artifact, dict) else None
        if not isinstance(model, dict):
            return {"status": "invalid_model", "action_code": "", "confidence": 0.0}
        action_code, scores = _predict_director_action_with_scores(model, state)
        action_code = _clean_action_code(action_code)
        if not action_code:
            return {"status": "invalid_action", "action_code": "", "confidence": 0.0}
        confidence = _prediction_confidence(scores, action_code)
        return {
            "status": "predicted",
            "action_code": action_code,
            "action_name": ACTION_LIBRARY.get(action_code, {}).get("name", action_code),
            "confidence": confidence,
            "model_accuracy": artifact.get("accuracy"),
            "training_samples": artifact.get("training_samples"),
        }

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
        _update_action_elo(action, sample)

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

    def _read_recent_samples(self, limit: int = 500) -> list[dict[str, Any]]:
        if not self.training_path.exists():
            return []
        lines = self.training_path.read_text(encoding="utf-8").splitlines()
        rows = []
        for line in lines[-limit:]:
            try:
                item = json.loads(line)
            except Exception:
                continue
            if isinstance(item, dict):
                rows.append(item)
        return rows


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


def score_sample_quality(
    product_name: str,
    action_code: str,
    ai_decision: dict[str, Any],
    host_action: dict[str, Any],
    before_metrics: dict[str, Any],
    after_metrics: dict[str, Any],
    delta: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    score = 100
    reasons: list[str] = []
    before_time = _to_float(before_metrics.get("timestamp"))
    after_time = _to_float(after_metrics.get("timestamp"))
    window = after_time - before_time if before_time and after_time else 0
    if not before_metrics or not after_metrics:
        score -= 35
        reasons.append("missing_before_or_after_metrics")
    if window and (window < 10 or window > 180):
        score -= 15
        reasons.append("effect_window_out_of_range")
    if not product_name or product_name == "当前商品":
        score -= 12
        reasons.append("missing_product_name")
    action_text = str(host_action.get("action_label") or host_action.get("decision") or "")
    ai_text = " ".join(str(item) for item in [
        ai_decision.get("decision"),
        ai_decision.get("next_action"),
        " ".join(str(reason) for reason in (ai_decision.get("reason") or [])),
    ])
    ai_action_code = _clean_action_code(ai_decision.get("action_code")) or (classify_action(ai_text) if ai_text else "")
    if ai_action_code and ai_action_code != action_code:
        score -= 20
        reasons.append("host_action_differs_from_ai_recommendation")
    if _to_float(context.get("product_elapsed_seconds")) <= 0:
        score -= 8
        reasons.append("missing_product_elapsed_time")
    if abs(_to_float(delta.get("gmv"))) < 1 and abs(_to_float(delta.get("cvr"))) < 0.0001 and abs(_to_float(delta.get("ctr"))) < 0.0001:
        score -= 18
        reasons.append("no_measurable_metric_change")
    if context.get("product_switched_during_window"):
        score -= 30
        reasons.append("product_switched_during_effect_window")
    return {"score": max(0, min(100, round(score))), "reasons": reasons or ["clean_sample"]}


def director_reward(delta: dict[str, Any], quality_score: int) -> float:
    reward = _sample_score(delta)
    return round(reward * max(0.1, quality_score / 100), 4)


def _update_action_elo(action: dict[str, Any], sample: dict[str, Any]) -> None:
    elo = float(action.get("elo") or 1500)
    reward = _to_float(sample.get("reward"))
    quality = _to_float(sample.get("sample_quality_score"))
    expected = 1 / (1 + 10 ** ((1500 - elo) / 400))
    actual = 1.0 if reward > 1 else 0.5 if reward >= -1 else 0.0
    k = 24 * max(0.25, quality / 100)
    action["elo"] = round(elo + k * (actual - expected), 2)


def _replay_event(sample: dict[str, Any]) -> dict[str, Any]:
    delta = sample.get("result_delta") or {}
    ai = sample.get("ai_decision") or {}
    host = sample.get("host_execution") or {}
    return {
        "timestamp": sample.get("timestamp"),
        "product_name": sample.get("product_name"),
        "ai_action": ai.get("decision") or ai.get("next_action") or "--",
        "host_action": host.get("action") or "--",
        "action_code": sample.get("action_code"),
        "result": sample.get("result"),
        "quality": sample.get("sample_quality_score"),
        "use_for_training": sample.get("use_for_training"),
        "delta_summary": {
            "ctr": _to_float(delta.get("ctr")),
            "cvr": _to_float(delta.get("cvr")),
            "gmv": _to_float(delta.get("gmv")),
            "heat": _to_float(delta.get("heat")),
        },
    }


def _best_practice_from_product(product: dict[str, Any]) -> dict[str, Any]:
    actions = product.get("actions") or {}
    ranked = sorted(
        [
            {
                "code": code,
                "name": stats.get("name") or ACTION_LIBRARY.get(code, {}).get("name") or code,
                "count": int(stats.get("count") or 0),
                "avg_score": _to_float(stats.get("score_total")) / max(int(stats.get("count") or 0), 1),
                "avg_cvr_delta": _to_float(stats.get("cvr_delta")) / max(int(stats.get("count") or 0), 1),
                "avg_gmv_delta": _to_float(stats.get("gmv_delta")) / max(int(stats.get("count") or 0), 1),
            }
            for code, stats in actions.items()
        ],
        key=lambda item: item["avg_score"],
        reverse=True,
    )
    return {
        "recommended_sequence": [item["name"] for item in ranked[:3]],
        "top_actions": ranked[:5],
        "sample_count": int(product.get("samples") or 0),
        "category": product.get("category") or "--",
        "comment_topics": sorted((product.get("comment_topics") or {}).items(), key=lambda item: item[1], reverse=True)[:5],
    }


def _training_state(sample: dict[str, Any]) -> dict[str, Any]:
    before = sample.get("before_metrics") or {}
    dna = sample.get("product_dna") or {}
    return {
        "heat": before.get("heat"),
        "ctr": before.get("ctr"),
        "cvr": before.get("cvr"),
        "gmv": before.get("gmv"),
        "comments": before.get("comments"),
        "online_uv": before.get("online_uv"),
        "product_category": dna.get("category"),
        "product_tags": dna.get("tags") or [],
        "season": dna.get("season"),
        "price_band": dna.get("price_band"),
        "comment_topics": sample.get("comment_topics") or [],
        "host_id": sample.get("host_id"),
    }


def _group_sample_counts(rows: list[dict[str, Any]], field: str, action_names: bool = False) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = str(row.get(field) or "unknown")
        item = grouped.setdefault(key, {"key": key, "samples": 0, "training_samples": 0, "quality_total": 0})
        item["samples"] += 1
        item["training_samples"] += 1 if row.get("use_for_training") else 0
        item["quality_total"] += int(row.get("sample_quality_score") or 0)
    output = []
    for item in grouped.values():
        samples = max(int(item["samples"]), 1)
        output.append({
            "key": item["key"],
            "name": ACTION_LIBRARY.get(item["key"], {}).get("name", item["key"]) if action_names else item["key"],
            "samples": item["samples"],
            "training_samples": item["training_samples"],
            "avg_quality": round(item["quality_total"] / samples, 1),
        })
    return sorted(output, key=lambda item: item["samples"], reverse=True)


def _coverage_row(key: str, item: dict[str, Any], threshold: int) -> dict[str, Any]:
    samples = int(item.get("samples") or 0)
    return {
        "key": key,
        "name": item.get("name") or key,
        "samples": samples,
        "sample_gap": max(0, threshold - samples),
        "coverage": min(1.0, samples / max(threshold, 1)),
    }


def _coverage_rate(rows: list[dict[str, Any]]) -> float:
    if not rows:
        return 0.0
    return round(sum(1 for item in rows if item["sample_gap"] <= 0) / len(rows), 4)


def _dna_similarity(left: dict[str, Any], right: dict[str, Any]) -> float:
    score = 0.0
    if left.get("category") and left.get("category") == right.get("category"):
        score += 0.45
    left_tags = set(left.get("tags") or [])
    right_tags = set(right.get("tags") or [])
    if left_tags or right_tags:
        score += 0.35 * (len(left_tags & right_tags) / max(len(left_tags | right_tags), 1))
    if left.get("season") == right.get("season"):
        score += 0.10
    if left.get("price_band") == right.get("price_band"):
        score += 0.10
    return score


def _cold_start_default(dna: dict[str, Any]) -> dict[str, Any]:
    return {
        "recommended_sequence": dna.get("recommended_strategy") or ["适合谁", "尺码/场景", "价格价值"],
        "top_actions": [],
        "sample_count": 0,
        "category": dna.get("category") or "--",
        "comment_topics": [],
    }


def _best_action_for_sample(sample: dict[str, Any], graph: dict[str, Any]) -> str:
    product_key = _key(str(sample.get("product_name") or ""))
    product = graph.get("products", {}).get(product_key, {})
    actions = product.get("actions") or graph.get("actions") or {}
    if not actions:
        return str(sample.get("action_code") or "A007")
    best_code = str(sample.get("action_code") or "A007")
    best_score = None
    for code, stats in actions.items():
        count = max(int(stats.get("count") or 0), 1)
        score = _to_float(stats.get("score_total")) / count
        if best_score is None or score > best_score:
            best_code = code
            best_score = score
    return best_code


def _evaluation_by_action(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, int]] = {}
    for row in rows:
        key = str(row.get("best_action") or "unknown")
        item = grouped.setdefault(key, {"total": 0, "ai_correct": 0, "host_correct": 0})
        item["total"] += 1
        item["ai_correct"] += 1 if row.get("ai_match") else 0
        item["host_correct"] += 1 if row.get("host_match") else 0
    return [
        {
            "action": key,
            "name": ACTION_LIBRARY.get(key, {}).get("name", key),
            "samples": item["total"],
            "director_accuracy": round(item["ai_correct"] / max(item["total"], 1), 4),
            "host_match_rate": round(item["host_correct"] / max(item["total"], 1), 4),
        }
        for key, item in sorted(grouped.items(), key=lambda pair: pair[1]["total"], reverse=True)
    ]


def _fit_director_model(rows: list[dict[str, Any]]) -> dict[str, Any]:
    action_counts: dict[str, int] = {}
    feature_counts: dict[str, dict[str, int]] = {}
    for row in rows:
        action = str(row.get("action") or "A007")
        action_counts[action] = action_counts.get(action, 0) + 1
        for feature in _state_features(row.get("state") or {}):
            bucket = feature_counts.setdefault(feature, {})
            bucket[action] = bucket.get(action, 0) + 1
    return {
        "action_counts": action_counts,
        "feature_counts": feature_counts,
        "actions": sorted(action_counts),
    }


def _predict_director_action(model: dict[str, Any], state: dict[str, Any]) -> str:
    action, _scores = _predict_director_action_with_scores(model, state)
    return action


def _predict_director_action_with_scores(model: dict[str, Any], state: dict[str, Any]) -> tuple[str, dict[str, float]]:
    actions = model.get("actions") or list(ACTION_LIBRARY)
    action_counts = model.get("action_counts") or {}
    total = sum(int(value) for value in action_counts.values()) or 1
    scores = {}
    for action in actions:
        scores[action] = (int(action_counts.get(action) or 0) + 1) / (total + len(actions))
    for feature in _state_features(state):
        bucket = (model.get("feature_counts") or {}).get(feature, {})
        bucket_total = sum(int(value) for value in bucket.values()) or 0
        for action in actions:
            scores[action] *= (int(bucket.get(action) or 0) + 1) / (bucket_total + len(actions))
    action = max(scores.items(), key=lambda item: item[1])[0] if scores else "A007"
    return action, scores


def _prediction_confidence(scores: dict[str, float], action_code: str) -> float:
    if not scores or not action_code:
        return 0.0
    total = sum(float(value) for value in scores.values()) or 1
    return round(float(scores.get(action_code) or 0) / total, 4)


def _clean_action_code(value: Any) -> str:
    code = str(value or "").strip().upper()
    return code if code in ACTION_LIBRARY else ""


def _state_features(state: dict[str, Any]) -> list[str]:
    features = [
        f"category={state.get('product_category') or 'unknown'}",
        f"season={state.get('season') or 'unknown'}",
        f"price_band={state.get('price_band') or 'unknown'}",
        f"ctr={_metric_bin(state.get('ctr'), [0.03, 0.08, 0.15])}",
        f"cvr={_metric_bin(state.get('cvr'), [0.01, 0.02, 0.04])}",
        f"heat={_metric_bin(state.get('heat'), [200, 500, 800])}",
        f"online={_metric_bin(state.get('online_uv'), [20, 100, 500])}",
    ]
    features.extend(f"tag={tag}" for tag in (state.get("product_tags") or [])[:5])
    features.extend(f"topic={topic}" for topic in (state.get("comment_topics") or [])[:5])
    return features


def _metric_bin(value: Any, thresholds: list[float]) -> str:
    number = _to_float(value)
    if number <= thresholds[0]:
        return "low"
    if number <= thresholds[1]:
        return "mid"
    if number <= thresholds[2]:
        return "high"
    return "very_high"


def _rollup_action(bucket: dict[str, Any], code: str, name: str, sample: dict[str, Any], score: float, cvr: float) -> None:
    item = bucket.setdefault(code, {"name": name, "count": 0, "training_count": 0, "score_total": 0.0, "effective_count": 0, "ctr_delta": 0.0, "cvr_delta": 0.0, "gmv_delta": 0.0, "cvr_total": 0.0, "elo": 1500})
    delta = sample.get("result_delta") or {}
    item["count"] = int(item.get("count") or 0) + 1
    item["training_count"] = int(item.get("training_count") or 0) + (1 if sample.get("use_for_training") else 0)
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
