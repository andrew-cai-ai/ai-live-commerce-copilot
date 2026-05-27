from __future__ import annotations

from app.providers.base import PriceResult, SocialSignal
from app.providers.social_search import RealSocialSearchProvider


class XiaohongshuProvider(RealSocialSearchProvider):
    name = "xiaohongshu"
    platform = "小红书"
    query_suffix = "小红书"
    allowed_domains = ("xiaohongshu.com", "xhslink.com")

    def search_product(self, product_name: str, brand: str = "Arc'teryx") -> list[PriceResult]:
        return []
