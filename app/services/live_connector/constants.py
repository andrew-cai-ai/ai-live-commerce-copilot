from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()

LATEST_EXTENSION_VERSION = os.getenv("LATEST_EXTENSION_VERSION", "0.1.4")
MODEL_V0_MIN_CONFIDENCE = float(os.getenv("MODEL_V0_MIN_CONFIDENCE", "0.4"))

_TREND_FIELDS = [
    "online_uv",
    "uv",
    "pv",
    "stay_time_pu",
    "heat_score",
    "ipv_uv_rate",
    "pay_byr_rate",
    "pay_amt",
    "pay_buyer_cnt",
    "pay_item_qty",
    "comment_uv",
    "atn_uv",
    "look_uv_5min_d_live",
    "look_time_5min_avg_d_live",
    "pay_amt_5min_d_live",
    "item_click_rate",
    "item_conversion_rate",
    "item_add_cart_rate",
    "item_gmv",
]

_TIMELINE_TREND_FIELDS = [
    "online_uv",
    "stay_time_pu",
    "heat_score",
    "ipv_uv_rate",
    "pay_byr_rate",
    "comment_uv",
    "pay_amt_5min_d_live",
    "item_click_rate",
    "item_conversion_rate",
    "item_add_cart_rate",
]

_TOTAL_STATS_REQUIRED = (
    "heat_score",
    "ipv_uv_rate",
    "pay_byr_rate",
    "online_uv",
    "uv",
    "pv",
    "comment_uv",
    "pay_amt",
)

_DATA_REGION_REQUIRED = (
    "look_uv_td_d_live",
    "look_uv_5min_d_live",
    "pay_amt_td_d_live",
    "pay_amt_5min_d_live",
    "look_time_td_avg_d_live",
)


_ENCODED_VALUE_TYPE_MAP: dict[str, str] = {
    "uv": "uv",
    "pv": "pv",
    "online_uv": "online_uv",
    "heat_score": "heat_score",
    "pay_amt": "pay_amt",
    "pay_byr_rate": "pay_byr_rate",
    "ipv_uv_rate": "ipv_uv_rate",
    "stay_time_pu": "stay_time_pu",
    "comment_uv": "comment_uv",
    "pay_item_qty": "pay_item_qty",
    "pay_buyer_cnt": "pay_buyer_cnt",
    "look_uv_td_d_live": "look_uv_td_d_live",
    "look_uv_5min_d_live": "look_uv_5min_d_live",
    "look_time_td_avg_d_live": "look_time_td_avg_d_live",
    "look_time_5min_avg_d_live": "look_time_5min_avg_d_live",
    "pay_amt_td_d_live": "pay_amt_td_d_live",
    "pay_amt_5min_d_live": "pay_amt_5min_d_live",
    "pay_amt_td_d_shop": "pay_amt_td_d_shop",
    "pay_amt_5min_d_shop": "pay_amt_5min_d_shop",
}
