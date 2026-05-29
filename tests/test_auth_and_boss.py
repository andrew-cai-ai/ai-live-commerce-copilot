import os
import unittest

from fastapi.testclient import TestClient

from app.services.auth import AUTH_COOKIE_NAME
from app.services.live_data_connector import LiveDataConnector
from app.services.security_utils import safe_compare_digest
from main import app


class SecurityUtilsTests(unittest.TestCase):
    def test_safe_compare_digest_rejects_length_mismatch(self) -> None:
        self.assertFalse(safe_compare_digest("", "abcdef"))
        self.assertTrue(safe_compare_digest("secret", "secret"))


class BossDashboardTests(unittest.TestCase):
    def test_boss_dashboard_with_workspace_filter(self) -> None:
        connector = LiveDataConnector()
        connector.ingest_live_metrics({
            "source": "chrome_extension",
            "host_id": "boss-test-host",
            "workspace_id": "store-alpha",
            "liveId": "live-boss-test",
            "metrics": {
                "online_uv": 20,
                "uv": 200,
                "pv": 400,
                "heat_score": 400,
                "pay_amt": 5000,
                "pay_byr_rate": 0.02,
                "ipv_uv_rate": 0.1,
                "comment_uv": 5,
            },
        })
        dashboard = connector.boss_dashboard(workspace_id="store-alpha")
        self.assertEqual(dashboard["workspace_id"], "store-alpha")
        self.assertGreaterEqual(dashboard["active_count"], 1)
        self.assertTrue(dashboard["rooms"])


class AuthApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)
        self.previous_password = os.environ.get("APP_PASSWORD")
        os.environ["APP_PASSWORD"] = "test-password"

    def tearDown(self) -> None:
        if self.previous_password is None:
            os.environ.pop("APP_PASSWORD", None)
        else:
            os.environ["APP_PASSWORD"] = self.previous_password

    def test_protected_api_returns_401_without_cookie(self) -> None:
        response = self.client.get("/api/live/sessions")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"], "unauthorized")

    def test_protected_api_accepts_auth_cookie(self) -> None:
        response = self.client.get("/api/live/sessions")
        self.assertEqual(response.status_code, 401)
        authed = TestClient(app)
        login = authed.post("/login", data={"password": "test-password"}, follow_redirects=False)
        self.assertEqual(login.status_code, 303)
        cookie = login.cookies.get(AUTH_COOKIE_NAME)
        self.assertTrue(cookie)
        authed.cookies.set(AUTH_COOKIE_NAME, cookie)
        ok = authed.get("/api/live/sessions")
        self.assertEqual(ok.status_code, 200)
        self.assertIn("sessions", ok.json())

    def test_wrong_length_cookie_does_not_500(self) -> None:
        self.client.cookies.set(AUTH_COOKIE_NAME, "short")
        response = self.client.get("/api/live/sessions")
        self.assertEqual(response.status_code, 401)


class IngestSecurityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)
        self.previous_token = os.environ.get("LIVE_INGEST_TOKEN")
        self.previous_render = os.environ.get("RENDER")

    def tearDown(self) -> None:
        if self.previous_token is None:
            os.environ.pop("LIVE_INGEST_TOKEN", None)
        else:
            os.environ["LIVE_INGEST_TOKEN"] = self.previous_token
        if self.previous_render is None:
            os.environ.pop("RENDER", None)
        else:
            os.environ["RENDER"] = self.previous_render

    def test_ingest_rejects_missing_token_header_without_500(self) -> None:
        os.environ["LIVE_INGEST_TOKEN"] = "ingest-secret"
        os.environ.pop("RENDER", None)
        response = self.client.post("/api/live-ingest", json={
            "source": "chrome_extension",
            "host_id": "token-missing",
            "metrics": {"online_uv": 1, "uv": 1, "heat_score": 1},
        })
        self.assertEqual(response.status_code, 401)

    def test_render_requires_ingest_token_configuration(self) -> None:
        os.environ.pop("LIVE_INGEST_TOKEN", None)
        os.environ["RENDER"] = "true"
        response = self.client.post("/api/live-ingest", json={
            "source": "chrome_extension",
            "host_id": "render-blocked",
            "metrics": {"online_uv": 1, "uv": 1, "heat_score": 1},
        })
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"], "ingest_token_not_configured")


if __name__ == "__main__":
    unittest.main()
