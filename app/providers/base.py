from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class PriceResult:
    title: str
    platform: str
    price: float
    currency: str
    url: str
    image_url: str
    source: str
    confidence: float


@dataclass(frozen=True)
class SocialPost:
    post_title: str
    platform: str
    likes: int | None
    favorites: int | None
    comments: int | None
    cover_image: str
    real_url: str
    snippet: str = ""
    top_comments: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SocialSignal:
    platform: str
    product_name: str
    popularity_score: float
    keywords: list[str]
    positive_points: list[str]
    concerns: list[str]
    evidence_urls: list[str]
    confidence: float
    evidence_posts: list[SocialPost] = field(default_factory=list)
    most_common_user_language: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class MarketResearchResult:
    product_name: str
    price_results: list[PriceResult]
    social_signals: list[SocialSignal]
    avg_market_price: float | None
    min_market_price: float | None
    max_market_price: float | None
    price_gap_score: float
    popularity_score: float
    evidence_summary: list[str]
    warnings: list[str]


@dataclass(frozen=True)
class ManualResearchOverride:
    product_name: str
    dewu_price: float | None = None
    taobao_average_price: float | None = None
    douyin_heat_notes: str = ""
    xiaohongshu_review_notes: str = ""
    competitor_seller_price: float | None = None


@dataclass(frozen=True)
class TaobaoProduct:
    source_product_title: str
    source_product_image: str
    source_product_id: str
    price: float
    advise_sale_price_high: float | None
    advise_sale_price_low: float | None
    sku_number: int
    target_product_status: str
    cat_name: str
    supplier_name: str


@dataclass(frozen=True)
class TaobaoProductScore:
    product: TaobaoProduct
    advised_price: float
    profit_margin: float
    inventory_score: float
    sellability_score: float
    final_score: float
    reason: list[str]


@dataclass(frozen=True)
class TaobaoLivestreamOrderItem:
    slot: str
    product_score: TaobaoProductScore
    reason: str


@dataclass(frozen=True)
class TaobaoTopProductsReport:
    scores: list[TaobaoProductScore]
    livestream_order: list[TaobaoLivestreamOrderItem]
    warnings: list[str]


class PriceProvider(Protocol):
    name: str
    enabled: bool

    def search_product(self, product_name: str, brand: str = "Arc'teryx") -> list[PriceResult]:
        """Return market price results for a product query."""


class SocialProvider(Protocol):
    name: str
    enabled: bool

    def search_social_signals(self, product_name: str, brand: str = "Arc'teryx") -> list[SocialSignal]:
        """Return social demand and customer concern signals for a product query."""
