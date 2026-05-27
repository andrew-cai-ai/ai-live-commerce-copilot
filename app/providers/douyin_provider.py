from __future__ import annotations

from app.providers.base import PriceResult, SocialSignal
from app.providers.social_search import RealSocialSearchProvider


class DouyinProvider(RealSocialSearchProvider):
    name = "douyin"
    platform = "抖音"
    query_suffix = "抖音"
    allowed_domains = ("douyin.com",)

    def search_product(self, product_name: str, brand: str = "Arc'teryx") -> list[PriceResult]:
        return []
