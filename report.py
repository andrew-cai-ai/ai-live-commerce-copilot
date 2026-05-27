from __future__ import annotations

import html
import json
import os
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI

from app.providers.base import TaobaoTopProductsReport
from app.services.audience_questions import AudienceQuestionResponse
from scoring import ScoredProduct

load_dotenv()


def generate_product_reports(products: list[ScoredProduct]) -> list[dict[str, Any]]:
    if not os.getenv("OPENAI_API_KEY"):
        return [_fallback_product_report(product) for product in products]

    client = OpenAI()
    prompt = _build_prompt(products)
    model = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")

    try:
        response = client.responses.create(
            model=model,
            input=[
                {
                    "role": "system",
                    "content": (
                        "你是面向中文主播和中文客户的户外服饰直播选品助手。"
                        "只返回简洁、促转化、适合直播口播的简体中文 JSON。"
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            text={
                "format": {
                    "type": "json_schema",
                    "name": "livestream_selection_report",
                    "schema": _report_json_schema(),
                    "strict": True,
                }
            },
            temperature=0.4,
        )
        text = response.output_text
        payload = json.loads(text)
        reports = payload.get("products", [])
        if not isinstance(reports, list):
            raise ValueError("OpenAI response did not contain a products list.")
        return [_merge_report(product, reports) for product in products]
    except Exception:
        return [_fallback_product_report(product) for product in products]


def render_report_page(
    products: list[ScoredProduct],
    reports: list[dict[str, Any]],
    audience_answers: list[AudienceQuestionResponse] | None = None,
    taobao_report: TaobaoTopProductsReport | None = None,
) -> str:
    report_by_name = {report["product_name"]: report for report in reports}
    livestream_order = _livestream_order(products, report_by_name)
    rows = "\n".join(_render_product_card(product, report_by_name[product.product_name]) for product in products)
    audience_answers = audience_answers or []

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AI Live Commerce Copilot v2</title>
  <style>
    :root {{
      color-scheme: light;
      --ink: #17211f;
      --muted: #5b6764;
      --line: #d7dfdc;
      --paper: #f7f9f6;
      --panel: #ffffff;
      --accent: #0c6b58;
      --accent-soft: #e0f1ea;
      --warn: #a14b10;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: var(--paper);
      color: var(--ink);
      line-height: 1.5;
    }}
    main {{ max-width: 1180px; margin: 0 auto; padding: 32px 20px 56px; }}
    header {{ display: flex; justify-content: space-between; gap: 24px; align-items: flex-end; margin-bottom: 24px; }}
    h1 {{ margin: 0; font-size: clamp(28px, 4vw, 46px); line-height: 1.05; letter-spacing: 0; }}
    h2 {{ margin: 0 0 12px; font-size: 22px; }}
    h3 {{ margin: 20px 0 8px; font-size: 15px; text-transform: uppercase; letter-spacing: 0; color: var(--muted); }}
    .summary {{ color: var(--muted); max-width: 560px; margin: 10px 0 0; }}
    .badge {{ background: var(--accent-soft); color: var(--accent); padding: 7px 10px; border-radius: 999px; font-weight: 700; white-space: nowrap; }}
    .card {{ background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 22px; margin-top: 18px; box-shadow: 0 10px 30px rgba(23, 33, 31, .05); }}
    .product-head {{ display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 18px; align-items: start; }}
    .rank {{ font-size: 14px; color: var(--muted); font-weight: 700; }}
    .score {{ text-align: right; }}
    .score strong {{ display: block; font-size: 30px; color: var(--accent); line-height: 1; }}
    .metrics {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); gap: 10px; margin: 18px 0 8px; }}
    .metric {{ border: 1px solid var(--line); border-radius: 8px; padding: 10px; min-width: 0; }}
    .metric span {{ display: block; color: var(--muted); font-size: 12px; }}
    .metric b {{ display: block; font-size: 16px; overflow-wrap: anywhere; }}
    .table-wrap {{ overflow-x: auto; border: 1px solid var(--line); border-radius: 8px; margin-bottom: 12px; }}
    table {{ width: 100%; border-collapse: collapse; min-width: 720px; background: #fff; }}
    th, td {{ padding: 10px 12px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; }}
    th {{ color: var(--muted); font-size: 12px; font-weight: 800; }}
    tbody tr:last-child td {{ border-bottom: 0; }}
    .empty {{ color: var(--muted); margin: 0 0 12px; }}
    .evidence {{ border: 1px solid var(--line); background: #fbfdfb; border-radius: 8px; padding: 12px 14px; margin: 10px 0 14px; }}
    .evidence b {{ color: var(--accent); }}
    .evidence ul {{ margin-top: 6px; }}
    .warning {{ border: 1px solid rgba(161, 75, 16, .25); background: rgba(161, 75, 16, .08); color: var(--warn); border-radius: 8px; padding: 12px 14px; margin: 10px 0 14px; }}
    .warning ul {{ margin-top: 6px; }}
    .grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 18px; }}
    .section {{ margin-top: 18px; }}
    .script-grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }}
    .script-box {{ border: 1px solid var(--line); border-radius: 8px; padding: 12px; background: #fbfdfb; }}
    .script-box h4 {{ margin: 0 0 8px; font-size: 14px; color: var(--accent); }}
    .decision {{ display: inline-flex; align-items: center; border-radius: 999px; padding: 8px 12px; font-weight: 900; background: var(--accent-soft); color: var(--accent); }}
    .live-panel {{ background: #fff; border: 1px solid var(--line); border-radius: 8px; padding: 18px; margin-bottom: 20px; }}
    .live-head {{ display: flex; justify-content: space-between; gap: 16px; align-items: center; margin-bottom: 12px; }}
    .live-head h2 {{ margin: 0; }}
    .live-status {{ color: var(--accent); background: var(--accent-soft); padding: 6px 10px; border-radius: 999px; font-weight: 900; }}
    .live-grid {{ display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 10px; }}
    .live-metric {{ border: 1px solid var(--line); border-radius: 8px; padding: 12px; background: #fbfdfb; }}
    .live-metric span {{ display: block; color: var(--muted); font-size: 12px; font-weight: 800; }}
    .live-metric b {{ display: block; font-size: 22px; margin-top: 4px; }}
    .action-grid {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; margin-top: 12px; }}
    .action-card {{ border: 1px solid var(--line); border-radius: 8px; padding: 10px; background: #fff; font-weight: 800; color: var(--muted); }}
    .action-card.active {{ border-color: var(--accent); background: var(--accent-soft); color: var(--accent); }}
    .suggestions {{ margin-top: 12px; border-left: 4px solid var(--accent); background: #f3f8f5; border-radius: 0 8px 8px 0; padding: 10px 14px; }}
    .suggestions b {{ color: var(--accent); }}
    .live-input {{ margin-top: 12px; }}
    .live-input textarea {{ width: 100%; min-height: 120px; resize: vertical; border: 1px solid var(--line); border-radius: 8px; padding: 12px; font: 13px/1.5 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; background: #fbfdfb; }}
    .manual-live-grid {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; margin-top: 8px; }}
    .manual-live-grid input {{ width: 100%; border: 1px solid var(--line); border-radius: 8px; padding: 9px; background: #fbfdfb; }}
    .decision-card {{ margin-top: 12px; border: 1px solid var(--line); border-radius: 8px; padding: 14px; background: #fff; }}
    .decision-card strong {{ display: block; font-size: 26px; color: var(--accent); }}
    .trend-grid {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 8px; margin-top: 12px; }}
    .trend-card {{ border: 1px solid var(--line); border-radius: 8px; padding: 10px; background: #fbfdfb; }}
    .trend-card span {{ display: block; color: var(--muted); font-size: 12px; font-weight: 800; }}
    .trend-up {{ color: #0c6b58; }}
    .trend-down {{ color: #9f2f22; }}
    .trend-stable {{ color: var(--muted); }}
    .host-assistant {{ position: fixed; top: 96px; right: 18px; width: 320px; max-height: calc(100vh - 120px); overflow-y: auto; z-index: 20; background: #fff; border: 1px solid var(--line); border-radius: 8px; box-shadow: 0 18px 50px rgba(23, 33, 31, .16); padding: 16px; }}
    .host-assistant h2 {{ margin: 0 0 10px; font-size: 18px; }}
    .host-product {{ border: 1px solid var(--line); border-radius: 8px; padding: 10px; background: #fbfdfb; margin-bottom: 10px; }}
    .host-product span, .host-row span {{ color: var(--muted); font-size: 12px; font-weight: 800; }}
    .host-product b {{ display: block; font-size: 16px; margin-top: 2px; }}
    .host-mini-grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }}
    .host-row {{ border: 1px solid var(--line); border-radius: 8px; padding: 8px; }}
    .host-row b {{ display: block; font-size: 15px; }}
    .host-advice {{ margin-top: 10px; border-left: 4px solid var(--accent); background: #f3f8f5; border-radius: 0 8px 8px 0; padding: 10px; }}
    .host-advice strong {{ display: block; color: var(--accent); font-size: 18px; margin-top: 4px; }}
    .next-sentence {{ margin-top: 10px; border: 1px solid var(--line); border-radius: 8px; padding: 10px; background: #fbfdfb; }}
    .next-sentence b {{ color: var(--accent); }}
    .engagement {{ margin-top: 10px; border: 1px solid var(--line); border-radius: 8px; padding: 10px; }}
    .engagement b {{ color: var(--accent); }}
    .engagement ul {{ margin-top: 6px; }}
    .order-panel {{ background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 18px; margin-bottom: 20px; }}
    .order-grid {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }}
    .order-item {{ border: 1px solid var(--line); border-radius: 8px; padding: 12px; background: #fbfdfb; }}
    .order-item span {{ display: block; color: var(--muted); font-size: 12px; font-weight: 800; }}
    .order-item b {{ display: block; margin: 4px 0; }}
    .question-table td:nth-child(2) {{ min-width: 260px; }}
    .live-comment-input textarea {{ width: 100%; min-height: 110px; resize: vertical; border: 1px solid var(--line); border-radius: 8px; padding: 12px; font: 14px/1.5 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }}
    ul {{ margin: 0; padding-left: 20px; }}
    li {{ margin: 4px 0; }}
    .script {{ border-left: 4px solid var(--accent); padding: 10px 14px; background: #f3f8f5; border-radius: 0 8px 8px 0; }}
    .negative {{ color: var(--warn); }}
    a.button {{ display: inline-flex; align-items: center; color: #fff; background: var(--accent); text-decoration: none; padding: 10px 14px; border-radius: 8px; font-weight: 800; }}
    @media (max-width: 900px) {{
      header, .product-head {{ grid-template-columns: 1fr; display: grid; }}
      .score {{ text-align: left; }}
      .metrics, .grid, .order-grid, .live-grid, .action-grid {{ grid-template-columns: 1fr; }}
      .host-assistant {{ position: static; width: auto; max-height: none; margin-bottom: 18px; }}
    }}
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <h1>AI Live Commerce Copilot v2</h1>
        <p class="summary">系统按真实市场证据、真实社媒证据、直播好卖程度、利润率、库存优先级和竞争强度综合排序。</p>
      </div>
      <a class="button" href="/">继续分析</a>
    </header>
    {_render_live_mode_dashboard()}
    {_render_host_assistant(products)}
    {_render_taobao_top_products(taobao_report)}
    {_render_livestream_order(livestream_order)}
    {_render_post_live_analysis(products)}
    {_render_audience_question_assistant(audience_answers)}
    {_render_live_comment_input()}
    {rows}
    {_live_mode_script(products)}
  </main>
</body>
</html>"""


def _build_prompt(products: list[ScoredProduct]) -> str:
    compact_products = []
    for product in products:
        knowledge = product.knowledge
        compact_products.append(
            {
                "product_name": product.product_name,
                "rank": product.rank,
                "score": round(product.score, 4),
                "cost": product.cost,
                "stock": product.stock,
                "target_selling_price": product.target_selling_price,
                "profit_margin": round(product.profit_margin, 4),
                "price_gap": round(product.price_gap, 2),
                "min_competitor_price": product.min_competitor_price,
                "max_competitor_price": product.max_competitor_price,
                "avg_competitor_price": product.avg_competitor_price,
                "price_results": [
                    {
                        "title": price.title,
                        "platform": price.platform,
                        "price": price.price,
                        "currency": price.currency,
                        "url": price.url,
                        "image_url": price.image_url,
                        "source": price.source,
                        "confidence": price.confidence,
                    }
                    for price in (product.market_research.price_results if product.market_research else [])
                ],
                "social_signals": [
                    {
                        "platform": signal.platform,
                        "popularity_score": signal.popularity_score,
                        "keywords": signal.keywords,
                        "positive_points": signal.positive_points,
                        "concerns": signal.concerns,
                        "evidence_urls": signal.evidence_urls,
                        "most_common_user_language": signal.most_common_user_language,
                        "evidence_posts": [
                            {
                                "post_title": post.post_title,
                                "likes": post.likes,
                                "favorites": post.favorites,
                                "comments": post.comments,
                                "cover_image": post.cover_image,
                                "real_url": post.real_url,
                                "top_comments": post.top_comments,
                            }
                            for post in signal.evidence_posts
                        ],
                        "confidence": signal.confidence,
                    }
                    for signal in (product.market_research.social_signals if product.market_research else [])
                ],
                "evidence_summary": product.market_research.evidence_summary if product.market_research else [],
                "warnings": product.market_research.warnings if product.market_research else [],
                "inventory_priority": round(product.inventory_priority, 4),
                "popularity_score": round(product.popularity_score, 4),
                "competition_score": product.competition_score,
                "sellability_score": product.sellability.score,
                "traffic_score": product.traffic_score,
                "conversion_score": product.conversion_score,
                "profit_score": product.profit_score,
                "gmv_score": product.gmv_score,
                "gmv_level": product.gmv_level,
                "sellability_factors": {
                    "daily_wear_suitability": product.sellability.daily_wear_suitability,
                    "mainstream_popularity": product.sellability.mainstream_popularity,
                    "conversion_difficulty": product.sellability.conversion_difficulty,
                    "price_accessibility": product.sellability.price_accessibility,
                    "gifting_potential": product.sellability.gifting_potential,
                },
                "ranking_explanation": product.ranking_explanation,
                "category": knowledge.category if knowledge else "专业户外产品",
                "similar_products": knowledge.similar_products if knowledge else [],
            }
        )

    return (
        "请根据 Google Shopping/SerpAPI 价格结果，以及抖音、小红书真实搜索证据，"
        "为这些始祖鸟库存商品生成中文直播选品建议。"
        "社媒证据只能引用真实搜索结果中的标题、链接和图片；没有真实社媒来源时必须承认没有证据，不要编造链接、点赞、收藏或评论。"
        "客户和主播都是中文用户，所有字段内容必须使用简体中文。"
        "请严格按结构化 JSON schema 返回。"
        "分数范围为 0 到 100。开场钩子约 15 秒，销售话术约 60 秒。"
        "host_decision 只能是 Push hard、Mention briefly、Skip for today 三者之一。"
        "排序逻辑：market_popularity 35%、sellability 30%、profit_margin 15%、inventory_priority 10%、competition_score 10%。"
        "必须优先考虑直播转化和主流需求，不要因为毛利高就过度推荐小众高客单产品。"
        "直播话术要像真实中文直播间，不要像广告文案，不要写 high-quality materials 这类泛词。"
        "不同产品必须使用不同风格：Atom=日常通勤，Beta=天气防护，Cerium=轻量保暖，Alpha=专业户外。避免每个商品重复同一句口头禅。"
        f"Products: {json.dumps(compact_products)}"
    )


def _report_json_schema() -> dict[str, Any]:
    script_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "opening_hook_15s": {"type": "string"},
            "selling_script_60s": {"type": "string"},
            "objection_handling": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "authenticity": {"type": "string"},
                    "sizing": {"type": "string"},
                    "price": {"type": "string"},
                },
                "required": ["authenticity", "sizing", "price"],
            },
            "comparison_script": {"type": "string"},
            "closing_urgency_line": {"type": "string"},
        },
        "required": [
            "opening_hook_15s",
            "selling_script_60s",
            "objection_handling",
            "comparison_script",
            "closing_urgency_line",
        ],
    }
    product_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "product_name": {"type": "string"},
            "product_heat_score": {"type": "number"},
            "user_concerns": {"type": "array", "items": {"type": "string"}},
            "core_selling_points": {"type": "array", "items": {"type": "string"}},
            "ai_livestream_script": script_schema,
            "recommendation_score": {"type": "number"},
            "host_decision": {
                "type": "string",
                "enum": ["Push hard", "Mention briefly", "Skip for today"],
            },
            "comparison_with_similar_products": {"type": "array", "items": {"type": "string"}},
            "suggested_image_evidence": {"type": "array", "items": {"type": "string"}},
        },
        "required": [
            "product_name",
            "product_heat_score",
            "user_concerns",
            "core_selling_points",
            "ai_livestream_script",
            "recommendation_score",
            "host_decision",
            "comparison_with_similar_products",
            "suggested_image_evidence",
        ],
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "products": {
                "type": "array",
                "items": product_schema,
            }
        },
        "required": ["products"],
    }


def _merge_report(product: ScoredProduct, reports: list[dict[str, Any]]) -> dict[str, Any]:
    fallback = _fallback_product_report(product)
    match = next((report for report in reports if report.get("product_name") == product.product_name), {})
    merged = fallback | {key: value for key, value in match.items() if value}
    merged["product_name"] = product.product_name
    return merged


def _fallback_product_report(product: ScoredProduct) -> dict[str, Any]:
    knowledge = product.knowledge
    similar = knowledge.similar_products if knowledge else ["同类防护款", "同类通勤款", "同类保暖款"]
    category = knowledge.category if knowledge else "专业户外产品"
    margin_label = f"{product.profit_margin:.1%}"
    market_label = _money(product.avg_competitor_price, "CNY") if product.avg_competitor_price else "暂无平台均价"
    heat_score = round(product.popularity_score * 100)
    recommendation_score = round(product.score * 100)
    concerns = [
        concern
        for signal in (product.market_research.social_signals if product.market_research else [])
        for concern in signal.concerns
    ]

    return {
        "product_name": product.product_name,
        "product_heat_score": heat_score,
        "recommendation_score": recommendation_score,
        "host_decision": _fallback_host_decision(product.score),
        "core_selling_points": knowledge.likely_selling_points
        if knowledge
        else ["始祖鸟技术产品定位清晰", "户外和通勤需求都容易理解", "适合直播间做场景化讲解"],
        "user_concerns": concerns[:3]
        if concerns
        else knowledge.likely_customer_concerns
        if knowledge
        else ["尺码是否合适", "使用场景是否匹配价格", "真伪和成色是否可靠"],
        "ai_livestream_script": _fallback_script(product, category, margin_label, market_label, similar),
        "comparison_with_similar_products": [
            f"和 {similar[0]} 对比时，重点讲使用场景和专业性能差异。",
            f"客户想要更轻便或更日常时，可以拿 {similar[1]} 做对比。",
            f"客户关心保暖、活动量或通勤穿搭时，可以提到 {similar[2]}。",
        ],
        "suggested_image_evidence": knowledge.suggested_image_evidence
        if knowledge
        else ["吊牌和型号近景", "面料和拉链细节", "正反面和上身图"],
    }


def _fallback_script(
    product: ScoredProduct,
    category: str,
    margin_label: str,
    market_label: str,
    similar: list[str],
) -> dict[str, Any]:
    normalized = product.product_name.lower()
    style = _script_style_template(normalized)
    return {
        "opening_hook_15s": (
            f"{style['lead']} {product.product_name}。{style['hook']}"
        ),
        "selling_script_60s": (
            f"{style['opening']} {style['focus']} 这件属于{category}。"
            f"现在库存 {product.stock} 件，平台参考均价大概 {market_label}。"
            f"{style['proof']} {style['close']}"
        ),
        "objection_handling": {
            "authenticity": style["authenticity"],
            "sizing": style["sizing"],
            "price": style["price"],
        },
        "comparison_script": (
            f"拿 {similar[0]}、{similar[1]} 比的话，我说实话，别只比名字。"
            f"你看专业程度、日常好不好穿、保暖或防护够不够。{similar[2]} 是另一种需求，不是所有人都适合。"
        ),
        "closing_urgency_line": style["urgency"],
    }


def _script_style_template(normalized_name: str) -> dict[str, str]:
    if "atom" in normalized_name:
        return {
            "lead": "日常通勤的先看这件",
            "hook": "早晚温差、开车、办公室、周末出门都能穿，不是买回去只挂衣柜的款。",
            "opening": "这件不用讲得太玄，核心就是轻、好搭、使用频率高。",
            "focus": "通勤穿不夸张，里面加 T 恤或薄卫衣都顺。",
            "proof": "镜头给到袖口、拉链、下摆和填充状态，日常磨损位置要看清楚。",
            "close": "想买第一件始祖鸟，又怕太专业穿不上，可以先从这类入手。",
            "authenticity": "真假别靠嘴说，直接拍吊牌、洗标、拉链头和压胶细节，通勤款最怕买到乱标。",
            "sizing": "Atom 按通勤穿法选，正常内搭按常规码；里面要加厚卫衣，就建议放大一码。",
            "price": "这件别只看单价，要看穿的次数；通勤能高频穿，单次成本会下来。",
            "urgency": "常用尺码先别等，通勤款走码最快，报身高体重我直接帮你卡尺码。",
        }
    if "beta" in normalized_name:
        return {
            "lead": "经常遇到风雨天气的看这件",
            "hook": "它不是为了显摆 logo，重点是下雨、刮风、出门办事的时候真能顶上。",
            "opening": "Beta 的讲法要围绕天气保护，不要把它讲成普通外套。",
            "focus": "通勤遇到风雨、周末轻户外、旅行带一件壳，都能解释得很清楚。",
            "proof": "镜头拉近看面料、帽檐、压胶、袖口和拉链，防护类产品证据要给足。",
            "close": "想要一件更万能的硬壳，Beta 比专业高山款更容易日常消化。",
            "authenticity": "硬壳看真伪重点拍压胶、洗标、拉链、防水细节和型号标，不要只拍正面。",
            "sizing": "Beta 要看里面加不加中间层；只穿 T 恤正常码，加抓绒或棉服建议留空间。",
            "price": "价格要和天气保护讲在一起，能挡风防雨的使用场景比普通外套多。",
            "urgency": "下雨季和常规黑灰色走得快，想看上身的直接扣 2。",
        }
    if "cerium" in normalized_name:
        return {
            "lead": "怕冷但不想穿得臃肿的看这件",
            "hook": "重点不是厚，是轻、暖、能收纳，冬天通勤和旅行都好带。",
            "opening": "Cerium 要用保暖和轻量来讲，不要一直堆参数。",
            "focus": "里面怎么搭、外面能不能再套壳、什么温度适合，直接给客户讲明白。",
            "proof": "镜头看蓬松度、领口、袖口、内侧标签和压缩状态，羽绒类一定要给细节。",
            "close": "怕冷又不想穿得像面包，这类轻量保暖层会更好成交。",
            "authenticity": "羽绒款看吊牌、洗标、填充标识和走线，细节拍清楚客户才敢下单。",
            "sizing": "Cerium 如果外面还要套壳，按正常码；里面想加厚内搭就别选太贴。",
            "price": "价格要和轻量保暖讲，别拿它和普通棉服硬比。",
            "urgency": "怕冷人群决策很快，热门尺码先锁，想问温度场景直接打出来。",
        }
    if "alpha" in normalized_name:
        return {
            "lead": "专业户外玩家重点看这件",
            "hook": "这不是普通通勤外套，预算和使用场景都要匹配，别盲拍。",
            "opening": "Alpha 要按专业场景讲，越坦诚越容易成交。",
            "focus": "重点讲高山、徒步、滑雪、恶劣天气和防护上限，不强行说人人都需要。",
            "proof": "镜头给压胶、帽型、面料、拉链和磨损位置，专业款必须用细节建立信任。",
            "close": "真有户外需求的人会懂它的价值，纯通勤客户可以引导去更日常的款。",
            "authenticity": "Alpha 客单高，必须拍型号标、洗标、压胶、拉链和所有容易磨损的位置。",
            "sizing": "专业硬壳要预留中间层空间，报身高体重还要问里面穿什么。",
            "price": "别把它讲成便宜，讲防护上限、使用场景和成色证据，预算不匹配就不要硬推。",
            "urgency": "专业客户看细节，细节确认没问题再下手，码数合适的可以直接锁。",
        }
    return {
        "lead": "这件先看使用场景",
        "hook": "适合什么人、什么天气、怎么搭配，讲清楚比喊价格更重要。",
        "opening": "这件不要泛泛讲品牌，先把人群和场景说清楚。",
        "focus": "重点讲它适合什么人、什么天气、什么穿法。",
        "proof": "镜头给吊牌、面料、拉链、袖口和成色细节。",
        "close": "场景匹配的人可以直接下手，不匹配就换下一件。",
        "authenticity": "真伪用吊牌、洗标、型号标和细节说话。",
        "sizing": "尺码按身高体重和内搭厚度判断，别只看 S/M/L。",
        "price": "价格要结合成色、平台参考价和使用频率讲。",
        "urgency": "合适的人先报尺码，我帮你判断要不要拍。",
    }


def _fallback_host_decision(score: float) -> str:
    if score >= 0.72:
        return "Push hard"
    if score >= 0.5:
        return "Mention briefly"
    return "Skip for today"


def _render_product_card(product: ScoredProduct, report: dict[str, Any]) -> str:
    return f"""
    <section class="card">
      <div class="product-head">
        <div>
          <div class="rank">排名 #{product.rank}</div>
          <h2>{html.escape(product.product_name)}</h2>
          <span class="badge">{html.escape(product.knowledge.category if product.knowledge else "专业产品")}</span>
        </div>
        <div class="score">
          <span>综合分</span>
          <strong>{product.score:.3f}</strong>
        </div>
      </div>
      <div class="metrics">
        {_metric("成本", _money(product.cost, "CNY"))}
        {_metric("目标售价", _money(product.target_selling_price, "CNY"))}
        {_metric("市场价差", _money(product.price_gap, "CNY"), "negative" if product.price_gap < 0 else "")}
        {_metric("毛利率", f"{product.profit_margin:.1%}", "negative" if product.profit_margin < 0 else "")}
        {_metric("库存", str(product.stock))}
        {_metric("平台热度", f"{product.popularity_score:.2f}")}
        {_metric("市场低价", _money_or_na(product.min_competitor_price, "CNY"))}
        {_metric("市场均价", _money_or_na(product.avg_competitor_price, "CNY"))}
        {_metric("市场高价", _money_or_na(product.max_competitor_price, "CNY"))}
        {_metric("商品热度分", str(report["product_heat_score"]))}
        {_metric("推荐分", str(report["recommendation_score"]))}
        {_metric("转化适配", f"{product.sellability.score:.2f}")}
        {_metric("日常穿着", f"{product.sellability.daily_wear_suitability:.2f}")}
        {_metric("转化难度", f"{product.sellability.conversion_difficulty:.2f}")}
        {_metric("竞争分", f"{product.competition_score:.2f}")}
        {_metric("GMV潜力", product.gmv_level)}
      </div>
      <div class="evidence"><b>Real data vs AI inferred data</b>{_real_vs_inferred(product)}</div>
      <div class="section">
        <h3>Why AI ranked this product</h3>
        {_list(product.ranking_explanation)}
      </div>
      <div class="section">
        <h3>Market Price Evidence</h3>
        {_market_price_table(product)}
      </div>
      <div class="section">
        <h3>Social Heat Signals</h3>
        {_social_heat_signals(product)}
      </div>
      <div class="section">
        <h3>AI Livestream Script</h3>
        {_ai_script(report)}
      </div>
      <div class="section">
        <h3>Host Decision</h3>
        <span class="decision">{html.escape(report["host_decision"])}（{_decision_label(report["host_decision"])}）</span>
      </div>
      {_evidence_summary(product)}
      {_warning_list(product)}
      <div class="grid">
        <div>
          <h3>核心卖点</h3>
          {_list(report["core_selling_points"])}
          <h3>用户顾虑</h3>
          {_list(report["user_concerns"])}
        </div>
        <div>
          <h3>同类始祖鸟产品对比</h3>
          {_list(report["comparison_with_similar_products"])}
        </div>
        <div>
          <h3>建议展示的图片证据</h3>
          {_list(report["suggested_image_evidence"])}
        </div>
      </div>
    </section>"""


def _metric(label: str, value: str, class_name: str = "") -> str:
    return f'<div class="metric"><span>{html.escape(label)}</span><b class="{class_name}">{html.escape(value)}</b></div>'


def _render_taobao_top_products(taobao_report: TaobaoTopProductsReport | None) -> str:
    if taobao_report is None:
        return ""

    warning_html = ""
    if taobao_report.warnings:
        warning_html = '<div class="warning"><b>淘宝商品池提示</b>' + _list(taobao_report.warnings) + "</div>"

    if not taobao_report.scores:
        return f"""
      <section class="order-panel">
        <h2>Today's Top Products</h2>
        {warning_html}
        <p class="empty">暂未拿到淘宝商品池数据。请在首页粘贴 Chrome DevTools 复制出来的淘宝 JSON；留空时才会尝试可选 API URL 模式。</p>
      </section>"""

    rows = "".join(
        f"""
        <tr>
          <td>#{index}</td>
          <td>{html.escape(score.product.source_product_title)}</td>
          <td>{html.escape(score.product.cat_name or "N/A")}</td>
          <td>{_money(score.product.price, "CNY")}</td>
          <td>{_money(score.advised_price, "CNY")}</td>
          <td>{score.product.sku_number}</td>
          <td>{score.profit_margin:.1%}</td>
          <td>{score.inventory_score:.2f}</td>
          <td>{score.sellability_score:.2f}</td>
          <td><b>{score.final_score:.3f}</b></td>
        </tr>"""
        for index, score in enumerate(taobao_report.scores[:8], start=1)
    )
    reasons = "".join(
        f"""
        <div class="script-box">
          <h4>#{index} {html.escape(score.product.source_product_title)}</h4>
          {_list(score.reason)}
        </div>"""
        for index, score in enumerate(taobao_report.scores[:4], start=1)
    )
    order = "".join(
        f"""
        <div class="order-item">
          <span>{html.escape(item.slot)}</span>
          <b>{html.escape(item.product_score.product.source_product_title)}</b>
          <p>{html.escape(item.reason)}</p>
        </div>"""
        for item in taobao_report.livestream_order
    )

    return f"""
      <section class="order-panel">
        <h2>Today's Top Products</h2>
        {warning_html}
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Rank</th>
                <th>Product title</th>
                <th>Category</th>
                <th>Cost price</th>
                <th>Suggested sale price</th>
                <th>SKU count</th>
                <th>Profit margin</th>
                <th>Inventory score</th>
                <th>Sellability</th>
                <th>Final score</th>
              </tr>
            </thead>
            <tbody>{rows}</tbody>
          </table>
        </div>
        <h3>Reason</h3>
        <div class="script-grid">{reasons}</div>
        <h3>Suggested livestream order</h3>
        <div class="order-grid">{order}</div>
      </section>"""


def _render_live_mode_dashboard() -> str:
    return """
      <section class="live-panel" id="live-mode-dashboard">
        <div class="live-head">
          <h2>Live Director Mode</h2>
          <span class="live-status" id="live-status">Mock livestream simulator · updates every 5s</span>
        </div>
        <div class="live-grid">
          <div class="live-metric"><span>Current viewers</span><b id="viewer-count">--</b></div>
          <div class="live-metric"><span>CTR</span><b id="ctr">--</b></div>
          <div class="live-metric"><span>CVR</span><b id="cvr">--</b></div>
          <div class="live-metric"><span>Add-to-cart rate</span><b id="cart-rate">--</b></div>
          <div class="live-metric"><span>Average watch duration</span><b id="watch-duration">--</b></div>
          <div class="live-metric"><span>AI Score</span><b id="ai-live-score">--</b></div>
          <div class="live-metric"><span>Decision</span><b id="ai-live-decision">--</b></div>
          <div class="live-metric"><span>Item</span><b id="live-item-name">--</b></div>
          <div class="live-metric"><span>Item GMV</span><b id="live-item-gmv">--</b></div>
          <div class="live-metric"><span>讲解效果</span><b id="live-jiangjie-effect">--</b></div>
        </div>
        <div class="action-grid">
          <div class="action-card" id="action-continue">Continue selling</div>
          <div class="action-card" id="action-switch">Switch product</div>
          <div class="action-card" id="action-push">Push harder</div>
          <div class="action-card" id="action-sizing">Explain sizing</div>
          <div class="action-card" id="action-auth">Show authenticity proof</div>
          <div class="action-card" id="action-skip">Skip product</div>
          <div class="action-card" id="action-topic">Change topic</div>
        </div>
        <div class="suggestions">
          <b>AI Decision Engine</b>
          <ul id="host-suggestions">
            <li>等待模拟数据...</li>
          </ul>
        </div>
        <div class="decision-card">
          <span>Decision card</span>
          <strong id="decision-card-decision">--</strong>
          <div id="decision-card-reason">等待数据...</div>
          <div><b>Next sentence</b>: <span id="decision-card-sentence">--</span></div>
          <div><b>Current action</b>: <span id="decision-card-action">--</span></div>
          <div><b>Recommended next product</b>: <span id="decision-card-next-product">--</span></div>
        </div>
        <div class="trend-grid">
          <div class="trend-card"><span>AI Score 30s</span><b id="trend-ai-30">--</b></div>
          <div class="trend-card"><span>AI Score 60s</span><b id="trend-ai-60">--</b></div>
          <div class="trend-card"><span>Item CTR 30s</span><b id="trend-click-30">--</b></div>
          <div class="trend-card"><span>Watch 30s</span><b id="trend-watch-30">--</b></div>
          <div class="trend-card"><span>Cart 30s</span><b id="trend-cart-30">--</b></div>
          <div class="trend-card"><span>Conversion 60s</span><b id="trend-conv-60">--</b></div>
          <div class="trend-card"><span>Item GMV 60s</span><b id="trend-gmv-60">--</b></div>
        </div>
        <div class="decision-card">
          <span>Recommended queue</span>
          <div class="order-grid" id="live-director-queue">
            <div class="order-item"><span>Now</span><b>--</b></div>
            <div class="order-item"><span>Next</span><b>--</b></div>
            <div class="order-item"><span>Then</span><b>--</b></div>
            <div class="order-item"><span>Final</span><b>--</b></div>
          </div>
        </div>
        <div class="live-input">
          <b>Paste mtop.taobao.tblive.portal.live.user.assistant.data.get JSON</b>
          <textarea id="live-assistant-data-input" spellcheck="false" placeholder='{"data":{"online_uv":520,"pv":12000,"uv":2100,"stay_time_pu":68,"pay_byr_rate":0.025,"pay_buyer_cnt":18,"pay_item_qty":24,"pay_amt":12880,"heat_score":78,"ipv_uv_rate":0.12,"comment_uv":36,"refund_amt":0,"atn_uv":45,"item_name":"当前商品","item_click_rate":0.18,"item_conversion_rate":0.035,"item_add_cart_rate":0.08,"item_gmv":6800,"jiangJieEffect":82}}'></textarea>
        </div>
        <div class="live-input">
          <b>Manual fallback mode</b>
          <div class="manual-live-grid">
            <input id="manual-online-uv" placeholder="online_uv">
            <input id="manual-ipv-uv-rate" placeholder="ipv_uv_rate">
            <input id="manual-pay-byr-rate" placeholder="pay_byr_rate">
            <input id="manual-heat-score" placeholder="heat_score">
            <input id="manual-pay-amt" placeholder="pay_amt">
            <input id="manual-stay-time-pu" placeholder="stay_time_pu">
          </div>
        </div>
      </section>"""


def _render_host_assistant(products: list[ScoredProduct]) -> str:
    current_product = products[0] if products else None
    product_name = current_product.product_name if current_product else "等待商品"
    gmv_level = current_product.gmv_level if current_product else "--"
    return f"""
      <aside class="host-assistant" id="host-assistant">
        <h2>Host Assistant Mode</h2>
        <div class="host-product">
          <span>Current product</span>
          <b id="host-current-product">{html.escape(product_name)}</b>
        </div>
        <div class="host-mini-grid">
          <div class="host-row"><span>Current viewers</span><b id="host-viewer-count">--</b></div>
          <div class="host-row"><span>CTR</span><b id="host-ctr">--</b></div>
          <div class="host-row"><span>CVR</span><b id="host-cvr">--</b></div>
          <div class="host-row"><span>Watch duration</span><b id="host-watch-duration">--</b></div>
          <div class="host-row"><span>AI Score</span><b id="host-ai-score">--</b></div>
          <div class="host-row"><span>Decision</span><b id="host-ai-decision">--</b></div>
          <div class="host-row"><span>GMV score</span><b id="host-gmv-score">{html.escape(gmv_level)}</b></div>
          <div class="host-row"><span>Mode</span><b>Live</b></div>
        </div>
        <div class="host-advice">
          <span>Realtime AI suggestion</span>
          <strong id="host-primary-suggestion">等待模拟数据...</strong>
        </div>
        <div class="next-sentence">
          <b>Next sentence</b>
          <div id="host-next-sentence">很多人问尺码</div>
        </div>
        <div class="engagement">
          <b>Engagement suggestions</b>
          <ul id="host-engagement-suggestions">
            <li>评论区扣1</li>
            <li>想看上身扣2</li>
          </ul>
        </div>
      </aside>"""


def _live_mode_script(products: list[ScoredProduct]) -> str:
    live_products = [
        {
            "name": product.product_name,
            "score": round(product.score, 4),
            "inventory": product.stock,
            "profit_margin": round(product.profit_margin, 4),
            "rank": product.rank,
        }
        for product in products
    ]
    script = """
    <script>
      const liveProducts = __LIVE_PRODUCTS__;
      const hostProducts = Array.from(document.querySelectorAll(".card h2")).map(function(node) {
        return node.textContent.trim();
      });
      let hostProductIndex = 0;
      const nextSentenceOptions = [
        "很多人问尺码",
        "别光看价格",
        "平时通勤穿也完全够",
        "镜头拉近看细节"
      ];
      const engagementOptions = [
        "评论区扣1",
        "想看上身扣2",
        "175/70打身高",
        "想看黑色扣3"
      ];
      const liveMetricHistory = [];

      function randomBetween(min, max) {
        return Math.random() * (max - min) + min;
      }

      function formatPercent(value) {
        return (value * 100).toFixed(1) + "%";
      }

      function clamp(value, min, max) {
        return Math.max(min, Math.min(max, value));
      }

      function toNumber(value) {
        if (value === null || value === undefined || value === "") {
          return 0;
        }
        if (typeof value === "number") {
          return Number.isFinite(value) ? value : 0;
        }
        const text = String(value).replace(/,/g, "").replace(/%/g, "").trim();
        const parsed = Number(text);
        if (!Number.isFinite(parsed)) {
          return 0;
        }
        return String(value).includes("%") ? parsed / 100 : parsed;
      }

      function normalizeRate(value) {
        const numeric = toNumber(value);
        if (numeric > 1) {
          return clamp(numeric / 100, 0, 1);
        }
        return clamp(numeric, 0, 1);
      }

      function normalizeHeatScore(value) {
        const numeric = toNumber(value);
        if (numeric > 1) {
          return clamp(numeric / 100, 0, 1);
        }
        return clamp(numeric, 0, 1);
      }

      function normalizeStayTime(value) {
        const numeric = toNumber(value);
        if (numeric <= 1) {
          return clamp(numeric, 0, 1);
        }
        return clamp(numeric / 180, 0, 1);
      }

      function normalizeAudienceCount(value, onlineUv) {
        const count = toNumber(value);
        const base = Math.max(1, toNumber(onlineUv));
        return clamp(count / base, 0, 1);
      }

      function normalizeRefundAmount(refundAmt, payAmt) {
        const refund = Math.max(0, toNumber(refundAmt));
        const paid = Math.max(1, toNumber(payAmt));
        return clamp(refund / paid, 0, 1);
      }

      function normalizeMoney(value) {
        return clamp(toNumber(value) / 20000, 0, 1);
      }

      function formatMoney(value) {
        return "¥" + Math.round(toNumber(value)).toLocaleString();
      }

      function escapeHtml(text) {
        const node = document.createElement("div");
        node.textContent = String(text || "");
        return node.innerHTML;
      }

      function normalizeName(text) {
        return String(text || "").toLowerCase().replace(/[^a-z0-9\\u4e00-\\u9fa5]+/g, "");
      }

      function currentLiveProduct(itemName) {
        if (!liveProducts.length) {
          return { name: itemName || "当前商品", score: 0.5, inventory: 0, profit_margin: 0 };
        }
        const normalizedItem = normalizeName(itemName);
        const matched = liveProducts.find(function(product) {
          const normalizedProduct = normalizeName(product.name);
          return normalizedItem && (normalizedItem.includes(normalizedProduct) || normalizedProduct.includes(normalizedItem));
        });
        return matched || liveProducts[hostProductIndex] || liveProducts[0];
      }

      function productContext(metrics) {
        const product = currentLiveProduct(metrics.item_name);
        const inventory = metrics.inventory || product.inventory || 0;
        const profitMargin = metrics.profit_margin || product.profit_margin || 0;
        return {
          product: product,
          inventory: inventory,
          inventory_score: clamp(inventory / 50, 0, 1),
          profit_margin: profitMargin,
          profit_score: clamp(profitMargin, 0, 1)
        };
      }

      function computeProductScore(metrics) {
        const context = productContext(metrics);
        const clickRate = metrics.item_click_rate || metrics.ipv_uv_rate || 0;
        const conversionRate = metrics.item_conversion_rate || metrics.pay_byr_rate || 0;
        return clamp(
          metrics.heat_score * 0.20
          + clickRate * 0.20
          + conversionRate * 0.25
          + (metrics.item_add_cart_rate || metrics.cart_rate || 0) * 0.15
          + normalizeStayTime(metrics.watch_time) * 0.10
          + normalizeMoney(metrics.item_gmv || metrics.pay_amt || 0) * 0.05
          + context.inventory_score * 0.03
          + context.profit_score * 0.02,
          0,
          1
        );
      }

      function enrichMetrics(metrics) {
        const context = productContext(metrics);
        metrics.inventory = metrics.inventory || context.inventory;
        metrics.profit_margin = metrics.profit_margin || context.profit_margin;
        metrics.current_product_score = computeProductScore(metrics);
        return metrics;
      }

      function findAssistantData(payload) {
        if (!payload || typeof payload !== "object") {
          return null;
        }
        if (payload.data && typeof payload.data === "object") {
          if (payload.data.data && typeof payload.data.data === "object") {
            return payload.data.data;
          }
          if (payload.data.result && typeof payload.data.result === "object") {
            return payload.data.result;
          }
          return payload.data;
        }
        return payload;
      }

      function readAssistantPayload() {
        const input = document.getElementById("live-assistant-data-input");
        if (!input || !input.value.trim()) {
          return null;
        }
        try {
          const payload = JSON.parse(input.value);
          return findAssistantData(payload);
        } catch (error) {
          return { parse_error: true, error_message: error.message };
        }
      }

      function readManualLiveMetrics() {
        const fields = {
          online_uv: document.getElementById("manual-online-uv"),
          ipv_uv_rate: document.getElementById("manual-ipv-uv-rate"),
          pay_byr_rate: document.getElementById("manual-pay-byr-rate"),
          heat_score: document.getElementById("manual-heat-score"),
          pay_amt: document.getElementById("manual-pay-amt"),
          stay_time_pu: document.getElementById("manual-stay-time-pu")
        };
        const hasAny = Object.keys(fields).some(function(key) {
          return fields[key] && fields[key].value.trim();
        });
        if (!hasAny) {
          return null;
        }
        return {
          online_uv: fields.online_uv.value,
          ipv_uv_rate: fields.ipv_uv_rate.value,
          pay_byr_rate: fields.pay_byr_rate.value,
          heat_score: fields.heat_score.value,
          pay_amt: fields.pay_amt.value,
          stay_time_pu: fields.stay_time_pu.value,
          refund_amt: 0,
          atn_uv: 0,
          manual_fallback: true
        };
      }

      function buildMetricsFromAssistantData(data) {
        const onlineUv = toNumber(data.online_uv);
        const pv = toNumber(data.pv);
        const uv = toNumber(data.uv);
        const ipvUvRate = normalizeRate(data.ipv_uv_rate);
        const payByrRate = normalizeRate(data.pay_byr_rate);
        const stayTime = toNumber(data.stay_time_pu || data.watch_duration);
        const commentUv = toNumber(data.comment_uv);
        const atnUv = toNumber(data.atn_uv);
        const payAmt = toNumber(data.pay_amt);
        const refundAmt = toNumber(data.refund_amt);
        const itemName = String(data.item_name || data.itemName || data.item_title || "").trim();
        const itemClickRate = normalizeRate(data.item_click_rate);
        const itemConversionRate = normalizeRate(data.item_conversion_rate);
        const itemAddCartRate = normalizeRate(data.item_add_cart_rate);
        const itemGmv = toNumber(data.item_gmv);
        const jiangJieEffect = toNumber(data.jiangJieEffect);
        const heat = normalizeHeatScore(data.heat_score);
        const stay = normalizeStayTime(data.stay_time_pu || data.watch_duration);
        const attention = normalizeAudienceCount(data.atn_uv, onlineUv || uv);
        const refund = normalizeRefundAmount(data.refund_amt, data.pay_amt);
        const score = (
          heat * 0.25
          + ipvUvRate * 0.25
          + payByrRate * 0.25
          + stay * 0.10
          + attention * 0.05
          - refund * 0.10
        );
        return enrichMetrics({
          viewer_count: onlineUv || uv,
          pv: pv,
          uv: uv,
          ctr: ipvUvRate,
          cvr: payByrRate,
          cart_rate: itemAddCartRate || ipvUvRate,
          watch_time: stayTime,
          pay_buyer_cnt: toNumber(data.pay_buyer_cnt),
          pay_item_qty: toNumber(data.pay_item_qty),
          pay_amt: payAmt,
          heat_score_raw: toNumber(data.heat_score),
          heat_score: heat,
          ipv_uv_rate: ipvUvRate,
          pay_byr_rate: payByrRate,
          comment_uv: commentUv,
          refund_amt: refundAmt,
          refund_amt_normalized: refund,
          atn_uv: atnUv,
          atn_uv_normalized: attention,
          item_name: itemName,
          item_click_rate: itemClickRate,
          item_conversion_rate: itemConversionRate,
          item_add_cart_rate: itemAddCartRate,
          item_gmv: itemGmv,
          jiangJieEffect: jiangJieEffect,
          inventory: toNumber(data.inventory),
          profit_margin: normalizeRate(data.profit_margin),
          authenticity_questions: toNumber(data.authenticity_questions || data.auth_questions),
          sizing_questions: toNumber(data.sizing_questions || data.size_questions),
          ai_score: clamp(score, 0, 1),
          source: data.manual_fallback ? "manual" : "real"
        });
      }

      function calculateDecision(metrics, trends) {
        const clickRate = metrics.item_click_rate || metrics.ipv_uv_rate || 0;
        const conversionRate = metrics.item_conversion_rate || metrics.pay_byr_rate || 0;
        const addCartRate = metrics.item_add_cart_rate || metrics.cart_rate || 0;
        const score = metrics.current_product_score || metrics.ai_score || 0;
        const ctrUp = trends && trends.click30.direction === "up";
        const watchUp = trends && trends.watch30.direction === "up";
        const cartUp = trends && trends.cart30.direction === "up";
        const ctrDown = trends && trends.click30.direction === "down";
        const watchDown = trends && trends.watch30.direction === "down";
        const conversionDown = trends && trends.conv60.direction === "down";

        if (metrics.sizing_questions > 3) {
          return "Explain sizing";
        }
        if (metrics.authenticity_questions > 3 || (metrics.comment_uv > 25 && conversionRate < 0.025)) {
          return "Show authenticity";
        }
        if (clickRate >= 0.07 && conversionRate < 0.02) {
          return "Explain value/price";
        }
        if (ctrUp && watchUp && cartUp) {
          return "Continue product";
        }
        if (ctrDown && watchDown && conversionDown) {
          return "Switch product";
        }
        if (score < 0.24 && metrics.watch_time < 25 && conversionRate < 0.01) {
          return "Skip product";
        }
        if (addCartRate >= 0.05 || (score >= 0.62 && conversionRate >= 0.025)) {
          return "Push harder";
        }
        if (metrics.watch_time < 30) {
          return "Change topic";
        }
        return "Continue product";
      }

      function metricContributions(metrics) {
        const rows = [
          { label: "heat_score", value: metrics.heat_score * 0.25 },
          { label: "ipv_uv_rate", value: metrics.ipv_uv_rate * 0.25 },
          { label: "pay_byr_rate", value: metrics.pay_byr_rate * 0.25 },
          { label: "item_click_rate", value: (metrics.item_click_rate || 0) * 0.18 },
          { label: "item_conversion_rate", value: (metrics.item_conversion_rate || 0) * 0.18 },
          { label: "item_gmv", value: normalizeMoney(metrics.item_gmv || 0) * 0.12 },
          { label: "stay_time_pu", value: normalizeStayTime(metrics.watch_time) * 0.10 },
          { label: "atn_uv", value: metrics.atn_uv_normalized * 0.05 },
          { label: "refund_amt drag", value: -metrics.refund_amt_normalized * 0.10 }
        ];
        return rows
          .sort(function(a, b) { return Math.abs(b.value) - Math.abs(a.value); })
          .slice(0, 3);
      }

      function nextHostSentence(decision, metrics) {
        if (decision === "Switch product") {
          return "这件先放一下，哥几个我们切下一件更好成交的。";
        }
        if (decision === "Explain sizing") {
          return "很多人点进来看了，我先把尺码和上身效果讲清楚。";
        }
        if (decision === "Show authenticity") {
          return "镜头拉近，吊牌、洗标和细节我直接给大家看。";
        }
        if (decision === "Explain value/price") {
          return "别光看价格，我说实话，这件贵在哪、适合谁我直接讲明白。";
        }
        if (decision === "Skip product") {
          return "这件今天先不硬推，库存和反馈不够好，我们换一件更好卖的。";
        }
        if (decision === "Change topic") {
          return "先别急着拍，我换个场景讲，平时通勤到底能不能穿。";
        }
        if (decision === "Push harder") {
          if (metrics.pay_byr_rate > 0.03) {
            return "现在已经有人在下单了，尺码合适的先锁，等下热门码会断。";
          }
          return "这波热度起来了，别光看价格，我把值在哪讲清楚。";
        }
        if (metrics.ipv_uv_rate > 0.08 && metrics.pay_byr_rate < 0.02) {
          return "很多人点进来看了但还没拍，我直接讲价格和使用场景。";
        }
        if (metrics.comment_uv > 20) {
          return "评论区问题很多，我先集中回答尺码和真假。";
        }
        return "继续看细节，镜头拉近一点，吊牌和做工给大家看清楚。";
      }

      function currentActionText(decision) {
        const actions = {
          "Continue product": "继续讲当前商品",
          "Switch product": "切换下一件",
          "Push harder": "加速逼单",
          "Skip product": "跳过这件",
          "Change topic": "换话题保停留",
          "Explain value/price": "解释价值和价格",
          "Explain sizing": "开始讲尺码",
          "Show authenticity": "展示吊牌和洗标"
        };
        return actions[decision] || "继续观察";
      }

      function buildDecisionReasons(metrics, trends) {
        const decision = calculateDecision(metrics, trends);
        const reasons = metricContributions(metrics).map(function(row) {
          return row.label + ": " + (row.value >= 0 ? "+" : "") + (row.value * 100).toFixed(1);
        });
        return {
          decision: decision,
          reasons: reasons.slice(0, 3),
          sentence: nextHostSentence(decision, metrics),
          action: currentActionText(decision)
        };
      }

      function setActiveAction(actionId) {
        ["action-continue", "action-switch", "action-push", "action-sizing", "action-auth", "action-skip", "action-topic"].forEach(function(id) {
          document.getElementById(id).classList.toggle("active", id === actionId);
        });
      }

      function buildSuggestions(metrics, trends) {
        const decision = buildDecisionReasons(metrics, trends);
        const suggestions = [
          "Decision: " + decision.decision,
          "Reason: " + decision.reasons.join(" / "),
          "Next host sentence: " + decision.sentence
        ];
        return suggestions;
      }

      function chooseHostSuggestion(metrics) {
        if (metrics.watch_time < 30) {
          return "Switch product";
        }
        if (metrics.authenticity_questions > 3) {
          return "Show tag and details";
        }
        if (metrics.sizing_questions > 3) {
          return "Explain sizing";
        }
        if (metrics.ctr > 0.07 && metrics.cvr < 0.02) {
          return "Explain value and pricing";
        }
        if (metrics.cart_rate > 0.05) {
          return "制造紧迫感";
        }
        if (metrics.ctr >= 0.045 && metrics.cvr >= 0.025 && metrics.watch_time >= 50) {
          return "继续讲90秒";
        }
        return "继续讲90秒";
      }

      function chooseAction(decision) {
        if (decision === "Switch product") {
          return "action-switch";
        }
        if (decision === "Push harder") {
          return "action-push";
        }
        if (decision === "Explain sizing") {
          return "action-sizing";
        }
        if (decision === "Show authenticity") {
          return "action-auth";
        }
        if (decision === "Skip product") {
          return "action-skip";
        }
        if (decision === "Change topic" || decision === "Explain value/price") {
          return "action-topic";
        }
        return "action-continue";
      }

      function simulateLiveMetrics() {
        const realPayload = readAssistantPayload();
        if (realPayload && realPayload.parse_error) {
          document.getElementById("live-status").textContent = "Live assistant JSON parse error: " + realPayload.error_message;
          return;
        }
        if (realPayload) {
          renderLiveDecision(buildMetricsFromAssistantData(realPayload));
          return;
        }
        const manualPayload = readManualLiveMetrics();
        if (manualPayload) {
          renderLiveDecision(buildMetricsFromAssistantData(manualPayload));
          return;
        }
        const liveProduct = currentLiveProduct(hostProducts[hostProductIndex] || "当前商品");
        const metrics = {
          viewer_count: Math.round(randomBetween(180, 980)),
          pv: Math.round(randomBetween(2000, 18000)),
          uv: Math.round(randomBetween(500, 5000)),
          ctr: randomBetween(0.018, 0.085),
          cvr: randomBetween(0.006, 0.052),
          cart_rate: randomBetween(0.012, 0.095),
          watch_time: randomBetween(24, 135),
          pay_amt: randomBetween(0, 20000),
          item_name: hostProducts[hostProductIndex] || "当前商品",
          item_click_rate: randomBetween(0.02, 0.16),
          item_conversion_rate: randomBetween(0.006, 0.06),
          item_add_cart_rate: randomBetween(0.012, 0.10),
          item_gmv: randomBetween(0, 16000),
          jiangJieEffect: randomBetween(35, 95),
          heat_score: randomBetween(0.35, 0.95),
          heat_score_raw: randomBetween(35, 95),
          ipv_uv_rate: 0,
          pay_byr_rate: 0,
          comment_uv: Math.round(randomBetween(0, 80)),
          refund_amt: randomBetween(0, 600),
          refund_amt_normalized: 0,
          atn_uv: Math.round(randomBetween(0, 120)),
          atn_uv_normalized: 0,
          inventory: liveProduct.inventory || 0,
          profit_margin: liveProduct.profit_margin || 0,
          authenticity_questions: Math.round(randomBetween(0, 7)),
          sizing_questions: Math.round(randomBetween(0, 7)),
          source: "mock"
        };
        metrics.ipv_uv_rate = metrics.ctr;
        metrics.pay_byr_rate = metrics.cvr;
        metrics.atn_uv_normalized = normalizeAudienceCount(metrics.atn_uv, metrics.viewer_count);
        metrics.refund_amt_normalized = normalizeRefundAmount(metrics.refund_amt, metrics.pay_amt);
        metrics.ai_score = clamp(
          metrics.heat_score * 0.25
          + metrics.ipv_uv_rate * 0.25
          + metrics.pay_byr_rate * 0.25
          + normalizeStayTime(metrics.watch_time) * 0.10
          + metrics.atn_uv_normalized * 0.05
          - metrics.refund_amt_normalized * 0.10,
          0,
          1
        );
        renderLiveDecision(enrichMetrics(metrics));
      }

      function renderLiveDecision(metrics) {
        enrichMetrics(metrics);
        pushMetricHistory(metrics);
        const trends = buildTrendSummary(metrics);
        const decision = buildDecisionReasons(metrics, trends);
        const queue = buildRecommendedQueue(metrics, decision);
        document.getElementById("live-status").textContent = (
          metrics.source === "real"
            ? "Using real Taobao live assistant data · updates every 5s"
            : metrics.source === "manual"
            ? "Using manual fallback metrics · updates every 5s"
            : "Mock livestream simulator · updates every 5s"
        );
        document.getElementById("viewer-count").textContent = metrics.viewer_count.toLocaleString();
        document.getElementById("ctr").textContent = formatPercent(metrics.ipv_uv_rate);
        document.getElementById("cvr").textContent = formatPercent(metrics.pay_byr_rate);
        document.getElementById("cart-rate").textContent = formatPercent(metrics.cart_rate);
        document.getElementById("watch-duration").textContent = Math.round(metrics.watch_time) + "s";
        document.getElementById("ai-live-score").textContent = Math.round(metrics.current_product_score * 100);
        document.getElementById("ai-live-decision").textContent = decision.decision;
        document.getElementById("live-item-name").textContent = metrics.item_name || "当前商品";
        document.getElementById("live-item-gmv").textContent = formatMoney(metrics.item_gmv || metrics.pay_amt || 0);
        document.getElementById("live-jiangjie-effect").textContent = metrics.jiangJieEffect ? Math.round(metrics.jiangJieEffect) : "--";
        updateTrendCards(trends);
        updateDecisionCard(decision, queue);

        setActiveAction(chooseAction(decision.decision));
        updateHostAssistant(metrics, decision);
        updateRecommendedQueue(queue);

        const suggestionList = document.getElementById("host-suggestions");
        suggestionList.innerHTML = buildSuggestions(metrics, trends)
          .map(function(text) { return "<li>" + text + "</li>"; })
          .join("");
      }

      function pushMetricHistory(metrics) {
        liveMetricHistory.push({ ts: Date.now(), metrics: Object.assign({}, metrics) });
        const cutoff = Date.now() - 5 * 60 * 1000;
        while (liveMetricHistory.length && liveMetricHistory[0].ts < cutoff) {
          liveMetricHistory.shift();
        }
      }

      function metricSnapshotAgo(seconds) {
        const target = Date.now() - seconds * 1000;
        let candidate = null;
        liveMetricHistory.forEach(function(point) {
          if (point.ts <= target) {
            candidate = point.metrics;
          }
        });
        return candidate;
      }

      function trendArrow(current, previous) {
        if (!previous && previous !== 0) {
          return { label: "--", className: "trend-stable", direction: "unknown" };
        }
        const diff = current - previous;
        const threshold = Math.max(0.003, Math.abs(previous) * 0.05);
        if (diff > threshold) {
          return { label: "up ↑", className: "trend-up", direction: "up" };
        }
        if (diff < -threshold) {
          return { label: "down ↓", className: "trend-down", direction: "down" };
        }
        return { label: "stable →", className: "trend-stable", direction: "stable" };
      }

      function buildTrendSummary(metrics) {
        const ago30 = metricSnapshotAgo(30);
        const ago60 = metricSnapshotAgo(60);
        return {
          ai30: trendArrow(metrics.current_product_score, ago30 && ago30.current_product_score),
          ai60: trendArrow(metrics.current_product_score, ago60 && ago60.current_product_score),
          click30: trendArrow(metrics.item_click_rate || metrics.ipv_uv_rate, ago30 && (ago30.item_click_rate || ago30.ipv_uv_rate)),
          watch30: trendArrow(normalizeStayTime(metrics.watch_time), ago30 && normalizeStayTime(ago30.watch_time)),
          cart30: trendArrow(metrics.item_add_cart_rate || metrics.cart_rate, ago30 && (ago30.item_add_cart_rate || ago30.cart_rate)),
          conv60: trendArrow(metrics.item_conversion_rate || metrics.pay_byr_rate, ago60 && (ago60.item_conversion_rate || ago60.pay_byr_rate)),
          gmv60: trendArrow(metrics.item_gmv || metrics.pay_amt, ago60 && (ago60.item_gmv || ago60.pay_amt))
        };
      }

      function updateTrendCards(trends) {
        setTrend("trend-ai-30", trends.ai30);
        setTrend("trend-ai-60", trends.ai60);
        setTrend("trend-click-30", trends.click30);
        setTrend("trend-watch-30", trends.watch30);
        setTrend("trend-cart-30", trends.cart30);
        setTrend("trend-conv-60", trends.conv60);
        setTrend("trend-gmv-60", trends.gmv60);
      }

      function setTrend(id, trend) {
        const node = document.getElementById(id);
        node.textContent = trend.label;
        node.className = trend.className;
      }

      function buildRecommendedQueue(metrics, decision) {
        const current = currentLiveProduct(metrics.item_name);
        const sorted = liveProducts.slice().sort(function(a, b) {
          const aProfit = a.profit_margin || 0;
          const bProfit = b.profit_margin || 0;
          return (b.score + bProfit * 0.15) - (a.score + aProfit * 0.15);
        });
        const others = sorted.filter(function(product) { return product.name !== current.name; });
        let queue = [current].concat(others);
        if (decision.decision === "Switch product" || decision.decision === "Skip product") {
          queue = others.concat([current]);
        }
        while (queue.length < 4 && queue.length) {
          queue.push(queue[queue.length - 1]);
        }
        return queue.slice(0, 4).map(function(product, index) {
          const labels = ["Now", "Next", "Then", "Final"];
          return { label: labels[index], product: product };
        });
      }

      function updateDecisionCard(decision, queue) {
        document.getElementById("decision-card-decision").textContent = decision.decision;
        document.getElementById("decision-card-reason").textContent = decision.reasons.join(" / ");
        document.getElementById("decision-card-sentence").textContent = decision.sentence;
        document.getElementById("decision-card-action").textContent = decision.action;
        document.getElementById("decision-card-next-product").textContent = queue[1] ? queue[1].product.name : "--";
      }

      function updateRecommendedQueue(queue) {
        const node = document.getElementById("live-director-queue");
        if (!node) {
          return;
        }
        if (!queue.length) {
          node.innerHTML = '<div class="order-item"><span>Now</span><b>--</b></div>';
          return;
        }
        node.innerHTML = queue.map(function(item) {
          return '<div class="order-item"><span>' + escapeHtml(item.label) + '</span><b>' + escapeHtml(item.product.name) + '</b></div>';
        }).join("");
      }

      function updateHostAssistant(metrics, decision) {
        decision = decision || buildDecisionReasons(metrics);
        if (hostProducts.length && (decision.decision === "Switch product" || decision.decision === "Skip product")) {
          hostProductIndex = Math.min(hostProductIndex + 1, hostProducts.length - 1);
        }
        const currentProduct = (decision.decision === "Switch product" || decision.decision === "Skip product")
          ? (hostProducts[hostProductIndex] || metrics.item_name || "等待商品")
          : (metrics.item_name || hostProducts[hostProductIndex] || "等待商品");
        document.getElementById("host-current-product").textContent = currentProduct;
        document.getElementById("host-viewer-count").textContent = metrics.viewer_count.toLocaleString();
        document.getElementById("host-ctr").textContent = formatPercent(metrics.ipv_uv_rate);
        document.getElementById("host-cvr").textContent = formatPercent(metrics.pay_byr_rate);
        document.getElementById("host-watch-duration").textContent = Math.round(metrics.watch_time) + "s";
        document.getElementById("host-ai-score").textContent = Math.round(metrics.current_product_score * 100);
        document.getElementById("host-ai-decision").textContent = decision.decision;
        document.getElementById("host-primary-suggestion").textContent = decision.decision;
        document.getElementById("host-next-sentence").textContent = decision.sentence;
        const shuffled = engagementOptions.slice().sort(function() { return Math.random() - 0.5; }).slice(0, 3);
        document.getElementById("host-engagement-suggestions").innerHTML = shuffled
          .map(function(text) { return "<li>" + text + "</li>"; })
          .join("");
      }

      function answerLiveComment(comment) {
        const text = comment.trim();
        const sizingMatch = text.match(/(\\d{3})\\s*[\\/ ]?\\s*(\\d{2,3})\\s*kg?/i);
        if (sizingMatch || /尺码|穿啥|多大|身高|体重/.test(text)) {
          if (sizingMatch) {
            const height = Number(sizingMatch[1]);
            const weight = Number(sizingMatch[2]);
            let size = "M";
            let layer = "L";
            if (height <= 172 && weight <= 68) { size = "S 或 M"; layer = "M"; }
            else if (height <= 178 && weight <= 75) { size = "M"; layer = "L"; }
            else if (height <= 184 && weight <= 85) { size = "L"; layer = "XL"; }
            else { size = "XL"; layer = "XXL"; }
            return { reply: height + "/" + weight + "kg 正常" + size + "，里面加卫衣建议" + layer, confidence: "88%", action: "Explain sizing" };
          }
          return { reply: "尺码别乱拍，把身高体重打出来，里面要加卫衣就大半码。", confidence: "80%", action: "Explain sizing" };
        }
        if (/真假|真的假的|正品|吊牌|洗标/.test(text)) {
          return { reply: "真假别听我空说，镜头拉近看吊牌、洗标、拉链和走线。", confidence: "90%", action: "Show authenticity proof" };
        }
        if (/黑色|黑/.test(text)) {
          return { reply: "想看黑色扣3，我等下直接拿近镜头给你看色差和细节。", confidence: "82%", action: "Show color option" };
        }
        if (/值|值得|贵|价格|划算/.test(text)) {
          return { reply: "别光看价格，能通勤、能户外，买回去不会吃灰才是真的值。", confidence: "84%", action: "Explain value and pricing" };
        }
        if (/冬天|够暖|保暖|冷/.test(text)) {
          return { reply: "冬天够不够暖看地区，通勤没问题，特别冷里面加抓绒。", confidence: "82%", action: "Explain warmth" };
        }
        return { reply: "这个问题我先记一下，哥几个继续看细节，有具体尺码直接打出来。", confidence: "55%", action: "Ask follow-up" };
      }

      function updateLiveCommentAssistant() {
        const input = document.getElementById("live-comments-input");
        const output = document.getElementById("live-comment-results");
        if (!input || !output) {
          return;
        }
        const comments = input.value.split("\\n").map(function(line) { return line.trim(); }).filter(Boolean).slice(0, 8);
        if (!comments.length) {
          output.innerHTML = '<tr><td colspan="4">等待评论...</td></tr>';
          return;
        }
        output.innerHTML = comments.map(function(comment) {
          const answer = answerLiveComment(comment);
          return "<tr><td>" + comment + "</td><td>" + answer.reply + "</td><td>" + answer.confidence + "</td><td>" + answer.action + "</td></tr>";
        }).join("");
      }

      simulateLiveMetrics();
      updateLiveCommentAssistant();
      window.setInterval(simulateLiveMetrics, 5000);
      window.setInterval(updateLiveCommentAssistant, 5000);
    </script>"""
    return script.replace("__LIVE_PRODUCTS__", json.dumps(live_products, ensure_ascii=False))


def _livestream_order(
    products: list[ScoredProduct],
    report_by_name: dict[str, dict[str, Any]],
) -> dict[str, tuple[ScoredProduct, str]]:
    if not products:
        return {}

    decision_priority = {"Push hard": 0, "Mention briefly": 1, "Skip for today": 2}
    ordered = sorted(
        products,
        key=lambda product: (
            decision_priority.get(report_by_name[product.product_name]["host_decision"], 1),
            -product.score,
            -product.inventory_priority,
            -product.profit_score,
        ),
    )
    non_skip = [
        product
        for product in ordered
        if report_by_name[product.product_name]["host_decision"] != "Skip for today"
    ]
    skip = [
        product
        for product in ordered
        if report_by_name[product.product_name]["host_decision"] == "Skip for today"
    ]
    ordered = non_skip + skip

    start_product = ordered[0]
    second_product = ordered[1] if len(ordered) > 1 else ordered[0]
    third_product = ordered[2] if len(ordered) > 2 else ordered[-1]
    final_product = (skip[-1] if skip else ordered[-1])

    return {
        "Start product": (start_product, "Push hard / easiest conversion"),
        "Second product": (second_product, "Push hard or Mention briefly / traffic builder"),
        "Third product": (third_product, "profit or secondary mention"),
        "Final product": (final_product, "premium closer or Skip product held to the end"),
    }


def _render_livestream_order(order: dict[str, tuple[ScoredProduct, str]]) -> str:
    if not order:
        return ""
    items = "".join(
        f"""
        <div class="order-item">
          <span>{html.escape(slot)}</span>
          <b>{html.escape(product.product_name)}</b>
          <div>{html.escape(reason)} · Score {product.score:.3f} · GMV {product.gmv_level}</div>
          {_list(product.ranking_explanation[:2])}
        </div>"""
        for slot, (product, reason) in order.items()
    )
    return f"""
      <section class="order-panel">
        <h2>Today's Top Products</h2>
        <div class="order-grid">{items}</div>
      </section>"""


def _render_post_live_analysis(products: list[ScoredProduct]) -> str:
    if not products:
        return ""
    total_viewers = sum(round(240 + product.traffic_score * 620) for product in products)
    estimated_gmv = sum(product.target_selling_price * product.stock * product.gmv_score * 0.18 for product in products)
    best_product = max(products, key=lambda product: product.gmv_score)
    worst_product = min(products, key=lambda product: product.gmv_score)
    reasons = [
        f"最佳商品 {best_product.product_name}：流量={best_product.traffic_score:.2f}，转化={best_product.conversion_score:.2f}，GMV={best_product.gmv_level}。",
        f"最弱商品 {worst_product.product_name}：转化难度={worst_product.sellability.conversion_difficulty:.2f}，建议减少讲解时长。",
        "Skip 产品不要放前段，避免刚开场损失在线和互动。",
    ]
    suggestions = [
        "下一场先用 Push hard 商品开场，快速拉互动和加购。",
        "高 CTR 低 CVR 时优先解释价格价值，不要急着换款。",
        "尺码和真假问题集中出现时，提前准备吊牌、洗标、上身尺码话术。",
    ]
    return f"""
      <section class="order-panel">
        <h2>Post-Live Analysis</h2>
        <div class="metrics">
          {_metric("Total viewers", f"{total_viewers:,}")}
          {_metric("Estimated GMV", _money(estimated_gmv, "CNY"))}
          {_metric("Best product", best_product.product_name)}
          {_metric("Worst product", worst_product.product_name)}
        </div>
        <div class="grid">
          <div>
            <h3>Reasons</h3>
            {_list(reasons)}
          </div>
          <div>
            <h3>Next livestream suggestions</h3>
            {_list(suggestions)}
          </div>
        </div>
      </section>"""


def _render_audience_question_assistant(answers: list[AudienceQuestionResponse]) -> str:
    if not answers:
        return ""
    rows = "".join(
        f"""
        <tr>
          <td>{html.escape(answer.comment)}</td>
          <td>{html.escape(answer.short_host_reply)}</td>
          <td>{answer.confidence:.0%}</td>
          <td>{html.escape(answer.suggested_action)}</td>
        </tr>"""
        for answer in answers
    )
    return f"""
      <section class="order-panel">
        <h2>Audience Question Assistant</h2>
        <div class="table-wrap">
          <table class="question-table">
            <thead>
              <tr>
                <th>Viewer comment</th>
                <th>Short host reply</th>
                <th>Confidence</th>
                <th>Suggested action</th>
              </tr>
            </thead>
            <tbody>{rows}</tbody>
          </table>
        </div>
      </section>"""


def _render_live_comment_input() -> str:
    return """
      <section class="order-panel live-comment-input">
        <h2>Live Comment Input</h2>
        <textarea id="live-comments-input" spellcheck="false">175 70kg穿啥
真的假的
黑色有吗</textarea>
        <div class="hint">粘贴实时评论，每行一条；系统每 5 秒生成 reply / confidence / next action。</div>
        <div class="table-wrap">
          <table class="question-table">
            <thead>
              <tr>
                <th>Viewer comment</th>
                <th>Reply</th>
                <th>Confidence</th>
                <th>Next action</th>
              </tr>
            </thead>
            <tbody id="live-comment-results">
              <tr><td colspan="4">等待评论...</td></tr>
            </tbody>
          </table>
        </div>
      </section>"""


def _list(items: list[str]) -> str:
    return "<ul>" + "".join(f"<li>{html.escape(str(item))}</li>" for item in items) + "</ul>"


def _market_price_table(product: ScoredProduct) -> str:
    prices = product.market_research.price_results if product.market_research else []
    if not prices:
        return '<p class="empty">当前没有启用的平台返回价格数据。</p>'

    rows = "".join(
        f"""
        <tr>
          <td>{html.escape(price.platform)}</td>
          <td>{html.escape(price.source)}</td>
          <td>{html.escape(price.title)}</td>
          <td>{html.escape(_money(price.price, price.currency))}</td>
          <td>{_link(price.url)}</td>
          <td>{price.confidence:.0%}</td>
        </tr>"""
        for price in prices
    )
    return f"""
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Platform</th>
              <th>Seller/source</th>
              <th>Title</th>
              <th>Price</th>
              <th>URL</th>
              <th>Confidence</th>
            </tr>
          </thead>
          <tbody>{rows}</tbody>
        </table>
      </div>"""


def _real_vs_inferred(product: ScoredProduct) -> str:
    market = product.market_research
    real_price_count = len(market.price_results) if market else 0
    real_social_posts = sum(len(signal.evidence_posts) for signal in market.social_signals) if market else 0
    inferred = [
        "sellability",
        "competition_score" if not product.avg_competitor_price else "",
        "host scripts",
        "GMV potential",
    ]
    inferred = [item for item in inferred if item]
    return _list(
        [
            f"Real data: {real_price_count} 条价格证据，{real_social_posts} 条真实社媒帖子证据。",
            f"AI inferred data: {', '.join(inferred)}。",
        ]
    )


def _social_heat_signals(product: ScoredProduct) -> str:
    signals = product.market_research.social_signals if product.market_research else []
    preferred_platforms = {"抖音", "小红书", "Douyin", "Xiaohongshu"}
    preferred_signals = [signal for signal in signals if signal.platform in preferred_platforms]
    signals = preferred_signals or signals
    if not signals:
        return '<p class="empty">No real social evidence found</p>'

    cards = []
    for signal in signals:
        evidence = _social_evidence_posts(signal)
        cards.append(
            f"""
            <div class="script-box">
              <h4>{html.escape(signal.platform)} · 热度 {signal.popularity_score:.0%}</h4>
              <b>Top keywords</b>{_list(signal.keywords) if signal.keywords else '<p class="empty">Metrics unavailable</p>'}
              <b>Positive points</b>{_list(signal.positive_points) if signal.positive_points else '<p class="empty">Metrics unavailable</p>'}
              <b>Main concerns</b>{_list(signal.concerns) if signal.concerns else '<p class="empty">Metrics unavailable</p>'}
              <b>Top comments</b>{_top_comments_for_signal(signal)}
              <b>Most common user language</b>{_list(signal.most_common_user_language) if signal.most_common_user_language else '<p class="empty">Metrics unavailable</p>'}
              <b>Real evidence</b>{evidence}
            </div>"""
        )
    return '<div class="script-grid">' + "".join(cards) + "</div>"


def _social_evidence_posts(signal: Any) -> str:
    posts = getattr(signal, "evidence_posts", [])
    if not posts:
        return '<p class="empty">No real social evidence found</p>'

    items = []
    for post in posts:
        metrics = _post_metrics(post)
        top_comments = _list(post.top_comments) if post.top_comments else '<p class="empty">Metrics unavailable</p>'
        image = (
            f'<img src="{html.escape(post.cover_image)}" alt="{html.escape(post.post_title)}" '
            'style="width:100%;max-height:180px;object-fit:cover;border-radius:8px;margin:8px 0;">'
            if post.cover_image
            else ""
        )
        items.append(
            f"""
            <li>
              {image}
              <b>{html.escape(post.post_title)}</b><br>
              <span>{metrics}</span><br>
              <b>Top comments</b>{top_comments}
              {_link(post.real_url)}
            </li>"""
        )
    return "<ul>" + "".join(items) + "</ul>"


def _top_comments_for_signal(signal: Any) -> str:
    comments = [
        comment
        for post in getattr(signal, "evidence_posts", [])
        for comment in getattr(post, "top_comments", [])
    ]
    return _list(comments[:6]) if comments else '<p class="empty">Metrics unavailable</p>'


def _post_metrics(post: Any) -> str:
    if post.likes is None and post.favorites is None and post.comments is None:
        return "Metrics unavailable"
    return (
        f"Likes: {_metric_or_na(post.likes)} · "
        f"Favorites: {_metric_or_na(post.favorites)} · "
        f"Comments: {_metric_or_na(post.comments)}"
    )


def _ai_script(report: dict[str, Any]) -> str:
    script = report["ai_livestream_script"]
    objections = script["objection_handling"]
    return f"""
      <div class="script-grid">
        <div class="script-box">
          <h4>15-second opening hook</h4>
          <p>{html.escape(script["opening_hook_15s"])}</p>
        </div>
        <div class="script-box">
          <h4>60-second selling script</h4>
          <p>{html.escape(script["selling_script_60s"])}</p>
        </div>
        <div class="script-box">
          <h4>Objection handling</h4>
          <ul>
            <li><b>Authenticity:</b> {html.escape(objections["authenticity"])}</li>
            <li><b>Sizing:</b> {html.escape(objections["sizing"])}</li>
            <li><b>Price:</b> {html.escape(objections["price"])}</li>
          </ul>
        </div>
        <div class="script-box">
          <h4>Comparison script</h4>
          <p>{html.escape(script["comparison_script"])}</p>
          <h4>Closing urgency line</h4>
          <p>{html.escape(script["closing_urgency_line"])}</p>
        </div>
      </div>"""


def _warning_list(product: ScoredProduct) -> str:
    warnings = product.market_research.warnings if product.market_research else []
    if not warnings:
        return ""
    return '<div class="warning"><b>数据提示</b>' + _list(warnings) + "</div>"


def _evidence_summary(product: ScoredProduct) -> str:
    evidence = product.market_research.evidence_summary if product.market_research else []
    if not evidence:
        return ""
    return '<div class="evidence"><b>数据证据摘要</b>' + _list(evidence) + "</div>"


def _link(url: str) -> str:
    if not url:
        return "N/A"
    return f'<a href="{html.escape(url)}" target="_blank" rel="noreferrer">查看</a>'


def _link_list(urls: list[str]) -> str:
    if not urls:
        return "<ul><li>N/A</li></ul>"
    return "<ul>" + "".join(f"<li>{_link(url)}</li>" for url in urls) + "</ul>"


def _decision_label(decision: str) -> str:
    labels = {
        "Push hard": "重点主推",
        "Mention briefly": "简短提及",
        "Skip for today": "今日跳过",
    }
    return labels.get(decision, "待判断")


def _money_or_na(value: float | None, currency: str = "CNY") -> str:
    return _money(value, currency) if value is not None else "N/A"


def _metric_or_na(value: int | None) -> str:
    return f"{value:,}" if value is not None else "Metrics unavailable"


def _money(value: float | None, currency: str = "CNY") -> str:
    if value is None:
        return "N/A"
    symbol = {"USD": "$", "CAD": "C$", "CNY": "¥"}.get(currency.upper(), f"{currency.upper()} ")
    return f"{symbol}{value:,.2f}"
