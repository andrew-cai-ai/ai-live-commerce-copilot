import tempfile
import unittest
from pathlib import Path

from app.services.live_training_data import LiveTrainingDataService, classify_action, infer_product_dna


class LiveTrainingDataTests(unittest.TestCase):
    def test_action_classification_and_product_dna(self) -> None:
        self.assertEqual(classify_action("175 70kg 尺码怎么穿"), "A001")
        self.assertEqual(classify_action("镜头拉近展示吊牌和洗标"), "A002")
        dna = infer_product_dna("Gamma Pant Men's")
        self.assertEqual(dna["category"], "裤装")
        self.assertIn("尺码", dna["tags"])

    def test_quality_training_dataset_replay_and_model_v0(self) -> None:
        with tempfile.TemporaryDirectory() as dirname:
            service = LiveTrainingDataService(
                Path(dirname) / "training.jsonl",
                Path(dirname) / "graph.json",
                Path(dirname) / "model.json",
            )
            for index in range(6):
                product = "Kragg Shirt" if index < 3 else "Gamma Pant"
                action = "讲尺码" if index < 3 else "价格对比"
                ai = "switch to sizing explanation" if index < 3 else "explain value"
                service.record_sample(
                    host_id="Gigi" if index % 2 == 0 else "Duoduo",
                    product_name=product,
                    ai_decision={"decision": ai, "next_action": action},
                    host_action={"action_label": action, "next_action": action, "product_position": 2, "product_elapsed_seconds": 92},
                    before_metrics={"timestamp": 100 + index * 40, "ctr": 0.10, "cvr": 0.02, "gmv": 1000, "heat": 500, "comments": 10, "online_uv": 80},
                    after_metrics={"timestamp": 132 + index * 40, "ctr": 0.12, "cvr": 0.033, "gmv": 1800, "heat": 560, "comments": 18, "online_uv": 85},
                    delta={"ctr": 0.02, "cvr": 0.013, "gmv": 800, "heat": 60, "comments": 8},
                    result="有效",
                    context={"comments": "175 70kg穿啥", "product_position": 2, "product_elapsed_seconds": 92},
                )

            noisy = service.record_sample(
                host_id="Gigi",
                product_name="",
                ai_decision={"decision": "push harder"},
                host_action={"action_label": "切换商品"},
                before_metrics={},
                after_metrics={},
                delta={"ctr": 0, "cvr": 0, "gmv": 0},
                result="无效",
                context={},
            )
            self.assertLess(noisy["sample_quality_score"], 70)
            self.assertFalse(noisy["use_for_training"])

            dashboard = service.training_data_dashboard()
            self.assertEqual(dashboard["total_samples"], 7)
            self.assertEqual(dashboard["training_samples"], 6)
            self.assertEqual(dashboard["rejected_samples"], 1)

            coverage = service.coverage_dashboard(min_product_samples=4, min_host_samples=5)
            self.assertTrue(coverage["products_needing_samples"])
            self.assertTrue(coverage["hosts_needing_samples"])

            cold = service.cold_start_recommendation("Kragg Cotton Shirt")
            self.assertTrue(cold["similar_products"])
            self.assertIn("recommended_sequence", cold["inherited_playbook"])

            replay = service.director_replay(limit=10)
            self.assertEqual(len(replay), 7)
            self.assertIn("quality", replay[0])

            evaluation = service.offline_evaluation()
            self.assertEqual(evaluation["evaluated_samples"], 6)
            self.assertIn("director_accuracy", evaluation)

            model = service.train_director_model_v0()
            self.assertEqual(model["status"], "trained")
            self.assertTrue((Path(dirname) / "model.json").exists())
            prediction = service.predict_director_model_v0({
                "heat": 500,
                "ctr": 0.10,
                "cvr": 0.02,
                "gmv": 1000,
                "comments": 10,
                "online_uv": 80,
                "product_category": "上衣/T恤",
                "product_tags": ["日常", "尺码"],
                "season": "春夏",
                "price_band": "mid",
                "comment_topics": ["尺码"],
                "host_id": "Gigi",
            })
            self.assertEqual(prediction["status"], "predicted")
            self.assertIn(prediction["action_code"], {"A001", "A004"})

    def test_train_v0_not_enough_data(self) -> None:
        with tempfile.TemporaryDirectory() as dirname:
            service = LiveTrainingDataService(
                Path(dirname) / "training.jsonl",
                Path(dirname) / "graph.json",
                Path(dirname) / "model.json",
            )
            result = service.train_director_model_v0()
            self.assertEqual(result["status"], "not_enough_data")
            prediction = service.predict_director_model_v0({"heat": 500})
            self.assertEqual(prediction["status"], "missing_model")

    def test_training_sample_prefers_explicit_action_code(self) -> None:
        with tempfile.TemporaryDirectory() as dirname:
            service = LiveTrainingDataService(
                Path(dirname) / "training.jsonl",
                Path(dirname) / "graph.json",
                Path(dirname) / "model.json",
            )
            sample = service.record_sample(
                host_id="Gigi",
                product_name="Kragg Shirt",
                ai_decision={"action_code": "A004", "decision": "engage comments", "next_action": "评论区扣1"},
                host_action={"action_code": "A004", "action_label": "评论区扣1"},
                before_metrics={"timestamp": 100, "ctr": 0.10, "cvr": 0.02, "gmv": 1000},
                after_metrics={"timestamp": 130, "ctr": 0.11, "cvr": 0.03, "gmv": 1300},
                delta={"ctr": 0.01, "cvr": 0.01, "gmv": 300},
                result="有效",
                context={"product_elapsed_seconds": 60},
            )
            self.assertEqual(sample["action_code"], "A004")
            self.assertNotIn("host_action_differs_from_ai_recommendation", sample["sample_quality_reasons"])


if __name__ == "__main__":
    unittest.main()
