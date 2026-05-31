import unittest

from fastapi.testclient import TestClient

from app.services.ecc_live_quality import (
    capability_surface,
    extension_link_gate,
    inventory_quality_gate,
)
from app.services.inventory_import import SmartInventoryRow
from main import app


class EccLiveQualityTests(unittest.TestCase):
    def test_ashley_style_inventory_promotes_even_when_inventory_is_in_notes(self) -> None:
        rows = [
            SmartInventoryRow(
                product_name="Beta Jacket Men's",
                sku="X000010511",
                color="Spotlight",
                cost_price=340,
                inventory=None,
                target_price=2399,
                notes="硬壳冲锋衣特价！按照erp更新库存。已做",
            ),
            SmartInventoryRow(
                product_name="Atom Vest Men's",
                sku="X000009559",
                color="Black",
                cost_price=210,
                inventory=None,
                target_price=1499,
                notes="特价。按照erp更新库存。已做",
            ),
            SmartInventoryRow(
                product_name="Kragg Cotton Shirt LS",
                sku="X000009714",
                color="White Light",
                cost_price=70,
                inventory=None,
                target_price=499,
                notes="店内折扣。按照官网库存更新。已做",
            ),
            SmartInventoryRow(
                product_name="Rho Bottom",
                sku="X000007308",
                color="Stratus",
                cost_price=65,
                inventory=None,
                target_price=499,
                notes="奥莱。xs按照奥莱官网更新库存。已做",
            ),
            SmartInventoryRow(
                product_name="Gamma pant men",
                sku="X000010481",
                color="Tatsu",
                cost_price=202.33,
                inventory=None,
                target_price=1229,
                notes="店内折扣。按照官网库存更新。",
            ),
        ]

        gate = inventory_quality_gate(rows)

        self.assertEqual(gate["decision"], "Promote")
        self.assertGreaterEqual(gate["score"], 80)
        self.assertTrue(any(check["name"] == "Inventory Evidence" and check["status"] == "Warn" for check in gate["checks"]))

    def test_inventory_gate_blocks_empty_import(self) -> None:
        gate = inventory_quality_gate([])

        self.assertEqual(gate["decision"], "Blocked")
        self.assertEqual(gate["score"], 0)

    def test_extension_gate_requires_capture_parse_and_send(self) -> None:
        gate = extension_link_gate({
            "activeTabMatches": True,
            "contentScriptInjected": True,
            "pageHookInjected": True,
            "capturedTargetApi": True,
            "lastParseSuccess": True,
            "lastSendSuccess": True,
            "workspaceId": "ashley",
        })

        self.assertEqual(gate["decision"], "Ready")
        self.assertGreaterEqual(gate["score"], 90)

    def test_capability_surface_separates_daily_from_library(self) -> None:
        surface = capability_surface()

        self.assertIn("daily", surface)
        self.assertIn("library", surface)
        self.assertTrue(any(item["key"] == "excel_import_gate" for item in surface["daily"]))
        self.assertTrue(any(item["key"] == "deep_market_research" for item in surface["library"]))


class EccLiveApiTests(unittest.TestCase):
    def test_ecc_capabilities_endpoint(self) -> None:
        client = TestClient(app)

        response = client.get("/api/ecc/capabilities")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["schema"], "ecc_live_capabilities_v1")
        self.assertIn("daily", payload["surface"])
        self.assertIn("library", payload["surface"])


if __name__ == "__main__":
    unittest.main()
