import io
import json
import unittest

from openpyxl import Workbook

from app.services.inventory_import import build_inventory_items, parse_smart_inventory_file
from report import _fallback_host_decision
from scoring import score_products


def _workbook_bytes(rows: list[tuple[object, ...]]) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


class InventoryImportTests(unittest.TestCase):
    def test_headerless_ashley_style_excel_keeps_all_products_and_prices(self) -> None:
        excel_bytes = _workbook_bytes([
            (
                "5月29号\n小溪",
                "Emblem Fleece Crew Neck Pullover M",
                "X000009787",
                "Habitat",
                95.0,
                699.0,
                "闪降",
                "店内折扣",
                "按照erp+官网库存。从1200闪降到699",
                None,
                "已做",
                None,
                "1200\n国内官网下架",
            ),
            (
                None,
                "Emblem Fleece Full Zip Hoody Men's",
                "X000009929",
                "Habitat",
                "133*5.9+20.33",
                980.0,
                None,
                "店内折扣",
                "店铺折扣，可以按照官网更新库存+erp更新库存",
                None,
                "已做",
                None,
                None,
            ),
            (
                None,
                "Squamish Hoody\n男士",
                "X000010276",
                "sea salt",
                "店铺折扣，进价*0.7+20.33",
                1299.0,
                None,
                "店内折扣",
                "店铺折扣，可以根据官网+erp更新库存",
                None,
                "已做",
                None,
                "白色都是被女生买的",
            ),
            (
                None,
                "Gamma pant men",
                "X000010481",
                "Tatsu",
                "260*0.7+20.33",
                1229.0,
                None,
                "店内折扣",
                "按照官网库存更新",
                None,
                "已做",
                None,
                None,
            ),
        ])

        rows = parse_smart_inventory_file(excel_bytes, "ashley.xlsx")

        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[0].product_name, "Emblem Fleece Crew Neck Pullover M")
        self.assertEqual(rows[0].sku, "X000009787")
        self.assertEqual(rows[0].target_price, 699.0)
        self.assertIn("国内官网下架", rows[0].notes)
        self.assertEqual(rows[1].cost_price, 805.03)
        self.assertEqual(rows[1].cost_currency, "CNY")
        self.assertEqual(rows[2].sku, "X000010276")
        self.assertEqual(rows[2].target_price, 1299.0)
        self.assertIsNone(rows[2].cost_price)
        self.assertEqual(rows[3].cost_price, 202.33)
        self.assertEqual(rows[3].cost_currency, "CAD")

        items = build_inventory_items("", excel_bytes=excel_bytes, excel_filename="ashley.xlsx")
        squamish = next(item for item in items if item.sku == "X000010276")
        gamma = next(item for item in items if item.sku == "X000010481")
        self.assertTrue(squamish.cost_unknown)
        self.assertFalse(gamma.cost_unknown)

    def test_unknown_cost_does_not_create_fake_profit_margin(self) -> None:
        excel_bytes = _workbook_bytes([
            (None, "Squamish Hoody\n男士", "X000010276", "sea salt", "店铺折扣，进价*0.7+20.33", 1299.0),
        ])

        product = score_products(build_inventory_items("", excel_bytes=excel_bytes, excel_filename="ashley.xlsx"))[0]

        self.assertTrue(product.cost_unknown)
        self.assertEqual(product.profit_margin, 0)
        self.assertEqual(product.profit, 0)
        self.assertNotEqual(_fallback_host_decision(product.score, product), "Skip")

    def test_inventory_text_or_excel_takes_priority_over_taobao_json(self) -> None:
        taobao_json = json.dumps({
            "data": {
                "dataSource": [
                    {
                        "sourceProductTitle": "Taobao Product",
                        "price": 100,
                        "adviseSalePriceHigh": 399,
                        "skuNumber": 9,
                        "targetProductStatus": 1,
                        "sourceProductId": "tb-1",
                    }
                ]
            }
        })

        items = build_inventory_items(
            inventory_text="Excel Product,10,2,100,CAD",
            taobao_json_text=taobao_json,
        )

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].product_name, "Excel Product")
        self.assertEqual(items[0].source, "manual")

    def test_single_product_name_header_is_not_treated_as_data(self) -> None:
        excel_bytes = _workbook_bytes([
            ("商品名",),
            ("Atom Vest Men's",),
            ("Beta Jacket Men's",),
        ])

        rows = parse_smart_inventory_file(excel_bytes, "names.xlsx")

        self.assertEqual([row.product_name for row in rows], ["Atom Vest Men's", "Beta Jacket Men's"])


if __name__ == "__main__":
    unittest.main()
