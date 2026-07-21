from __future__ import annotations

import unittest

from inspitrip.recommendation.query_plan import build_rule_query_plan
from inspitrip.recommendation.query_runtime import QueryStateStore, resolve_query_turn


class QueryRuntimeTests(unittest.TestCase):
    def test_invalid_planner_output_uses_rule_plan_and_blocks_non_domain(self) -> None:
        result = resolve_query_turn(
            raw_query="周末想去云南",
            planner_output="{not json}",
        )
        self.assertEqual("out_of_region", result["query_plan"]["scope"])
        self.assertFalse(result["enter_retrieval"])

    def test_missing_required_slots_clarify_once_then_merge_second_turn(self) -> None:
        store = QueryStateStore()
        first_query = "想找三小时内能到、安静看海的地方"
        first = resolve_query_turn(
            raw_query=first_query,
            planner_output=build_rule_query_plan(first_query),
            conversation_id="conversation-1",
            store=store,
        )
        self.assertTrue(first["clarification"]["should_clarify"])
        self.assertFalse(first["enter_retrieval"])
        self.assertEqual(1, first["clarification_count"])
        self.assertEqual(
            [
                "hard_constraints.origin",
                "hard_constraints.budget_max",
                "hard_constraints.days_max",
            ],
            first["clarification"]["missing_slots"],
        )

        second_query = "上海出发，人均1000元，两天"
        second = resolve_query_turn(
            raw_query=second_query,
            planner_output=build_rule_query_plan(second_query),
            conversation_id="conversation-1",
            store=store,
        )
        hard = second["query_plan"]["hard_constraints"]
        self.assertEqual("上海", hard["origin"])
        self.assertEqual(1000, hard["budget_max"])
        self.assertEqual(2, hard["days_max"])
        self.assertEqual(180, hard["travel_time_max"])
        self.assertFalse(second["clarification"]["should_clarify"])
        self.assertTrue(second["enter_retrieval"])

    def test_required_slots_remain_blocking_after_one_clarification(self) -> None:
        store = QueryStateStore()
        query = "三小时内能到的地方"
        resolve_query_turn(
            raw_query=query,
            planner_output=build_rule_query_plan(query),
            conversation_id="conversation-2",
            store=store,
        )
        second = resolve_query_turn(
            raw_query="还是先推荐吧",
            planner_output=build_rule_query_plan("还是先推荐吧"),
            conversation_id="conversation-2",
            store=store,
        )
        self.assertEqual("required_slots_still_missing", second["clarification"]["reason"])
        self.assertTrue(second["clarification"]["should_clarify"])
        self.assertEqual(1, second["clarification_count"])
        self.assertFalse(second["enter_retrieval"])

    def test_complete_required_slots_enter_retrieval_without_clarification(self) -> None:
        query = "上海出发，人均1000元，玩两天，想安静看海"
        result = resolve_query_turn(
            raw_query=query,
            planner_output=build_rule_query_plan(query),
        )
        self.assertFalse(result["clarification"]["should_clarify"])
        self.assertEqual([], result["clarification"]["missing_slots"])
        self.assertTrue(result["enter_retrieval"])

    def test_store_is_lru_bounded_and_expiring(self) -> None:
        now = [0.0]
        store = QueryStateStore(max_entries=1, ttl_seconds=10, clock=lambda: now[0])
        plan = build_rule_query_plan("想放松")
        store.set("a", plan, 0)
        store.set("b", plan, 0)
        self.assertIsNone(store.get("a"))
        self.assertIsNotNone(store.get("b"))
        now[0] = 11.0
        self.assertIsNone(store.get("b"))


if __name__ == "__main__":
    unittest.main()
