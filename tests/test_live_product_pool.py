import tempfile
import unittest
from pathlib import Path

from app.services.live_connector.connector import LiveDataConnector
from app.services.live_product_pool import LiveProductPoolStore


class LiveProductPoolTests(unittest.TestCase):
    def test_save_and_read_workspace_product_pool(self) -> None:
        with tempfile.TemporaryDirectory() as dirname:
            store = LiveProductPoolStore(Path(dirname) / "pool.json")
            store.save(
                [
                    {"name": "Beta Jacket Men's", "score": 0.81, "target_selling_price": 2399},
                    {"name": "Atom Vest Men's", "score": 0.76, "target_selling_price": 1499},
                ],
                workspace_id="ashley",
            )
            record = store.get("ashley")
            self.assertEqual(record["workspace_id"], "ashley")
            self.assertEqual(record["count"], 2)
            self.assertEqual(record["products"][0]["name"], "Beta Jacket Men's")
            self.assertEqual(record["products"][0]["target_selling_price"], 2399)

    def test_director_matches_taobao_title_to_excel_product_pool(self) -> None:
        connector = LiveDataConnector()
        connector.ingest_live_metrics({
            "source": "chrome_extension",
            "host_id": "host-product-match",
            "liveId": "live-product-match",
            "metrics": {
                "pay_amt": 12075,
                "online_uv": 27,
                "uv": 53,
                "current_product": "【极速】始祖鸟26新男Beta硬壳冲锋衣 X8884/X10361",
            },
        })
        decision = connector.get_decision(
            payload={"host_id": "host-product-match"},
            products=[
                {"name": "Beta Jacket Men's", "score": 0.9, "profit_margin": 0.3, "inventory": 5},
                {"name": "Atom Vest Men's", "score": 0.8, "profit_margin": 0.31, "inventory": 5},
            ],
        )
        self.assertEqual(decision.matched_current_product, "Beta Jacket Men's")
        self.assertGreaterEqual(decision.current_product_match_confidence, 0.52)
        self.assertEqual(decision.product_health["product"], "Beta Jacket Men's")
        self.assertEqual(decision.recommended_next_product, "Atom Vest Men's")

    def test_director_does_not_mislabel_unknown_model_as_excel_product(self) -> None:
        connector = LiveDataConnector()
        connector.ingest_live_metrics({
            "source": "chrome_extension",
            "host_id": "host-product-unmatched",
            "liveId": "live-product-unmatched",
            "metrics": {
                "pay_amt": 12075,
                "online_uv": 27,
                "uv": 53,
                "current_product": "【极速】始祖鸟26新男COVERT无帽全拉链针织抓绒衣 X8884/X10361",
            },
        })
        decision = connector.get_decision(
            payload={"host_id": "host-product-unmatched"},
            products=[
                {"name": "Emblem Fleece Full Zip Hoody Men's", "score": 0.9, "profit_margin": 0.18, "inventory": 5},
                {"name": "Rho Bottom", "score": 0.8, "profit_margin": 0.36, "inventory": 5},
            ],
        )
        self.assertEqual(decision.matched_current_product, "")
        self.assertEqual(decision.recommended_next_product, "Emblem Fleece Full Zip Hoody Men's")


if __name__ == "__main__":
    unittest.main()
