from __future__ import annotations

class ManualProvider:
    name = "manual"
    enabled = False

    def search_product(self, query: str) -> list[PriceResult]:
        return []
