from __future__ import annotations

from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import app as app_module
from papermint_analytics import AnalyticsStore
from papermint_auth import AuthStore


class AnalyticsTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp(prefix="papermint-analytics-tests-"))
        self.db = self.directory / "app.sqlite3"
        self.analytics = AnalyticsStore(sqlite_path=self.db)
        self.auth = AuthStore(sqlite_path=self.db)
        self.client = TestClient(app_module.app)

    def tearDown(self):
        shutil.rmtree(self.directory, ignore_errors=True)

    def test_aggregate_summary_counts_unique_visitors(self):
        self.analytics.record("page_view", visitor_hash="a", path="/", source="direct")
        self.analytics.record("page_view", visitor_hash="a", path="/", source="direct")
        self.analytics.record("page_view", visitor_hash="b", path="/tools/merge", source="google")
        self.analytics.record("tool_open", visitor_hash="a", tool_id="merge")
        summary = self.analytics.summary(30)
        self.assertEqual(summary["totals"]["visitors"], 2)
        self.assertEqual(summary["totals"]["pageviews"], 3)
        self.assertEqual(summary["top_tools"][0], {"name": "merge", "count": 1})

    def test_event_endpoint_and_admin_access(self):
        with patch.object(app_module, "ANALYTICS_STORE", self.analytics):
            response = self.client.post(
                "/api/analytics/event",
                json={"event": "page_view", "path": "/", "referrer": "https://www.google.com/"},
            )
        self.assertEqual(response.status_code, 204)
        self.assertEqual(self.analytics.summary()["sources"][0]["name"], "google")

        with patch.object(app_module, "ANALYTICS_STORE", self.analytics):
            self.assertEqual(self.client.get("/api/analytics/summary").status_code, 401)

        user = self.auth.register("pdfaspect@gmail.com", "strong-password")
        token = self.auth.create_session(user.id)
        self.client.cookies.set(app_module.AUTH_COOKIE, token)
        with (
            patch.object(app_module, "AUTH_STORE", self.auth),
            patch.object(app_module, "ANALYTICS_STORE", self.analytics),
        ):
            response = self.client.get("/api/analytics/summary")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["totals"]["pageviews"], 1)


if __name__ == "__main__":
    unittest.main()
