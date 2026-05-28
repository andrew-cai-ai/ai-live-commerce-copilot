from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field

from app.providers.base import ManualResearchOverride, MarketResearchResult
from app.services.fx import FxRateService
from app.services.market_research import MarketResearchService
from search import ProductKnowledge, get_product_knowledge


@dataclass
class InventoryItem:
    product_name: str
    cost: float
    stock: int
    target_selling_price: float
    sku: str = ""
    sku_count: int = 0
    category: str = ""
    status: str = ""
    color: str = ""
    notes: str = ""
    source: str = "manual"
    inventory_unknown: bool = False
    cost_currency: str = "CAD"
    target_currency: str = "CNY"


@dataclass
class SellabilityScore:
    daily_wear_suitability: float
    mainstream_popularity: float
    conversion_difficulty: float
    price_accessibility: float
    gifting_potential: float
    score: float


@dataclass
class ScoredProduct:
    product_name: str
    cost: float
    cost_price_original: float
    original_cost: float
    cost_currency: str
    target_currency: str
    cad_to_cny_rate: float
    fx_source: str
    fx_timestamp: float
    fx_warning: str
    stock: int
    inventory_unknown: bool
    target_selling_price: float
    profit: float
    profit_margin: float
    price_gap: float
    price_gap_score: float
    min_competitor_price: float | None
    max_competitor_price: float | None
    avg_competitor_price: float | None
    inventory_priority: float
    popularity_score: float
    competition_score: float
    sellability: SellabilityScore
    score: float
    traffic_score: float
    conversion_score: float
    profit_score: float
    gmv_score: float
    gmv_level: str
    ranking_explanation: list[str]
    rank: int = 0
    knowledge: ProductKnowledge | None = field(default=None, repr=False)
    market_research: MarketResearchResult | None = field(default=None, repr=False)


def parse_inventory(raw_text: str) -> list[InventoryItem]:
    items: list[InventoryItem] = []

    for line_number, line in enumerate(raw_text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        if _looks_like_header(stripped):
            continue

        row = _split_row(stripped)
        if len(row) not in {4, 5}:
            raise ValueError(
                f"第 {line_number} 行需要 4 或 5 个字段：商品名、成本、库存、目标售价、可选成本币种。"
            )

        name, cost, stock, target_price = row[:4]
        cost_currency = row[4].strip().upper() if len(row) == 5 and row[4].strip() else "CAD"
        items.append(
            InventoryItem(
                product_name=name.strip(),
                cost=_parse_optional_money(cost, line_number, "cost") or 0.0,
                stock=_parse_optional_stock(stock, line_number) or 0,
                target_selling_price=_parse_optional_money(target_price, line_number, "target selling price") or 0.0,
                cost_currency=_validate_cost_currency(cost_currency, line_number),
            )
        )

    if not items:
        raise ValueError("请至少粘贴一个库存商品。")

    return items


def parse_manual_research_overrides(raw_text: str) -> dict[str, ManualResearchOverride]:
    overrides: dict[str, ManualResearchOverride] = {}
    if not raw_text.strip():
        return overrides

    for line_number, line in enumerate(raw_text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        if _looks_like_manual_header(stripped):
            continue

        row = _split_row(stripped)
        if len(row) != 6:
            raise ValueError(
                f"手动调研第 {line_number} 行需要 6 个字段：商品名、得物价、淘宝均价、抖音热度笔记、小红书评价笔记、竞品卖家价。"
            )

        product_name, dewu_price, taobao_average_price, douyin_notes, xiaohongshu_notes, competitor_price = row
        override = ManualResearchOverride(
            product_name=product_name.strip(),
            dewu_price=_parse_optional_money(dewu_price, line_number, "得物价"),
            taobao_average_price=_parse_optional_money(taobao_average_price, line_number, "淘宝均价"),
            douyin_heat_notes=douyin_notes.strip(),
            xiaohongshu_review_notes=xiaohongshu_notes.strip(),
            competitor_seller_price=_parse_optional_money(competitor_price, line_number, "竞品卖家价"),
        )
        if override.product_name:
            overrides[_normalize_product_key(override.product_name)] = override

    return overrides


def score_products(
    items: list[InventoryItem],
    manual_overrides: dict[str, ManualResearchOverride] | None = None,
) -> list[ScoredProduct]:
    raw_rows = []
    market_research_service = MarketResearchService()
    needs_cad_fx = any(item.cost_currency.upper() == "CAD" for item in items)
    fx_rate = FxRateService().get_cad_to_cny_rate() if needs_cad_fx else None
    manual_overrides = manual_overrides or {}

    for item in items:
        if item.cost < 0:
            raise ValueError(f"{item.product_name}: 成本不能为负数。")
        if item.stock < 0:
            raise ValueError(f"{item.product_name}: 库存不能为负数。")

        knowledge = get_product_knowledge(item.product_name)
        cost_cny = _convert_cost_to_cny(item.cost, item.cost_currency, fx_rate.rate if fx_rate else 1.0)
        target_selling_price = item.target_selling_price if item.target_selling_price > 0 else _default_target_price(cost_cny)
        market_research = market_research_service.research_product(
            product_name=item.product_name,
            target_selling_price=target_selling_price,
            manual_override=_find_manual_override(item.product_name, manual_overrides),
        )
        if fx_rate and fx_rate.warning:
            market_research.warnings.append(fx_rate.warning)
        target_selling_price = _resolve_target_price(item, market_research, cost_cny)
        profit = target_selling_price - cost_cny
        profit_margin = profit / target_selling_price if target_selling_price > 0 else 0
        price_gap = (
            market_research.avg_market_price - target_selling_price
            if market_research.avg_market_price is not None
            else profit
        )
        raw_rows.append((item, cost_cny, target_selling_price, knowledge, market_research, profit, profit_margin, price_gap))

    max_stock = max(
        (row[0].stock or row[0].sku_count for row in raw_rows if not row[0].inventory_unknown),
        default=0,
    )

    scored: list[ScoredProduct] = []
    for item, cost_cny, target_selling_price, knowledge, market_research, profit, profit_margin, price_gap in raw_rows:
        price_gap_score = market_research.price_gap_score
        inventory_units = item.stock or item.sku_count
        inventory_priority = 0.5 if item.inventory_unknown else inventory_units / max_stock if max_stock else 0
        popularity_score = market_research.popularity_score or knowledge.popularity_score
        sellability = calculate_sellability_score(item.product_name, target_selling_price, popularity_score)
        competition_score = _competition_score(market_research.avg_market_price, target_selling_price)
        score = (
            popularity_score * 0.35
            + sellability.score * 0.30
            + max(0.0, profit_margin) * 0.15
            + inventory_priority * 0.10
            + competition_score * 0.10
        )
        traffic_score = popularity_score
        conversion_score = sellability.score
        profit_score = max(0.0, min(1.0, profit_margin))
        gmv_score = traffic_score * 0.4 + conversion_score * 0.4 + profit_score * 0.2
        ranking_explanation = _build_ranking_explanation(
            popularity_score=popularity_score,
            profit_margin=profit_margin,
            inventory_priority=inventory_priority,
            competition_score=competition_score,
            sellability=sellability,
        )

        scored.append(
            ScoredProduct(
                product_name=item.product_name,
                cost=round(cost_cny, 2),
                cost_price_original=item.cost,
                original_cost=item.cost,
                cost_currency=item.cost_currency,
                target_currency=item.target_currency,
                cad_to_cny_rate=fx_rate.rate if fx_rate else 1.0,
                fx_source=fx_rate.source if fx_rate else "not_used_non_cad_cost",
                fx_timestamp=fx_rate.timestamp if fx_rate else 0.0,
                fx_warning=fx_rate.warning if fx_rate else "",
                stock=inventory_units,
                inventory_unknown=item.inventory_unknown,
                target_selling_price=target_selling_price,
                profit=profit,
                profit_margin=profit_margin,
                price_gap=price_gap,
                price_gap_score=price_gap_score,
                min_competitor_price=market_research.min_market_price,
                max_competitor_price=market_research.max_market_price,
                avg_competitor_price=market_research.avg_market_price,
                inventory_priority=inventory_priority,
                popularity_score=popularity_score,
                competition_score=competition_score,
                sellability=sellability,
                score=score,
                traffic_score=traffic_score,
                conversion_score=conversion_score,
                profit_score=profit_score,
                gmv_score=gmv_score,
                gmv_level=_gmv_level(gmv_score),
                ranking_explanation=ranking_explanation,
                knowledge=knowledge,
                market_research=market_research,
            )
        )

    scored.sort(key=lambda product: product.score, reverse=True)
    for index, product in enumerate(scored, start=1):
        product.rank = index

    return scored


def calculate_sellability_score(product_name: str, target_selling_price: float, popularity_score: float) -> SellabilityScore:
    normalized = product_name.lower()
    profile = _sellability_profile(normalized)
    price_accessibility = profile.get("price_accessibility")
    if price_accessibility is None:
        price_accessibility = _price_accessibility(target_selling_price)

    daily_wear = profile["daily_wear_suitability"]
    mainstream = profile.get("mainstream_popularity", popularity_score)
    conversion_difficulty = profile["conversion_difficulty"]
    gifting = profile.get("gifting_potential", 0.65)
    score = (
        daily_wear * 0.28
        + mainstream * 0.24
        + (1 - conversion_difficulty) * 0.22
        + price_accessibility * 0.16
        + gifting * 0.10
    )
    return SellabilityScore(
        daily_wear_suitability=round(daily_wear, 4),
        mainstream_popularity=round(mainstream, 4),
        conversion_difficulty=round(conversion_difficulty, 4),
        price_accessibility=round(price_accessibility, 4),
        gifting_potential=round(gifting, 4),
        score=round(score, 4),
    )


def _sellability_profile(normalized_name: str) -> dict[str, float]:
    if "alpha" in normalized_name:
        return {
            "daily_wear_suitability": 0.3,
            "mainstream_popularity": 0.72,
            "conversion_difficulty": 0.9,
            "price_accessibility": 0.22,
            "gifting_potential": 0.45,
        }
    if "beta lt" in normalized_name or ("beta" in normalized_name and "lt" in normalized_name):
        return {
            "daily_wear_suitability": 0.95,
            "mainstream_popularity": 0.93,
            "conversion_difficulty": 0.2,
            "price_accessibility": 0.68,
            "gifting_potential": 0.82,
        }
    if "beta" in normalized_name:
        return {
            "daily_wear_suitability": 0.86,
            "mainstream_popularity": 0.88,
            "conversion_difficulty": 0.32,
            "price_accessibility": 0.58,
            "gifting_potential": 0.78,
        }
    if "atom" in normalized_name:
        return {
            "daily_wear_suitability": 0.9,
            "mainstream_popularity": 0.92,
            "conversion_difficulty": 0.15,
            "price_accessibility": 0.78,
            "gifting_potential": 0.86,
        }
    if "cerium" in normalized_name:
        return {
            "daily_wear_suitability": 0.7,
            "mainstream_popularity": 0.82,
            "conversion_difficulty": 0.4,
            "price_accessibility": 0.55,
            "gifting_potential": 0.8,
        }
    return {
        "daily_wear_suitability": 0.68,
        "mainstream_popularity": 0.68,
        "conversion_difficulty": 0.45,
        "gifting_potential": 0.65,
    }


def _price_accessibility(target_selling_price: float) -> float:
    if target_selling_price <= 250:
        return 0.9
    if target_selling_price <= 450:
        return 0.72
    if target_selling_price <= 700:
        return 0.52
    if target_selling_price <= 1000:
        return 0.32
    return 0.18


def _build_ranking_explanation(
    popularity_score: float,
    profit_margin: float,
    inventory_priority: float,
    competition_score: float,
    sellability: SellabilityScore,
) -> list[str]:
    return [
        f"市场热度占 35%，当前 market_popularity={popularity_score:.2f}，真实社媒为空时会退回价格证据或本地品牌知识推断。",
        f"直播好卖程度占 30%，当前 sellability={sellability.score:.2f}，日常穿着={sellability.daily_wear_suitability:.2f}，转化难度={sellability.conversion_difficulty:.2f}。",
        f"利润率只占 15%，当前毛利率={profit_margin:.1%}，避免高毛利但难成交的款式排过高。",
        f"库存优先级占 10%，当前 inventory_priority={inventory_priority:.2f}；竞争分占 10%，当前 competition_score={competition_score:.2f}。",
    ]


def _default_target_price(cost: float) -> float:
    if cost <= 0:
        return 1.0
    return round(cost * 1.55, 2)


def _resolve_target_price(item: InventoryItem, market_research: MarketResearchResult, cost_cny: float) -> float:
    if item.target_selling_price > 0:
        return item.target_selling_price
    if market_research.avg_market_price and market_research.avg_market_price > cost_cny:
        return round(market_research.avg_market_price, 2)
    return _default_target_price(cost_cny)


def _convert_cost_to_cny(cost: float, currency: str, cad_to_cny_rate: float) -> float:
    currency = currency.upper()
    if currency == "CAD":
        return round(cost * cad_to_cny_rate, 2)
    if currency == "USD":
        return round(cost * 7.25, 2)
    return round(cost, 2)


def _validate_cost_currency(currency: str, line_number: int) -> str:
    normalized = currency.upper()
    if normalized not in {"CNY", "CAD", "USD"}:
        raise ValueError(f"第 {line_number} 行成本币种只支持 CNY、CAD、USD。")
    return normalized


def _competition_score(avg_market_price: float | None, target_selling_price: float) -> float:
    if not avg_market_price or target_selling_price <= 0:
        return 0.5
    ratio = (avg_market_price - target_selling_price) / target_selling_price
    return round(max(0.0, min(1.0, 0.55 + ratio)), 4)


def _gmv_level(gmv_score: float) -> str:
    if gmv_score >= 0.72:
        return "High"
    if gmv_score >= 0.5:
        return "Medium"
    return "Low"


def _split_row(line: str) -> list[str]:
    delimiter = "\t" if "\t" in line else "|" if "|" in line else ","
    return next(csv.reader(io.StringIO(line), delimiter=delimiter, skipinitialspace=True))


def _parse_money(value: str, line_number: int, field_name: str) -> float:
    cleaned = re.sub(r"[$,\s]", "", value)
    try:
        return round(float(cleaned), 2)
    except ValueError as exc:
        raise ValueError(f"第 {line_number} 行：{field_name} 必须是数字。") from exc


def _parse_optional_money(value: str, line_number: int, field_name: str) -> float | None:
    if not value.strip():
        return None
    return _parse_money(value, line_number, field_name)


def _parse_stock(value: str, line_number: int) -> int:
    try:
        return int(float(value.strip()))
    except ValueError as exc:
        raise ValueError(f"第 {line_number} 行：库存必须是整数。") from exc


def _parse_optional_stock(value: str, line_number: int) -> int | None:
    if not value.strip():
        return None
    return _parse_stock(value, line_number)


def _normalize(value: float, minimum: float, maximum: float) -> float:
    if maximum == minimum:
        return 1.0 if value > 0 else 0.0
    return max(0.0, min(1.0, (value - minimum) / (maximum - minimum)))


def _looks_like_header(line: str) -> bool:
    lowered = line.lower()
    has_product = "product" in lowered or "商品" in lowered or "品名" in lowered
    has_cost = "cost" in lowered or "成本" in lowered or "进价" in lowered or "供货价" in lowered
    has_inventory = "stock" in lowered or "inventory" in lowered or "库存" in lowered or "数量" in lowered
    return has_product and has_cost and has_inventory


def _looks_like_manual_header(line: str) -> bool:
    lowered = line.lower()
    return (
        ("product" in lowered or "商品" in lowered)
        and ("dewu" in lowered or "得物" in lowered)
        and ("taobao" in lowered or "淘宝" in lowered)
    )


def _find_manual_override(
    product_name: str,
    overrides: dict[str, ManualResearchOverride],
) -> ManualResearchOverride | None:
    key = _normalize_product_key(product_name)
    if key in overrides:
        return overrides[key]
    for override_key, override in overrides.items():
        if key in override_key or override_key in key:
            return override
    return None


def _normalize_product_key(product_name: str) -> str:
    return re.sub(r"\s+", " ", product_name.strip().lower())
