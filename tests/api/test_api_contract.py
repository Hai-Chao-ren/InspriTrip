from __future__ import annotations

import os
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from inspitrip.api.app import app
from inspitrip.recommendation.query_plan import build_rule_query_plan


class ApiContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_health_reports_demo_mode_by_default(self) -> None:
        with patch.dict(os.environ, {"INSPITRIP_MODE": "demo"}, clear=False):
            response = self.client.get("/api/health")
        self.assertEqual(200, response.status_code)
        self.assertEqual("demo", response.json()["mode"])

    def test_root_serves_public_portfolio(self) -> None:
        response = self.client.get("/")
        self.assertEqual(200, response.status_code)
        self.assertIn("InspiTrip", response.text)
        self.assertIn("合成数据交互演示", response.text)

    def test_demo_chat_requests_all_missing_hard_slots_before_ranking(self) -> None:
        response = self.client.post(
            "/api/v2/chat",
            json={"query": "想安静看海", "user": "contract-test"},
        )
        payload = response.json()
        self.assertEqual(200, response.status_code)
        self.assertEqual("message", payload["kind"])
        self.assertEqual("origin", payload["needs_clarification"]["field"])
        self.assertEqual(["origin", "budget", "days"], payload["needs_clarification"]["fields"])
        self.assertEqual("ask_all_once", payload["needs_clarification"]["mode"])

    def test_demo_chat_requests_only_the_still_missing_hard_slots(self) -> None:
        response = self.client.post(
            "/api/v2/chat",
            json={"query": "想安静看海", "origin": "上海", "user": "contract-test"},
        )
        payload = response.json()
        self.assertEqual("message", payload["kind"])
        self.assertEqual(["budget", "days"], payload["needs_clarification"]["fields"])

    def test_demo_chat_extracts_required_slots_from_natural_language(self) -> None:
        response = self.client.post(
            "/api/v2/chat",
            json={
                "query": "上海出发，人均1000元，玩两天，想安静看海",
                "user": "contract-test",
            },
        )
        payload = response.json()
        self.assertEqual(200, response.status_code)
        self.assertEqual("recommendations", payload["kind"])
        self.assertNotIn("needs_clarification", payload)

    def test_demo_chat_returns_structured_recommendations(self) -> None:
        response = self.client.post(
            "/api/v2/chat",
            json={
                "query": "想安静看海，不要太商业化",
                "origin": "上海",
                "budget": 1000,
                "days": 2,
                "user": "contract-test",
            },
        )
        payload = response.json()
        self.assertEqual(200, response.status_code)
        self.assertEqual("recommendations", payload["kind"])
        self.assertTrue(payload["recommendations"])
        self.assertTrue(all(row["synthetic"] for row in payload["recommendations"]))

    def test_demo_chat_does_not_expose_source_urls_or_author_ids(self) -> None:
        response = self.client.post(
            "/api/v2/chat",
            json={"query": "想看海", "origin": "上海", "budget": 1000, "days": 2},
        )
        text = response.text.lower()
        self.assertNotIn("source_url", text)
        self.assertNotIn("author_hash", text)
        self.assertNotIn("xiaohongshu", text)

    def test_full_mode_without_dify_returns_clear_503(self) -> None:
        environment = {
            "INSPITRIP_MODE": "full",
            "DIFY_APP_API_BASE": "",
            "DIFY_APP_API_KEY": "",
        }
        with patch.dict(os.environ, environment, clear=False):
            response = self.client.post(
                "/api/v2/chat",
                json={"query": "想看海", "origin": "上海"},
            )
        self.assertEqual(503, response.status_code)
        self.assertIn("Dify", response.json()["error"])

    def test_query_plan_resolve_preserves_public_contract(self) -> None:
        response = self.client.post(
            "/api/v2/query_plan/resolve",
            json={
                "raw_query": "上海出发，周末想安静看海",
                "form_values": {"origin": "上海", "days": 2},
                "conversation_id": "contract-query-plan",
            },
        )
        self.assertEqual(200, response.status_code)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertIn("query_plan", payload)
        self.assertEqual(["hard_constraints.budget_max"], payload["clarification"]["missing_slots"])
        self.assertFalse(payload["enter_retrieval"])

    def test_rank_candidates_uses_demo_inventory(self) -> None:
        plan = build_rule_query_plan(
            "想安静看海",
            form_values={"origin": "上海", "budget": 1000, "days": 2},
        )
        response = self.client.post(
            "/api/v2/rank_candidates",
            json={"raw_query": "想安静看海", "query_plan": plan, "retrieval_items": []},
        )
        self.assertEqual(200, response.status_code)
        self.assertTrue(response.json()["selected"])

    def test_output_validation_returns_stable_shape(self) -> None:
        response = self.client.post(
            "/api/v2/output/validate",
            json={"llm_output": None, "selected": [], "live_context": {}},
        )
        self.assertEqual(200, response.status_code)
        self.assertIn("validation", response.json())
        self.assertIn("fact_cards", response.json())

    def test_reverse_location_without_key_is_transparent(self) -> None:
        with patch("inspitrip.api.routes.location.AmapRoutePlanner", side_effect=RuntimeError("AMAP_KEY missing")):
            response = self.client.post(
                "/api/location/reverse",
                json={"longitude": 121.47, "latitude": 31.23},
            )
        self.assertEqual(503, response.status_code)
        self.assertNotIn("longitude", response.text)

    def test_experiment_assignment_is_stable(self) -> None:
        payload = {"experiment_id": "location_prompt_timing_v1", "anonymous_user_id": "stable-user"}
        first = self.client.post("/api/experiments/assign", json=payload).json()
        second = self.client.post("/api/experiments/assign", json=payload).json()
        self.assertEqual(first["variant"], second["variant"])

    def test_analytics_endpoint_accepts_whitelisted_payload(self) -> None:
        fake_store = Mock()
        fake_store.record.return_value = 1
        event = {
            "event_id": "evt-1",
            "event_name": "session_start",
            "anonymous_user_id": "user-1",
            "session_id": "session-1",
            "properties": {"is_demo": True},
        }
        with patch("inspitrip.api.routes.analytics.get_store", return_value=fake_store):
            response = self.client.post("/api/analytics/events", json={"events": [event]})
        self.assertEqual(200, response.status_code)
        self.assertEqual(1, response.json()["accepted"])


if __name__ == "__main__":
    unittest.main()
