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
                inventory_items=[
                    {"product_name": "Beta Jacket Men's", "sku": "X000010511", "color": "Spotlight", "notes": "hard shell"},
                    {"product_name": "Atom Vest Men's", "sku": "X000009559", "color": "Black"},
                ],
            )
            record = store.get("ashley")
            self.assertEqual(record["workspace_id"], "ashley")
            self.assertEqual(record["count"], 2)
            self.assertEqual(record["products"][0]["name"], "Beta Jacket Men's")
            self.assertEqual(record["products"][0]["sku"], "X000010511")
            self.assertIn("10511", record["products"][0]["aliases"])
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

    def test_director_matches_chinese_taobao_titles_to_excel_skus(self) -> None:
        products = [
            {"name": "Emblem Fleece Crew Neck Pullover M", "sku": "X000009787", "score": 0.9},
            {"name": "Psiphon Hoody", "sku": "X000009515", "score": 0.8},
            {"name": "Rho Bottom", "sku": "X000007308", "score": 0.7},
        ]
        cases = [
            ("加拿大直邮始祖鸟圆领卫衣女款Emblem Fleece Crew Women's加绒", "Emblem Fleece Crew Neck Pullover M"),
            ("ARC'TERYX始祖鸟 PSIPHON HOODY 防风 女子 软", "Psiphon Hoody"),
            ("ARC'TERYX/始祖鸟 Rho Boot Cut Bottom 女士内加绒保暖打底裤 7308", "Rho Bottom"),
        ]
        for index, (title, expected) in enumerate(cases):
            connector = LiveDataConnector()
            connector.ingest_live_metrics({
                "source": "chrome_extension",
                "host_id": f"host-product-title-{index}",
                "liveId": f"live-product-title-{index}",
                "metrics": {"pay_amt": 1000, "online_uv": 20, "uv": 40, "current_product": title},
            })
            decision = connector.get_decision(payload={"host_id": f"host-product-title-{index}"}, products=products)
            self.assertEqual(decision.matched_current_product, expected)

    def test_director_rejects_different_sku_inside_same_model_family(self) -> None:
        connector = LiveDataConnector()
        connector.ingest_live_metrics({
            "source": "chrome_extension",
            "host_id": "host-product-sku-mismatch",
            "liveId": "live-product-sku-mismatch",
            "metrics": {
                "pay_amt": 1000,
                "online_uv": 20,
                "uv": 40,
                "current_product": "现货 始祖鸟 Kragg SL Cotton Bird Tile Shirt LS长袖T恤9537",
            },
        })
        decision = connector.get_decision(
            payload={"host_id": "host-product-sku-mismatch"},
            products=[{"name": "Kragg Cotton Shirt LS", "sku": "X000009714", "score": 0.9}],
        )
        self.assertEqual(decision.matched_current_product, "")
        self.assertEqual(decision.recommended_next_product, "Kragg Cotton Shirt LS")


if __name__ == "__main__":
    unittest.main()
