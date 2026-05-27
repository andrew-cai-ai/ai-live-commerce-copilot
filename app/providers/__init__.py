from __future__ import annotations

from app.providers.base import PriceProvider, SocialProvider
from app.providers.dewu_provider import DewuProvider
from app.providers.douyin_provider import DouyinProvider
from app.providers.google_shopping_provider import GoogleShoppingProvider
from app.providers.xiaohongshu_provider import XiaohongshuProvider
from app.providers.taobao_provider import TaobaoProvider


def get_enabled_providers() -> list[PriceProvider]:
    providers: list[PriceProvider] = [
        GoogleShoppingProvider(),
        TaobaoProvider(),
        DewuProvider(),
        DouyinProvider(),
        XiaohongshuProvider(),
    ]
    return [provider for provider in providers if provider.enabled]


def get_enabled_social_providers() -> list[SocialProvider]:
    providers: list[SocialProvider] = [
        TaobaoProvider(),
        DewuProvider(),
        DouyinProvider(),
        XiaohongshuProvider(),
    ]
    return [provider for provider in providers if provider.enabled]
