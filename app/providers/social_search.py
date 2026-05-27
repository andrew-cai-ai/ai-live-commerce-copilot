from __future__ import annotations

import logging
import os
import re
from collections import Counter
from typing import Any
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv

from app.providers.base import SocialPost, SocialSignal
from app.services.serpapi_cache import get_cached_payload, serpapi_cache_key, set_cached_payload

load_dotenv()

SERPAPI_API_KEY = os.getenv("SERPAPI_API_KEY")
logger = logging.getLogger(__name__)


class RealSocialSearchProvider:
    platform: str = ""
    query_suffix: str = ""
    allowed_domains: tuple[str, ...] = ()

    @property
    def enabled(self) -> bool:
        env_name = f"ENABLE_{getattr(self, 'name', self.platform).upper()}_PROVIDER"
        return os.getenv(env_name, "true").lower() == "true"

    def search_social_signals(self, product_name: str, brand: str = "Arc'teryx") -> list[SocialSignal]:
        if not SERPAPI_API_KEY:
            raise RuntimeError("SERPAPI_API_KEY is missing. No real social evidence found.")

        query = f"{product_name} {self.query_suffix}".strip()
        params = {
            "engine": "google",
            "q": query,
            "api_key": SERPAPI_API_KEY,
            "num": 10,
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
        posts = self._extract_posts(payload)
        if not posts:
            return []

        text_corpus = " ".join([post.post_title + " " + post.snippet for post in posts])
        return [
            SocialSignal(
                platform=self.platform_label,
                product_name=product_name,
                popularity_score=_popularity_from_posts(posts),
                keywords=_top_keywords(text_corpus),
                positive_points=_positive_points(text_corpus),
                concerns=_concerns(text_corpus),
                evidence_urls=[post.real_url for post in posts if post.real_url],
                confidence=0.82,
                evidence_posts=posts,
                most_common_user_language=_common_user_language(text_corpus),
            )
        ]

    @property
    def platform_label(self) -> str:
        return self.platform

    def _extract_posts(self, payload: dict[str, Any]) -> list[SocialPost]:
        organic_results = payload.get("organic_results", [])
        if not isinstance(organic_results, list):
            return []

        posts: list[SocialPost] = []
        for item in organic_results:
            if not isinstance(item, dict):
                continue
            url = str(item.get("link") or "").strip()
            if not _is_allowed_url(url, self.allowed_domains):
                continue
            title = str(item.get("title") or "").strip()
            if not title:
                continue
            snippet = str(item.get("snippet") or "").strip()
            if _is_bad_social_result(title, snippet, url):
                continue
            metrics_text = f"{title} {snippet}"
            top_comments = _extract_top_comments(snippet)
            if self.platform_label == "小红书" and not top_comments:
                continue
            posts.append(
                SocialPost(
                    post_title=title,
                    platform=self.platform_label,
                    likes=_extract_metric(metrics_text, ("赞", "点赞", "likes")),
                    favorites=_extract_metric(metrics_text, ("收藏", "favorites", "saved")),
                    comments=_extract_metric(metrics_text, ("评论", "comments")),
                    cover_image=str(item.get("thumbnail") or ""),
                    real_url=url,
                    snippet=snippet,
                    top_comments=top_comments,
                )
            )
        return posts[:5]


def _is_allowed_url(url: str, allowed_domains: tuple[str, ...]) -> bool:
    if not url or not allowed_domains:
        return False
    host = urlparse(url).netloc.lower()
    return any(domain in host for domain in allowed_domains)


def _is_bad_social_result(title: str, snippet: str, url: str) -> bool:
    text = f"{title} {snippet}".lower()
    parsed = urlparse(url)
    path = parsed.path.lower().strip("/")
    if "user/profile" in text or "user/profile" in path:
        return True
    if "no information is available" in text:
        return True
    if re.fullmatch(r"user/profile/[^/]+", path):
        return True
    if path in {"user", "profile", "user/profile"}:
        return True
    return False


def _extract_metric(text: str, labels: tuple[str, ...]) -> int | None:
    for label in labels:
        patterns = [
            rf"(\d+(?:\.\d+)?)([万kK]?)\s*{re.escape(label)}",
            rf"{re.escape(label)}\s*(\d+(?:\.\d+)?)([万kK]?)",
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return _metric_to_int(match.group(1), match.group(2))
    return None


def _metric_to_int(number: str, unit: str) -> int:
    value = float(number)
    if unit == "万":
        value *= 10000
    elif unit.lower() == "k":
        value *= 1000
    return int(value)


def _popularity_from_posts(posts: list[SocialPost]) -> float:
    if not posts:
        return 0.0
    metric_total = sum((post.likes or 0) + (post.favorites or 0) + (post.comments or 0) for post in posts)
    metric_score = min(0.25, metric_total / 20000)
    count_score = min(0.55, len(posts) * 0.11)
    return round(0.2 + count_score + metric_score, 4)


def _top_keywords(text: str) -> list[str]:
    candidates = [
        "通勤",
        "保暖",
        "防水",
        "硬壳",
        "轻量",
        "穿搭",
        "尺码",
        "真假",
        "显瘦",
        "户外",
        "冬天",
        "上身",
        "值得",
    ]
    found = [word for word in candidates if word in text]
    if found:
        return found[:8]

    tokens = re.findall(r"[A-Za-z][A-Za-z0-9'-]+", text)
    common = [word for word, _count in Counter(token.lower() for token in tokens).most_common(8)]
    return common


def _extract_top_comments(snippet: str) -> list[str]:
    if not snippet:
        return []
    cleaned = re.sub(r"\s+", " ", snippet).strip()
    parts = [
        part.strip(" #，,。.;；:：")
        for part in re.split(r"[。；;\n]|(?:\s{2,})", cleaned)
    ]
    comments = [
        part
        for part in parts
        if len(part) >= 6 and any(marker in part for marker in ("尺码", "值", "真假", "通勤", "保暖", "上身", "价格", "颜色"))
    ]
    if comments:
        return comments[:3]
    return [cleaned[:80]] if cleaned else []


def _positive_points(text: str) -> list[str]:
    rules = [
        ("通勤" in text or "日常" in text, "真实笔记提到通勤/日常场景"),
        ("保暖" in text or "冬天" in text, "真实笔记提到保暖需求"),
        ("防水" in text or "硬壳" in text, "真实笔记提到天气防护"),
        ("穿搭" in text or "上身" in text, "真实笔记适合提炼穿搭语言"),
        ("轻量" in text or "不臃肿" in text, "真实笔记提到轻量或不臃肿"),
    ]
    return [label for matched, label in rules if matched][:5]


def _concerns(text: str) -> list[str]:
    rules = [
        ("真假" in text or "正品" in text, "用户会关心真伪和正品证据"),
        ("尺码" in text or "偏大" in text or "偏小" in text, "用户会追问尺码和上身效果"),
        ("贵" in text or "价格" in text or "值得" in text, "用户会比较价格和值不值"),
        ("保暖" in text or "冬天" in text, "用户会确认具体温度和保暖边界"),
    ]
    return [label for matched, label in rules if matched][:5]


def _common_user_language(text: str) -> list[str]:
    phrases = [
        "值不值得买",
        "尺码怎么选",
        "日常通勤能不能穿",
        "冬天够不够暖",
        "真的假的",
        "上身好不好看",
        "价格合不合适",
        "颜色有没有",
    ]
    return [phrase for phrase in phrases if phrase[:2] in text or phrase[-2:] in text][:5]
