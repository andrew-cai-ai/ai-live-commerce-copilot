from __future__ import annotations

import os

from app.providers.base import PriceResult, SocialSignal


class DewuProvider:
    name = "dewu"

    @property
    def enabled(self) -> bool:
        return os.getenv("ENABLE_DEWU_PROVIDER", "true").lower() == "true"

    def search_product(self, product_name: str, brand: str = "Arc'teryx") -> list[PriceResult]:
        # Placeholder only. Do not scrape Dewu directly in V1.
        return []

    def search_social_signals(self, product_name: str, brand: str = "Arc'teryx") -> list[SocialSignal]:
        return []
