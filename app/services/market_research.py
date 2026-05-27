from __future__ import annotations

from statistics import mean

from app.providers.base import (
    ManualResearchOverride,
    MarketResearchResult,
    PriceProvider,
    PriceResult,
    SocialProvider,
    SocialSignal,
)
from app.providers.douyin_provider import DouyinProvider
from app.providers.google_shopping_provider import GoogleShoppingProvider
from app.providers.xiaohongshu_provider import XiaohongshuProvider


class MarketResearchService:
    def __init__(
        self,
        price_providers: list[PriceProvider] | None = None,
        social_providers: list[SocialProvider] | None = None,
    ) -> None:
        self.price_providers = price_providers or [GoogleShoppingProvider()]
        self.social_providers = social_providers or [
            DouyinProvider(),
            XiaohongshuProvider(),
        ]

    def research_product(
        self,
        product_name: str,
        target_selling_price: float,
        brand: str = "Arc'teryx",
        manual_override: ManualResearchOverride | None = None,
    ) -> MarketResearchResult:
        warnings: list[str] = []
        price_results: list[PriceResult] = []
        social_signals: list[SocialSignal] = []

        for provider in self.price_providers:
            if not provider.enabled:
                continue
            try:
                price_results.extend(provider.search_product(product_name, brand=brand))
            except Exception as exc:
                warnings.append(f"{provider.name} 暂不可用：{exc}")

        if manual_override:
            manual_prices = _manual_price_results(manual_override)
            if manual_prices:
                warnings.append("已合并手动价格调研数据，手动价格按更高置信度参与计算。")
            price_results.extend(manual_prices)

        if not price_results:
            warnings.append("未获取到真实竞品价格；价格/竞争分将使用 AI 推断默认值，不展示假价格证据。")

        for provider in self.social_providers:
            if not provider.enabled:
                continue
            try:
                social_signals.extend(provider.search_social_signals(product_name, brand=brand))
            except Exception as exc:
                warnings.append(f"{provider.name} 社媒信号暂不可用：{exc}")

        social_signals = _merge_social_signals(social_signals)
        if not social_signals:
            warnings.append("No real social evidence found")
        prices = [result.price for result in price_results if result.price > 0]
        avg_market_price = mean(prices) if prices else None
        min_market_price = min(prices) if prices else None
        max_market_price = max(prices) if prices else None
        popularity_score = _calculate_popularity_score(social_signals, price_results)
        price_gap_score = _calculate_price_gap_score(avg_market_price, target_selling_price)

        return MarketResearchResult(
            product_name=product_name,
            price_results=price_results,
            social_signals=social_signals,
            avg_market_price=round(avg_market_price, 2) if avg_market_price is not None else None,
            min_market_price=round(min_market_price, 2) if min_market_price is not None else None,
            max_market_price=round(max_market_price, 2) if max_market_price is not None else None,
            price_gap_score=price_gap_score,
            popularity_score=popularity_score,
            evidence_summary=_build_evidence_summary(price_results, social_signals, warnings),
            warnings=warnings,
        )


def _manual_price_results(override: ManualResearchOverride) -> list[PriceResult]:
    rows: list[tuple[str, float | None, str]] = [
        ("得物", override.dewu_price, "manual_dewu_price"),
        ("淘宝", override.taobao_average_price, "manual_taobao_average_price"),
        ("竞品卖家", override.competitor_seller_price, "manual_competitor_seller_price"),
    ]
    return [
            PriceResult(
                title=f"{override.product_name} 手动调研价",
                platform=platform,
                price=round(price, 2),
                currency="CNY",
                url="",
                image_url="",
                source=source,
            confidence=0.92,
        )
        for platform, price, source in rows
        if price is not None and price > 0
    ]


def _notes_to_points(notes: str) -> list[str]:
    parts = [part.strip() for part in notes.replace("，", ";").replace(",", ";").split(";")]
    return [part for part in parts if part][:5] or [notes.strip()]


def _keywords_from_notes(notes: str) -> list[str]:
    words = [word.strip(" #，,。.;；") for word in notes.split()]
    return [word for word in words if word][:6] or ["手动调研"]


def _concerns_from_notes(notes: str) -> list[str]:
    concern_markers = ["担心", "顾虑", "吐槽", "问题", "怕", "不确定"]
    points = _notes_to_points(notes)
    concerns = [point for point in points if any(marker in point for marker in concern_markers)]
    return concerns[:5]


def _calculate_price_gap_score(avg_market_price: float | None, target_selling_price: float) -> float:
    if not avg_market_price or target_selling_price <= 0:
        return 0.0
    gap_ratio = (avg_market_price - target_selling_price) / target_selling_price
    return round(max(0.0, min(1.0, 0.5 + gap_ratio)), 4)


def _merge_social_signals(signals: list[SocialSignal]) -> list[SocialSignal]:
    merged: dict[str, list[SocialSignal]] = {}
    for signal in signals:
        merged.setdefault(signal.platform, []).append(signal)

    deduped = []
    for platform, platform_signals in merged.items():
        total_confidence = sum(signal.confidence for signal in platform_signals) or 1
        weighted_popularity = sum(
            signal.popularity_score * signal.confidence for signal in platform_signals
        ) / total_confidence
        deduped.append(
            SocialSignal(
                platform=platform,
                product_name=platform_signals[0].product_name,
                popularity_score=round(weighted_popularity, 4),
                keywords=_dedupe_text_list([item for signal in platform_signals for item in signal.keywords]),
                positive_points=_dedupe_text_list(
                    [item for signal in platform_signals for item in signal.positive_points]
                ),
                concerns=_dedupe_text_list([item for signal in platform_signals for item in signal.concerns]),
                evidence_urls=_dedupe_text_list([item for signal in platform_signals for item in signal.evidence_urls]),
                confidence=round(max(signal.confidence for signal in platform_signals), 4),
                evidence_posts=[post for signal in platform_signals for post in signal.evidence_posts],
                most_common_user_language=_dedupe_text_list(
                    [item for signal in platform_signals for item in signal.most_common_user_language]
                    + [comment for signal in platform_signals for post in signal.evidence_posts for comment in post.top_comments]
                ),
            )
        )

    return sorted(deduped, key=lambda signal: signal.confidence, reverse=True)


def _dedupe_text_list(items: list[str]) -> list[str]:
    seen = set()
    deduped = []
    for item in items:
        normalized = " ".join(str(item).strip().lower().split())
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(str(item).strip())
    return deduped[:8]


def _calculate_popularity_score(social_signals: list[SocialSignal], price_results: list[PriceResult]) -> float:
    scores = [signal.popularity_score * signal.confidence for signal in social_signals if signal.confidence > 0]
    weights = [signal.confidence for signal in social_signals if signal.confidence > 0]
    if scores and weights:
        return round(sum(scores) / sum(weights), 4)
    if price_results:
        return round(mean(result.confidence for result in price_results), 4)
    return 0.5


def _build_evidence_summary(
    price_results: list[PriceResult],
    social_signals: list[SocialSignal],
    warnings: list[str],
) -> list[str]:
    evidence = []
    if price_results:
        platforms = sorted({result.platform for result in price_results})
        evidence.append(f"价格参考来自：{', '.join(platforms)}")
    if social_signals:
        social_platforms = sorted({signal.platform for signal in social_signals})
        evidence.append(f"社媒信号来自：{', '.join(social_platforms)}")
    for signal in social_signals[:2]:
        if signal.concerns:
            evidence.append(f"{signal.platform} 主要顾虑：{'；'.join(signal.concerns[:2])}")
    evidence.extend(warnings)
    return evidence[:6]
