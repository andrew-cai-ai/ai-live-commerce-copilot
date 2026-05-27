from __future__ import annotations

import json
import logging

from app.providers.base import (
    TaobaoLivestreamOrderItem,
    TaobaoProduct,
    TaobaoProductScore,
    TaobaoTopProductsReport,
)
from app.providers.taobao_provider import TaobaoProvider

logger = logging.getLogger(__name__)


class TaobaoLiveScoringService:
    def __init__(self, provider: TaobaoProvider | None = None) -> None:
        self.provider = provider or TaobaoProvider()

    def build_report(self, query: str = "Arc'teryx", pasted_json: str = "") -> TaobaoTopProductsReport:
        warnings: list[str] = []
        products: list[TaobaoProduct] = []

        if pasted_json.strip():
            try:
                products = self.provider.parse_pasted_json(pasted_json)
            except json.JSONDecodeError as exc:
                warnings.append(f"淘宝 JSON 解析失败：第 {exc.lineno} 行第 {exc.colno} 列不是有效 JSON。")
            except Exception as exc:
                warnings.append(f"淘宝 JSON 解析失败：{exc}")
        else:
            try:
                products = self.provider.search_taobao_products(query)
            except Exception as exc:
                logger.info("Taobao API URL mode skipped or unavailable: %s", exc)
                products = []
                warnings.append("未粘贴淘宝 JSON；API URL 模式未配置或暂不可用，本区块暂不展示淘宝商品池。")

        scores = score_taobao_products(products)
        return TaobaoTopProductsReport(
            scores=scores,
            livestream_order=build_livestream_order(scores),
            warnings=warnings,
        )


def score_taobao_products(products: list[TaobaoProduct]) -> list[TaobaoProductScore]:
    max_stock = max((product.sku_number for product in products), default=0)
    scored = [_score_product(product, max_stock) for product in products]
    return sorted(scored, key=lambda item: item.final_score, reverse=True)


def build_livestream_order(scores: list[TaobaoProductScore]) -> list[TaobaoLivestreamOrderItem]:
    if not scores:
        return []

    remaining = scores.copy()
    order: list[TaobaoLivestreamOrderItem] = []

    start = remaining.pop(0)
    order.append(TaobaoLivestreamOrderItem("开场主推", start, "综合分最高，最适合作为今日开场爆品拉转化。"))

    if remaining:
        second = remaining.pop(0)
        order.append(TaobaoLivestreamOrderItem("第二件承接", second, "承接第一波流量，继续讲高需求和库存优势。"))

    if remaining:
        profit_pick = max(remaining, key=lambda item: item.profit_margin)
        remaining.remove(profit_pick)
        order.append(TaobaoLivestreamOrderItem("利润款", profit_pick, "毛利空间更好，适合在互动稳定后重点推。"))

    if remaining:
        premium_pick = max(remaining, key=lambda item: (item.advised_price, item.final_score))
        remaining.remove(premium_pick)
        order.append(TaobaoLivestreamOrderItem("高客单收尾", premium_pick, "客单价更高，放在后段给专业客户和高预算客户做收口。"))

    return order


def _score_product(product: TaobaoProduct, max_stock: int) -> TaobaoProductScore:
    advised_price = _advised_price(product)
    profit_margin = _profit_margin(product.price, advised_price)
    inventory_score = _inventory_score(product.sku_number, max_stock)
    sellability_score = _sellability_score(product.source_product_title, product.cat_name)
    final_score = max(0, profit_margin) * 0.35 + inventory_score * 0.2 + sellability_score * 0.45

    return TaobaoProductScore(
        product=product,
        advised_price=advised_price,
        profit_margin=profit_margin,
        inventory_score=inventory_score,
        sellability_score=sellability_score,
        final_score=final_score,
        reason=_reason(product, profit_margin, inventory_score, sellability_score),
    )


def _advised_price(product: TaobaoProduct) -> float:
    if product.advise_sale_price_high and product.advise_sale_price_high > 0:
        return product.advise_sale_price_high
    if product.advise_sale_price_low and product.advise_sale_price_low > 0:
        return product.advise_sale_price_low
    return product.price


def _profit_margin(cost_price: float, advised_price: float) -> float:
    if advised_price <= 0:
        return 0.0
    return (advised_price - cost_price) / advised_price


def _inventory_score(stock: int, max_stock: int) -> float:
    if max_stock <= 0:
        return 0.0
    return max(0.0, min(1.0, stock / max_stock))


def _sellability_score(title: str, category: str) -> float:
    text = f"{title} {category}".lower()
    score = 0.55

    if any(keyword in text for keyword in ("atom", "kyanite", "gamma", "solano", "mantis", "通勤", "日常", "抓绒")):
        score += 0.3
    if any(keyword in text for keyword in ("cerium", "thorium", "proton", "保暖", "羽绒", "棉服")):
        score += 0.22
    if any(keyword in text for keyword in ("beta", "shell", "硬壳", "冲锋衣", "防水")):
        score += 0.18
    if any(keyword in text for keyword in ("alpha", " sv", "专业", "高山", "攀登")):
        score -= 0.22
    if any(keyword in text for keyword in ("women", "women's", "kids", "童", "女款", "xs")):
        score -= 0.18

    return max(0.05, min(1.0, score))


def _reason(
    product: TaobaoProduct,
    profit_margin: float,
    inventory_score: float,
    sellability_score: float,
) -> list[str]:
    sellability_label = "容易转化" if sellability_score >= 0.8 else "需要讲清场景" if sellability_score >= 0.55 else "偏小众，谨慎主推"
    return [
        f"建议毛利率约 {profit_margin:.1%}，按最高建议售价和供货价计算。",
        f"库存 {product.sku_number} 件，库存分 {inventory_score:.2f}，适合直播间承接订单。",
        f"标题/类目判断为：{sellability_label}，好卖程度分 {sellability_score:.2f}。",
        f"类目：{product.cat_name or '未知'}；只保留 targetProductStatus=1 的可售商品。",
    ]
