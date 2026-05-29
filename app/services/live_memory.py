from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = BASE_DIR / "data"
MEMORY_PATH = DATA_DIR / "live_memory.json"


DEFAULT_PLAYBOOKS: list[dict[str, Any]] = [
    {
        "match": ["kragg", "shirt", "tee", "t-shirt", "ss", "ls", "棉", "短袖", "长袖"],
        "name": "Kragg / T恤打法",
        "best_for": "通勤、日常、想买始祖鸟但不想上高价外套的客户",
        "sequence": ["尺码", "面料", "价格", "颜色库存"],
        "opening": "哥几个这件别当普通T看，平时通勤穿最不容易吃灰。",
        "conversion_line": "尺码合适的先锁，T恤这种价格段最容易断码。",
        "avoid": "不要长时间讲户外参数，先把尺码和价格讲清楚。",
    },
    {
        "match": ["gamma", "pant", "pants", "裤"],
        "name": "Gamma / 软壳裤打法",
        "best_for": "通勤、徒步、四季都想穿一条裤子的客户",
        "sequence": ["上身版型", "尺码", "通勤场景", "价格"],
        "opening": "这条裤子是那种买回去真的会一直穿的，不是只挂衣柜。",
        "conversion_line": "想要一条通勤和户外都能穿的，先看这条。",
        "avoid": "不要只讲面料名，要讲腿型、弹性和日常场景。",
    },
    {
        "match": ["atom"],
        "name": "Atom / 日常保暖打法",
        "best_for": "通勤、开车、轻户外、怕买回去不常穿的客户",
        "sequence": ["通勤场景", "轻暖", "尺码", "颜色库存"],
        "opening": "Atom这类就是始祖鸟里最好日常化的一档，平时通勤穿都行。",
        "conversion_line": "别光看价格，能天天穿才是真值。",
        "avoid": "不要讲得太专业，强调轻、暖、好搭、不吃灰。",
    },
    {
        "match": ["cerium"],
        "name": "Cerium / 轻量羽绒打法",
        "best_for": "要轻、要暖、要能压缩收纳的客户",
        "sequence": ["保暖重量", "适合温度", "尺码", "价格价值"],
        "opening": "这件重点不是厚，是轻，而且保暖效率高。",
        "conversion_line": "怕冷又不想穿得臃肿的，重点看这件。",
        "avoid": "不要承诺极寒万能，要讲地区、内搭和温度边界。",
    },
    {
        "match": ["alpha", "sv"],
        "name": "Alpha / 专业户外打法",
        "best_for": "懂硬壳、要专业配置、愿意为顶级定位付费的客户",
        "sequence": ["专业定位", "细节证据", "适合人群", "价格锚点"],
        "opening": "Alpha不是给所有人的，懂它的人看的是专业定位和细节。",
        "conversion_line": "要顶级硬壳的重点看，日常通勤我反而会推荐别的款。",
        "avoid": "不要硬推给所有人，先筛选专业需求。",
    },
    {
        "match": ["beta"],
        "name": "Beta / 防护硬壳打法",
        "best_for": "想要雨雪防护、通勤和轻户外兼顾的客户",
        "sequence": ["天气防护", "日常场景", "尺码", "价格"],
        "opening": "Beta这类好卖，是因为它不是只能爬山，通勤下雨也能穿。",
        "conversion_line": "想要一件雨雪天能直接上身的，先看Beta。",
        "avoid": "不要只讲防水参数，要讲城市雨雪和穿搭场景。",
    },
    {
        "match": ["rho", "zip neck", "fleece", "抓绒", "保暖内搭"],
        "name": "Rho / 抓绒内搭打法",
        "best_for": "秋冬内搭、滑雪内搭、怕冷但不想臃肿的客户",
        "sequence": ["保暖场景", "贴身尺码", "内搭方式", "库存"],
        "opening": "抓绒内搭这种不是看着炸，但复购和日常使用率很高。",
        "conversion_line": "怕冷的先把内搭配好，比一味买厚外套更实用。",
        "avoid": "不要当外套讲，重点讲贴身、保暖和叠穿。",
    },
    {
        "match": ["shoe", "gtx", "kragg shoe", "norvan", "鞋"],
        "name": "鞋类打法",
        "best_for": "通勤走路多、户外轻徒步、想要折扣鞋的客户",
        "sequence": ["尺码脚型", "场景", "鞋底/防水", "价格"],
        "opening": "鞋先别急着拍，先把尺码脚型说清楚，合适再下单。",
        "conversion_line": "脚型合适的再冲，鞋子最怕买错码。",
        "avoid": "不要先逼单，鞋类先解决尺码和脚型信任。",
    },
]


class LiveMemoryService:
    def __init__(self, path: Path = MEMORY_PATH) -> None:
        self.path = path
        self.memory = self._load()

    def get_playbook(self, product_name: str) -> dict[str, Any]:
        name = str(product_name or "").strip()
        base = _default_playbook(name)
        product_key = _product_key(name)
        learned = self.memory.get("products", {}).get(product_key, {})
        best_actions = _rank_action_stats(learned.get("actions", {}))
        return {
            **base,
            "product_name": name or "当前商品",
            "learned_samples": int(learned.get("samples") or 0),
            "best_position": learned.get("best_position") or base.get("best_position") or "--",
            "best_actions": best_actions[:5],
            "confidence": min(0.95, 0.55 + min(int(learned.get("samples") or 0), 20) * 0.02),
        }

    def record_action_effect(
        self,
        host_id: str,
        product_name: str,
        action: str,
        delta: dict[str, Any],
        result: str,
    ) -> None:
        product_key = _product_key(product_name)
        host_key = _host_key(host_id)
        action_key = _action_key(action)
        score = _memory_score(delta)
        now = time.time()
        product = self.memory.setdefault("products", {}).setdefault(product_key, {
            "display_name": product_name or "当前商品",
            "samples": 0,
            "actions": {},
        })
        host = self.memory.setdefault("hosts", {}).setdefault(host_key, {
            "display_name": host_id or "default",
            "samples": 0,
            "actions": {},
        })
        for bucket in (product, host):
            bucket["samples"] = int(bucket.get("samples") or 0) + 1
            bucket["last_seen_at"] = now
            actions = bucket.setdefault("actions", {})
            stats = actions.setdefault(action_key, {
                "label": action or "已执行 AI 建议",
                "count": 0,
                "score_total": 0.0,
                "effective_count": 0,
                "gmv_delta": 0.0,
                "cvr_delta": 0.0,
                "ctr_delta": 0.0,
            })
            stats["count"] = int(stats.get("count") or 0) + 1
            stats["score_total"] = float(stats.get("score_total") or 0) + score
            stats["effective_count"] = int(stats.get("effective_count") or 0) + (1 if result == "有效" else 0)
            stats["gmv_delta"] = float(stats.get("gmv_delta") or 0) + float(delta.get("gmv") or 0)
            stats["cvr_delta"] = float(stats.get("cvr_delta") or 0) + float(delta.get("cvr") or 0)
            stats["ctr_delta"] = float(stats.get("ctr_delta") or 0) + float(delta.get("ctr") or 0)
        self._save()

    def host_profile(self, host_id: str) -> dict[str, Any]:
        host = self.memory.get("hosts", {}).get(_host_key(host_id), {})
        return {
            "host_id": host_id or "default",
            "samples": int(host.get("samples") or 0),
            "best_actions": _rank_action_stats(host.get("actions", {}))[:5],
        }

    def product_profile(self, product_name: str) -> dict[str, Any]:
        return self.get_playbook(product_name)

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"products": {}, "hosts": {}, "updated_at": 0}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data.setdefault("products", {})
                data.setdefault("hosts", {})
                return data
        except Exception:
            return {"products": {}, "hosts": {}, "updated_at": 0}
        return {"products": {}, "hosts": {}, "updated_at": 0}

    def _save(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.memory["updated_at"] = time.time()
        self.path.write_text(json.dumps(self.memory, ensure_ascii=False, indent=2), encoding="utf-8")


def director_brief(product_name: str) -> dict[str, Any]:
    return live_memory.get_playbook(product_name)


def _default_playbook(product_name: str) -> dict[str, Any]:
    text = product_name.lower()
    tokens = set(re.findall(r"[a-z0-9]+|[\u4e00-\u9fa5]+", text))
    best = None
    best_score = -1
    for playbook in DEFAULT_PLAYBOOKS:
        score = 0
        for keyword in playbook["match"]:
            key = keyword.lower()
            if key in text or key in tokens:
                score += 1
        if score > best_score:
            best = playbook
            best_score = score
    if not best or best_score <= 0:
        best = {
            "name": "通用商品打法",
            "best_for": "对价格、尺码和使用场景还在犹豫的客户",
            "sequence": ["适合谁", "尺码/场景", "价格价值", "库存"],
            "opening": "哥几个先看适不适合自己，适合再拍。",
            "conversion_line": "别光看价格，关键是买回去会不会常穿。",
            "avoid": "不要一直讲参数，先解决尺码、真假和价格顾虑。",
        }
    return {key: value for key, value in best.items() if key != "match"}


def _rank_action_stats(actions: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for stats in actions.values():
        count = max(int(stats.get("count") or 0), 1)
        rows.append({
            "action": stats.get("label") or "已执行 AI 建议",
            "count": count,
            "avg_score": float(stats.get("score_total") or 0) / count,
            "effective_rate": int(stats.get("effective_count") or 0) / count,
            "avg_gmv_delta": float(stats.get("gmv_delta") or 0) / count,
            "avg_cvr_delta": float(stats.get("cvr_delta") or 0) / count,
            "avg_ctr_delta": float(stats.get("ctr_delta") or 0) / count,
        })
    return sorted(rows, key=lambda item: (item["avg_score"], item["effective_rate"]), reverse=True)


def _memory_score(delta: dict[str, Any]) -> float:
    return (
        float(delta.get("gmv") or 0) / 100
        + float(delta.get("cvr") or 0) * 1000
        + float(delta.get("ctr") or 0) * 350
        + float(delta.get("heat") or 0) / 20
    )


def _product_key(value: str) -> str:
    text = re.sub(r"\s+", " ", str(value or "").lower()).strip()
    return re.sub(r"[^a-z0-9\u4e00-\u9fa5]+", "-", text)[:120] or "unknown-product"


def _host_key(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.:-]+", "-", str(value or "default"))[:80] or "default"


def _action_key(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "已执行 AI 建议").strip().lower())[:80]


live_memory = LiveMemoryService()
