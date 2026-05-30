import unittest
import json
import tempfile
from pathlib import Path

import app.services.live_connector.constants as live_constants
from app.services.inventory_import import _normalize_taobao_payload
from app.services.live_data_connector import (
    LATEST_EXTENSION_VERSION,
    LiveDataConnector,
    LiveMetricSnapshot,
    LiveSessionState,
    _director_model_state,
    _effect_metrics,
    _extract_taobao_encoded_metrics,
    _nearest_ai_decision,
    _normalize_ingested_payload,
    _parse_encoded_metric_row,
    _product_switched_during_window,
    _version_lt,
    live_training_data,
)


class LiveDataConnectorTests(unittest.TestCase):
    def test_extension_version_matches_manifest(self) -> None:
        self.assertEqual(LATEST_EXTENSION_VERSION, "0.1.4")
        self.assertFalse(_version_lt("0.1.4", LATEST_EXTENSION_VERSION))

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

    def test_dom_fallback_payload_does_not_block_director(self) -> None:
        connector = LiveDataConnector()
        decision = connector.ingest_live_metrics({
            "source": "chrome_extension",
            "host_id": "host-dom",
            "liveId": "live-dom",
            "captured_api": "dom_live_dashboard",
            "payload_sections": {"domFallback": True},
            "metrics": {
                "pay_amt": 998,
                "online_uv": 20,
                "pv": 27,
                "uv": 50,
                "current_product": "Kragg Cotton Shirt",
            },
        })
        self.assertTrue(decision.valid_live_metrics)
        self.assertEqual(decision.missing_metrics, [])
        self.assertEqual(decision.current_action, "push harder")
        self.assertEqual(decision.livestream_mode, "有限实时数据模式")
        self.assertIn("页面可见成交额 ¥998", decision.reason)
        self.assertEqual(decision.snapshot.pay_amt, 998)
        self.assertEqual(decision.snapshot.current_product, "Kragg Cotton Shirt")

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

    def test_effect_metrics_do_not_use_room_level_fallback(self) -> None:
        room_only = LiveMetricSnapshot(
            timestamp=1.0,
            host_id="host-effect",
            ipv_uv_rate=0.12,
            pay_byr_rate=0.02,
            pay_amt=1000,
            pay_amt_5min_d_live=800,
        )
        metrics = _effect_metrics(room_only)
        self.assertEqual(metrics["ctr"], 0)
        self.assertEqual(metrics["cvr"], 0)
        self.assertEqual(metrics["gmv"], 0)

        product_level = LiveMetricSnapshot(
            timestamp=2.0,
            host_id="host-effect",
            ipv_uv_rate=0.12,
            pay_byr_rate=0.02,
            pay_amt=1000,
            item_click_rate=0.08,
            item_conversion_rate=0.03,
            item_gmv=500,
        )
        product_metrics = _effect_metrics(product_level)
        self.assertEqual(product_metrics["ctr"], 0.08)
        self.assertEqual(product_metrics["cvr"], 0.03)
        self.assertEqual(product_metrics["gmv"], 500)

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

    def test_model_v0_guides_default_live_decision(self) -> None:
        old_model_path = live_training_data.model_path
        with tempfile.TemporaryDirectory() as dirname:
            try:
                live_training_data.model_path = Path(dirname) / "director_model_v0.json"
                live_training_data.model_path.write_text(json.dumps({
                    "schema_version": "director_model_v0",
                    "accuracy": 0.75,
                    "training_samples": 8,
                    "model": {
                        "actions": ["A004", "A007"],
                        "action_counts": {"A004": 8, "A007": 1},
                        "feature_counts": {},
                    },
                }), encoding="utf-8")
                connector = LiveDataConnector()
                decision = connector.ingest_live_metrics({
                    "source": "chrome_extension",
                    "host_id": "host-model",
                    "liveId": "live-model",
                    "metrics": {
                        "online_uv": 30,
                        "uv": 300,
                        "pv": 600,
                        "heat_score": 300,
                        "pay_amt": 1000,
                        "pay_byr_rate": 0.02,
                        "ipv_uv_rate": 0.08,
                        "comment_uv": 12,
                        "item_name": "Kragg Shirt",
                    },
                })
                self.assertEqual(decision.action_code, "A004")
                self.assertEqual(decision.current_action, "explain value")
                self.assertIn("model_v0 action", decision.reason[0])
            finally:
                live_training_data.model_path = old_model_path

    def test_fixture_extension_payload_parses_encoded_metrics(self) -> None:
        fixture_path = Path(__file__).resolve().parent / "fixtures" / "extension_metrics_sample.json"
        payload = json.loads(fixture_path.read_text(encoding="utf-8"))
        normalized = _normalize_ingested_payload(payload)
        metrics = _extract_taobao_encoded_metrics(normalized)
        self.assertEqual(metrics.get("uv"), 13439)
        self.assertEqual(metrics.get("online_uv"), 15)
        self.assertEqual(metrics.get("look_time_5min_avg_d_live"), 52)
        connector = LiveDataConnector()
        decision = connector.ingest_live_metrics(payload)
        self.assertEqual(decision.snapshot.uv, 13439)
        self.assertEqual(decision.snapshot.online_uv, 15)

    def test_rules_take_priority_over_model_v0(self) -> None:
        old_model_path = live_training_data.model_path
        with tempfile.TemporaryDirectory() as dirname:
            try:
                live_training_data.model_path = Path(dirname) / "director_model_v0.json"
                live_training_data.model_path.write_text(json.dumps({
                    "schema_version": "director_model_v0",
                    "accuracy": 0.9,
                    "training_samples": 20,
                    "model": {
                        "actions": ["A004", "A007"],
                        "action_counts": {"A004": 20, "A007": 1},
                        "feature_counts": {},
                    },
                }), encoding="utf-8")
                connector = LiveDataConnector()
                decision = connector.ingest_live_metrics({
                    "source": "chrome_extension",
                    "host_id": "host-rule-priority",
                    "liveId": "live-rule-priority",
                    "metrics": {
                        "online_uv": 30,
                        "uv": 300,
                        "pv": 600,
                        "heat_score": 300,
                        "pay_amt": 1000,
                        "pay_byr_rate": 0.02,
                        "ipv_uv_rate": 0.08,
                        "comment_uv": 12,
                        "sizing_comments": 2,
                        "comment_text": "175 70kg穿啥",
                    },
                })
                self.assertEqual(decision.action_code, "A001")
                self.assertEqual(decision.current_action, "switch to sizing explanation")
                self.assertTrue(
                    any("model_v0 suggests" in warning for warning in decision.warnings),
                    msg=f"expected model disagreement warning, got {decision.warnings}",
                )
            finally:
                live_training_data.model_path = old_model_path

    def test_nearest_ai_decision_includes_action_code(self) -> None:
        session = LiveSessionState()
        session.action_history.append({
            "timestamp": 100.0,
            "action_code": "A005",
            "decision": "push harder",
            "mode": "Live",
            "reason": ["heat up"],
            "next_action": "加速逼单",
            "confidence": 0.9,
            "current_live_score": 80,
            "event_type": "director_decision",
        })
        ai_decision = _nearest_ai_decision(session, 100.0)
        self.assertEqual(ai_decision["action_code"], "A005")
        self.assertEqual(ai_decision["decision"], "push harder")

    def test_director_model_state_uses_product_metrics_only(self) -> None:
        snapshot = LiveMetricSnapshot(
            timestamp=1.0,
            host_id="host-model-state",
            ipv_uv_rate=0.12,
            pay_byr_rate=0.02,
            pay_amt=1000,
            heat_score=400,
            online_uv=50,
            comment_uv=8,
            current_product="Kragg Shirt",
        )
        state = _director_model_state(snapshot, {})
        self.assertEqual(state["ctr"], 0)
        self.assertEqual(state["cvr"], 0)
        self.assertEqual(state["gmv"], 0)

    def test_model_missing_syncs_a007_directive(self) -> None:
        old_model_path = live_training_data.model_path
        with tempfile.TemporaryDirectory() as dirname:
            try:
                live_training_data.model_path = Path(dirname) / "missing_model.json"
                connector = LiveDataConnector()
                decision = connector.ingest_live_metrics({
                    "source": "chrome_extension",
                    "host_id": "host-no-model",
                    "liveId": "live-no-model",
                    "metrics": {
                        "online_uv": 30,
                        "uv": 300,
                        "pv": 600,
                        "heat_score": 300,
                        "pay_amt": 1000,
                        "pay_byr_rate": 0.02,
                        "ipv_uv_rate": 0.08,
                        "comment_uv": 12,
                    },
                })
                self.assertEqual(decision.action_code, "A007")
                self.assertEqual(decision.current_action, "engage comments")
            finally:
                live_training_data.model_path = old_model_path

    def test_model_low_confidence_falls_back_to_a007(self) -> None:
        old_model_path = live_training_data.model_path
        old_threshold = live_constants.MODEL_V0_MIN_CONFIDENCE
        live_constants.MODEL_V0_MIN_CONFIDENCE = 0.9
        with tempfile.TemporaryDirectory() as dirname:
            try:
                live_training_data.model_path = Path(dirname) / "director_model_v0.json"
                live_training_data.model_path.write_text(json.dumps({
                    "schema_version": "director_model_v0",
                    "accuracy": 0.5,
                    "training_samples": 10,
                    "model": {
                        "actions": ["A004", "A007"],
                        "action_counts": {"A004": 5, "A007": 5},
                        "feature_counts": {},
                    },
                }), encoding="utf-8")
                connector = LiveDataConnector()
                decision = connector.ingest_live_metrics({
                    "source": "chrome_extension",
                    "host_id": "host-low-confidence",
                    "liveId": "live-low-confidence",
                    "metrics": {
                        "online_uv": 30,
                        "uv": 300,
                        "pv": 600,
                        "heat_score": 300,
                        "pay_amt": 1000,
                        "pay_byr_rate": 0.02,
                        "ipv_uv_rate": 0.08,
                        "comment_uv": 12,
                    },
                })
                self.assertEqual(decision.action_code, "A007")
                self.assertEqual(decision.current_action, "engage comments")
                self.assertTrue(any("low confidence" in reason for reason in decision.reason))
            finally:
                live_training_data.model_path = old_model_path
                live_constants.MODEL_V0_MIN_CONFIDENCE = old_threshold

    def test_host_feedback_stores_action_code(self) -> None:
        connector = LiveDataConnector()
        connector.ingest_live_metrics({
            "source": "chrome_extension",
            "host_id": "host-feedback-code",
            "liveId": "live-feedback-code",
            "metrics": {"online_uv": 10, "uv": 100, "heat_score": 300, "pay_amt": 1000},
        })
        result = connector.record_host_feedback(
            "host-feedback-code",
            {
                "action": "explain value",
                "action_code": "A004",
                "sentence": "讲价格优势",
                "current_product": "Kragg Shirt",
            },
        )
        self.assertEqual(result["feedback"]["action_code"], "A004")


if __name__ == "__main__":
    unittest.main()
