from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProductKnowledge:
    popularity_score: float
    category: str
    similar_products: list[str]
    likely_selling_points: list[str]
    likely_customer_concerns: list[str]
    suggested_image_evidence: list[str]


POPULARITY_KEYWORDS: dict[str, float] = {
    "alpha": 0.96,
    "beta": 0.9,
    "atom": 0.92,
    "cerium": 0.89,
    "thorium": 0.84,
    "gamma": 0.82,
    "proton": 0.86,
    "sabre": 0.8,
    "rush": 0.83,
    "mantis": 0.78,
    "aerios": 0.76,
    "norvan": 0.74,
    "solano": 0.72,
}


CATEGORY_KEYWORDS: dict[str, tuple[str, list[str]]] = {
    "alpha": ("高山旗舰硬壳", ["Beta AR Jacket", "Beta LT Jacket", "Rush Jacket"]),
    "beta": ("全能硬壳", ["Alpha SV Jacket", "Beta LT Jacket", "Gamma Hoody"]),
    "atom": ("轻量合成棉中间层", ["Proton Hoody", "Cerium Hoody", "Thorium Hoody"]),
    "proton": ("高透气动态保暖层", ["Atom Hoody", "Gamma Hoody", "Cerium Hoody"]),
    "cerium": ("轻量羽绒保暖层", ["Thorium Hoody", "Atom Hoody", "Proton Hoody"]),
    "thorium": ("高保暖羽绒层", ["Cerium Hoody", "Atom Hoody", "Proton Hoody"]),
    "gamma": ("软壳外套", ["Beta Jacket", "Atom Hoody", "Proton Hoody"]),
    "sabre": ("滑雪硬壳", ["Rush Jacket", "Beta AR Jacket", "Alpha SV Jacket"]),
    "rush": ("滑雪/高山硬壳", ["Sabre Jacket", "Alpha SV Jacket", "Beta AR Jacket"]),
    "mantis": ("背包配件", ["Aerios Pack", "Granville Pack", "Heliad Pack"]),
    "aerios": ("徒步越野系列", ["Norvan Shoe", "Mantis Pack", "Gamma Pant"]),
    "norvan": ("越野跑系列", ["Aerios Shoe", "Sylan Shoe", "Gamma Lightweight Hoody"]),
}


def get_product_knowledge(product_name: str) -> ProductKnowledge:
    normalized = product_name.lower()
    matched_keyword = next((key for key in POPULARITY_KEYWORDS if key in normalized), None)

    popularity_score = POPULARITY_KEYWORDS.get(matched_keyword or "", 0.64)
    category, similar = CATEGORY_KEYWORDS.get(
        matched_keyword or "",
        ("专业户外产品", ["同类防护款", "同类通勤款", "同类保暖款"]),
    )

    return ProductKnowledge(
        popularity_score=popularity_score,
        category=category,
        similar_products=similar,
        likely_selling_points=[
            f"始祖鸟{category}定位清晰，用户认知强",
            "面料、拉链、剪裁和做工适合直播细节展示",
            "适合强调专业户外、通勤升级和送礼价值",
        ],
        likely_customer_concerns=[
            "尺码和上身效果是否合适",
            "使用场景是否配得上这个价格",
            "真伪、成色、吊牌和售后保障",
        ],
        suggested_image_evidence=[
            "吊牌、型号标签和尺码标近景",
            "面料、拉链、袖口、压胶或走线细节",
            "正面、背面、上身图和瑕疵位置照片",
        ],
    )
