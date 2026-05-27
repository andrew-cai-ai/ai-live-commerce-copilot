from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class AudienceQuestionResponse:
    comment: str
    short_host_reply: str
    confidence: float
    suggested_action: str


def answer_audience_questions(raw_comments: str) -> list[AudienceQuestionResponse]:
    responses = []
    for line in raw_comments.splitlines():
        comment = line.strip()
        if not comment:
            continue
        responses.append(answer_audience_question(comment))
    return responses


def answer_audience_question(comment: str) -> AudienceQuestionResponse:
    normalized = comment.lower()
    sizing = re.search(r"(\d{3})\s*[/ ]?\s*(\d{2,3})\s*kg?", normalized)
    if sizing or any(keyword in comment for keyword in ["尺码", "穿啥", "多大", "身高", "体重"]):
        reply = _sizing_reply(comment, sizing)
        return AudienceQuestionResponse(comment, reply, 0.88, "Explain sizing")

    if any(keyword in comment for keyword in ["冬天", "够暖", "保暖", "冷"]):
        return AudienceQuestionResponse(
            comment,
            "冬天够不够暖看地区和内搭，通勤没问题，特别冷建议里面加抓绒或卫衣。",
            0.82,
            "Explain warmth",
        )

    if any(keyword in comment for keyword in ["真", "假", "真的假的", "正品", "吊牌"]):
        return AudienceQuestionResponse(
            comment,
            "真假别听我空说，镜头拉近看吊牌、洗标、拉链和走线，细节直接给你看。",
            0.9,
            "Show authenticity proof",
        )

    if any(keyword in comment for keyword in ["值", "值得", "贵", "价格", "划算"]):
        return AudienceQuestionResponse(
            comment,
            "别光看价格，你看它能不能常穿、场景够不够多，平时通勤穿都行就不容易吃灰。",
            0.84,
            "Explain value and pricing",
        )

    return AudienceQuestionResponse(
        comment,
        "这个问题我先记一下，哥几个继续看细节，有具体尺码和场景直接打出来。",
        0.55,
        "Ask follow-up",
    )


def _sizing_reply(comment: str, match: re.Match[str] | None) -> str:
    if not match:
        return "尺码别乱拍，把身高体重发出来；想里面加卫衣或抓绒，建议留一点空间。"
    height = int(match.group(1))
    weight = int(match.group(2))
    if height <= 172 and weight <= 68:
        size = "S 或 M"
        layer_size = "M"
    elif height <= 178 and weight <= 75:
        size = "M"
        layer_size = "L"
    elif height <= 184 and weight <= 85:
        size = "L"
        layer_size = "XL"
    else:
        size = "XL"
        layer_size = "XXL"
    return f"{height}/{weight}kg 正常 {size}，里面加卫衣建议 {layer_size}，想修身就选前一个。"
