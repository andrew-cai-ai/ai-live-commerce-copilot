import unittest

from app.services.inventory_import import _normalize_taobao_payload
from app.services.live_data_connector import (
    LATEST_EXTENSION_VERSION,
    LiveDataConnector,
    _extract_taobao_encoded_metrics,
    _normalize_ingested_payload,
    _parse_encoded_metric_row,
    _product_switched_during_window,
    _version_lt,
)


class LiveDataConnectorTests(unittest.TestCase):
    def test_extension_version_matches_manifest(self) -> None:
        self.assertEqual(LATEST_EXTENSION_VERSION, "0.1.3")
        self.assertFalse(_version_lt("0.1.3", LATEST_EXTENSION_VERSION))

    def test_jsonp_payload_normalization(self) -> None:
        payload = 'mtopjsonpdc_lsad29({"data":{"online_uv":520}});'
        self.assertEqual(_normalize_taobao_payload(payload), '{"data":{"online_uv":520}}')
        self.assertEqual(_normalize_taobao_payload('{"data":1}'), '{"data":1}')

    def test_encoded_metric_row_exact_value_type(self) -> None:
        row = {"value": "累计观看,uv,13,13439,x"}
        parsed = _parse_encoded_metric_row(row)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["value_type"], "uv")
        self.assertEqual(parsed["numeric_value"], 13439)

    def test_extract_encoded_metrics_does_not_guess_similar_rate_names(self) -> None:
        payload = {
            "data": {
                "dataList": [
                    {
                        "data": [
                            {"value": "累计观看,uv,13,13439,x"},
                            {"value": "在线,online_uv,15,15,x"},
                            {"value": "粉丝占比,look_uv_not_fans_rate,66.8%,0.668,x"},
                            {"value": "点击率,ipv_uv_rate,15.52%,0.1552,x"},
                            {"value": "5分钟停留,look_time_5min_avg_d_live,52,52,x"},
                        ]
                    }
                ]
            }
        }
        metrics = _extract_taobao_encoded_metrics(payload)
        self.assertEqual(metrics["uv"], 13439)
        self.assertEqual(metrics["online_uv"], 15)
        self.assertEqual(metrics["ipv_uv_rate"], 0.1552)
        self.assertEqual(metrics["look_time_5min_avg_d_live"], 52)
        self.assertNotIn("look_uv_not_fans_rate", metrics)

    def test_normalize_extension_payload_preserves_sections_and_events(self) -> None:
        normalized = _normalize_ingested_payload({
            "source": "chrome_extension",
            "host_id": "host-001",
            "liveId": "live-001",
            "metrics": {
                "uv": 13439,
                "online_uv": 15,
                "pay_amt": 41230,
                "dataRegion": {"look_uv_td_d_live": 10511},
            },
            "events": [{"title": "Kragg Shirt", "payBuyerCnt": 3}],
        })
        self.assertEqual(normalized["uv"], 13439)
        self.assertEqual(normalized["online_uv"], 15)
        self.assertEqual(normalized["dataRegion"]["look_uv_td_d_live"], 10511)
        self.assertEqual(normalized["interactSecKill"][0]["title"], "Kragg Shirt")

    def test_ingest_extension_payload_maps_exact_metrics(self) -> None:
        connector = LiveDataConnector()
        decision = connector.ingest_live_metrics({
            "source": "chrome_extension",
            "host_id": "host-001",
            "liveId": "live-001",
            "metrics": {
                "uv": 13439,
                "pv": 21213,
                "online_uv": 15,
                "heat_score": 571,
                "pay_amt": 41230,
                "pay_byr_rate": 0.0201,
                "ipv_uv_rate": 0.1552,
                "stay_time_pu": 66,
                "comment_uv": 33,
                "pay_item_qty": 12,
                "pay_buyer_cnt": 8,
            },
        })
        snapshot = decision.snapshot
        self.assertEqual(snapshot.uv, 13439)
        self.assertEqual(snapshot.pv, 21213)
        self.assertEqual(snapshot.online_uv, 15)
        self.assertEqual(snapshot.heat_score, 571)
        self.assertEqual(snapshot.pay_amt, 41230)
        self.assertEqual(snapshot.ipv_uv_rate, 0.1552)
        self.assertEqual(snapshot.pay_byr_rate, 0.0201)
        self.assertEqual(decision.source, "chrome_extension")

    def test_room_metrics_do_not_fill_product_level_fields(self) -> None:
        connector = LiveDataConnector()
        decision = connector.ingest_live_metrics({
            "source": "chrome_extension",
            "host_id": "host-no-product",
            "liveId": "live-no-product",
            "metrics": {
                "online_uv": 10,
                "uv": 100,
                "heat_score": 300,
                "pay_amt": 1000,
                "pay_byr_rate": 0.02,
                "ipv_uv_rate": 0.12,
                "dataRegion": {
                    "look_time_5min_avg_d_live": 52,
                    "pay_amt_5min_d_live": 800,
                },
            },
        })
        snapshot = decision.snapshot
        self.assertEqual(snapshot.item_click_rate, 0)
        self.assertEqual(snapshot.item_conversion_rate, 0)
        self.assertEqual(snapshot.item_gmv, 0)
        self.assertEqual(snapshot.stay_time_pu, 0)
        self.assertEqual(snapshot.look_time_5min_avg_d_live, 52)

    def test_product_switch_detection_for_sample_quality(self) -> None:
        connector = LiveDataConnector()
        decision = connector.ingest_live_metrics({
            "source": "chrome_extension",
            "host_id": "host-switch",
            "liveId": "live-switch",
            "metrics": {
                "online_uv": 10,
                "uv": 100,
                "heat_score": 300,
                "pay_amt": 1000,
                "item_name": "Gamma Pant",
            },
        })
        self.assertTrue(_product_switched_during_window({"product": "Kragg Shirt"}, decision.snapshot))
        self.assertFalse(_product_switched_during_window({"product": "Gamma Pant"}, decision.snapshot))


if __name__ == "__main__":
    unittest.main()
