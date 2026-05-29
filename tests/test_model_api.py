import os
import io
import zipfile
import unittest

from fastapi.testclient import TestClient

from main import app


class ModelApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_model_readiness_endpoint_returns_summary(self) -> None:
        response = self.client.get("/api/model/readiness?product_threshold=3&host_threshold=3&min_quality=70")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("dashboard", payload)
        self.assertIn("coverage", payload)
        self.assertIn("evaluation", payload)
        self.assertIn("artifact", payload)
        self.assertIn("action_library", payload)

    def test_model_readiness_apis_return_json(self) -> None:
        routes = [
            "/api/model/training-dashboard",
            "/api/model/coverage?product_threshold=3&host_threshold=3",
            "/api/model/cold-start?product_name=Kragg%20Shirt&top_k=2",
            "/api/model/offline-evaluation?min_quality=70",
        ]
        for route in routes:
            with self.subTest(route=route):
                response = self.client.get(route)
                self.assertEqual(response.status_code, 200)
                self.assertIsInstance(response.json(), dict)

    def test_train_v0_api_handles_body(self) -> None:
        response = self.client.post("/api/model/train-v0", json={"min_quality": 70})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn(payload["status"], {"trained", "not_enough_data"})

    def test_live_ingest_endpoint_accepts_extension_payload(self) -> None:
        os.environ.pop("LIVE_INGEST_TOKEN", None)
        response = self.client.post("/api/live-ingest", json={
            "source": "chrome_extension",
            "host_id": "api-test",
            "liveId": "live-api-test",
            "metrics": {
                "online_uv": 15,
                "uv": 13439,
                "pv": 21213,
                "heat_score": 571,
                "pay_amt": 41230,
                "pay_byr_rate": 0.0201,
                "ipv_uv_rate": 0.1552,
                "stay_time_pu": 66,
            },
            "events": [{"title": "Kragg Shirt", "payBuyerCnt": 3}],
        })
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["source"], "chrome_extension")
        self.assertEqual(payload["host_id"], "api-test")

    def test_live_ingest_rejects_invalid_json(self) -> None:
        os.environ.pop("LIVE_INGEST_TOKEN", None)
        response = self.client.post(
            "/api/live-ingest",
            content="{bad-json",
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "invalid_json")

    def test_live_ingest_token_when_configured(self) -> None:
        previous = os.environ.get("LIVE_INGEST_TOKEN")
        os.environ["LIVE_INGEST_TOKEN"] = "test-token"
        try:
            payload = {
                "source": "chrome_extension",
                "host_id": "token-test",
                "liveId": "token-test",
                "metrics": {"online_uv": 1, "uv": 1, "heat_score": 1},
            }
            rejected = self.client.post("/api/live-ingest", json=payload)
            self.assertEqual(rejected.status_code, 401)
            accepted = self.client.post("/api/live-ingest", json=payload, headers={"X-Live-Ingest-Token": "test-token"})
            self.assertEqual(accepted.status_code, 200)
            self.assertTrue(accepted.json()["ok"])
        finally:
            if previous is None:
                os.environ.pop("LIVE_INGEST_TOKEN", None)
            else:
                os.environ["LIVE_INGEST_TOKEN"] = previous

    def test_chrome_extension_download_contains_required_files(self) -> None:
        response = self.client.get("/download/chrome-extension")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "application/zip")
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            names = set(archive.namelist())
        self.assertIn("manifest.json", names)
        self.assertIn("content.js", names)
        self.assertIn("page_hook.js", names)
        self.assertIn("popup.html", names)
        self.assertFalse(any("__pycache__" in name for name in names))


if __name__ == "__main__":
    unittest.main()
