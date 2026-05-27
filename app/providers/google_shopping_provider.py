from __future__ import annotations

import os
import re
import logging
from difflib import SequenceMatcher
from statistics import median

import requests
from dotenv import load_dotenv

from app.providers.base import PriceResult
from app.services.serpapi_cache import get_cached_payload, serpapi_cache_key, set_cached_payload

load_dotenv()
SERPAPI_API_KEY = os.getenv("SERPAPI_API_KEY")
logger = logging.getLogger(__name__)
REJECT_TITLE_KEYWORDS = ["Silver Label", "Used", "Outlet", "Women's", "Kids", "Geartrade", "Resale"]
ATOM_REJECT_TITLE_KEYWORDS = ["SL", "LT", "Heavyweight", "LEAF", "Women's", "XS", "Purple"]
MIN_TITLE_SIMILARITY = 0.8


class GoogleShoppingProvider:
    name = "google_shopping"

    @property
    def enabled(self) -> bool:
        return os.getenv("ENABLE_GOOGLE_SHOPPING_PROVIDER", "true").lower() == "true"

    def search_product(self, product_name: str, brand: str = "Arc'teryx") -> list[PriceResult]:
        if not SERPAPI_API_KEY:
            logger.info("No real Google Shopping evidence found")
            raise RuntimeError("SERPAPI_API_KEY is missing. No real Google Shopping evidence found.")

        query = f"Arc'teryx {product_name}"
        params = {
            "engine": "google",
            "tbm": "shop",
            "q": query,
            "api_key": SERPAPI_API_KEY,
            "num": 20,
        }
        cache_key = serpapi_cache_key(params)
        payload = get_cached_payload(cache_key)
        if payload is None:
            response = requests.get(
                "https://serpapi.com/search.json",
                params=params,
                timeout=12,
            )
            response.raise_for_status()
            payload = response.json()
            set_cached_payload(cache_key, payload)
        shopping_results = payload.get("shopping_results", [])
        if not isinstance(shopping_results, list):
            return []

        results: list[PriceResult] = []
        for item in shopping_results:
            result = self._map_result(item, product_name)
            if result:
                results.append(result)

        if results:
            logger.info("Using real shopping results")
            return _remove_price_outliers(
                sorted(results, key=lambda result: result.confidence, reverse=True)
            )[:5]

        logger.info("No real Google Shopping evidence found")
        return []

    def _map_result(self, item: dict, product_name: str) -> PriceResult | None:
        title = str(item.get("title") or "").strip()
        if not title:
            return None
        if _has_rejected_keyword(title):
            return None
        if _has_atom_rejected_variant(title, product_name):
            return None
        similarity = _title_similarity(title, product_name)
        if similarity <= MIN_TITLE_SIMILARITY:
            return None

        price = item.get("extracted_price")
        if price is None:
            price = _parse_price(str(item.get("price") or ""))
        if price is None or price <= 0:
            return None

        url = item.get("product_link") or item.get("link") or item.get("serpapi_product_api") or ""
        source = str(item.get("source") or "Google Shopping").strip()
        rating = item.get("rating")
        reviews = item.get("reviews")
        confidence = _relevance_score(title, product_name, similarity)
        confidence = _boost_confidence_with_review_signals(confidence, rating, reviews)

        return PriceResult(
            title=title,
            platform="Google Shopping",
            price=round(float(price), 2),
            currency=_guess_currency(str(item.get("price") or "")),
            url=str(url),
            image_url=str(item.get("thumbnail") or ""),
            source=source,
            confidence=confidence,
        )


def _parse_price(raw_price: str) -> float | None:
    match = re.search(r"[\d,.]+", raw_price)
    if not match:
        return None
    try:
        return float(match.group(0).replace(",", ""))
    except ValueError:
        return None


def _guess_currency(raw_price: str) -> str:
    if "¥" in raw_price or "￥" in raw_price:
        return "CNY"
    if "C$" in raw_price or "CA$" in raw_price:
        return "CAD"
    return "USD"


def _has_rejected_keyword(title: str) -> bool:
    lowered = title.lower()
    return any(keyword.lower() in lowered for keyword in REJECT_TITLE_KEYWORDS)


def _has_atom_rejected_variant(title: str, product_name: str) -> bool:
    if "atom hoody" not in product_name.lower():
        return False
    title_tokens = set(re.findall(r"[a-z0-9]+", title.lower()))
    for keyword in ATOM_REJECT_TITLE_KEYWORDS:
        normalized = keyword.lower().replace("'", "")
        if normalized in title_tokens:
            return True
    return False


def _title_similarity(title: str, product_name: str) -> float:
    clean_title = _normalize_match_text(title)
    clean_product = _normalize_match_text(product_name)
    if not clean_title or not clean_product:
        return 0.0
    if clean_product in clean_title:
        return 1.0
    return SequenceMatcher(None, clean_title, clean_product).ratio()


def _normalize_match_text(value: str) -> str:
    normalized = value.lower()
    normalized = normalized.replace("arc'teryx", "").replace("arcteryx", "")
    normalized = re.sub(r"[^a-z0-9 ]+", " ", normalized)
    normalized = re.sub(r"\b(men|mens|man|jacket|hoody|hoodie)\b", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def _relevance_score(title: str, product_name: str, similarity: float) -> float:
    title_words = set(re.findall(r"[a-z0-9]+", title.lower()))
    query_words = set(re.findall(r"[a-z0-9]+", product_name.lower()))
    if not query_words:
        return 0.5
    overlap = len(title_words & query_words) / len(query_words)
    brand_bonus = 0.15 if "arc" in title.lower() or "teryx" in title.lower() else 0.0
    return round(max(0.1, min(1.0, 0.35 + overlap * 0.25 + similarity * 0.25 + brand_bonus)), 2)


def _boost_confidence_with_review_signals(confidence: float, rating: object, reviews: object) -> float:
    try:
        rating_bonus = 0.03 if rating is not None and float(rating) >= 4 else 0.0
    except (TypeError, ValueError):
        rating_bonus = 0.0

    try:
        reviews_text = str(reviews or "0").replace(",", "")
        reviews_bonus = 0.03 if int(float(reviews_text)) >= 20 else 0.0
    except (TypeError, ValueError):
        reviews_bonus = 0.0

    return round(min(1.0, confidence + rating_bonus + reviews_bonus), 2)


def _remove_price_outliers(results: list[PriceResult]) -> list[PriceResult]:
    prices = [result.price for result in results if result.price > 0]
    if len(prices) < 3:
        return results
    median_price = median(prices)
    lower_bound = median_price * 0.6
    upper_bound = median_price * 1.4
    return [result for result in results if lower_bound <= result.price <= upper_bound]
