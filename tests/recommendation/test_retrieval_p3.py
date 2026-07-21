from __future__ import annotations

import unittest

from inspitrip.recommendation.service import hydrate_candidates
from inspitrip.recommendation.query_plan import build_rule_query_plan
from inspitrip.recommendation.ranking import filter_and_rank


def candidate(
    destination_id: str,
    *,
    sea: float = 0.0,
    budget: int | None = 800,
    budget_filterable: bool = True,
    status: str = "active",
) -> dict:
    return {
        "destination_id": destination_id,
        "name": destination_id,
        "status": status,
        "city": "测试市",
        "category": "scenic",
        "mood_scores": {"mood_heal": 0.8},
        "vibe_scores": {"vibe_nature": 0.8},
        "activity_scores": {"act_sea": sea},
        "semantic_match": 0.8,
        "evidence_quality": 0.8,
        "freshness_score": 0.8,
        "private_discovery_value": 0.8,
        "metadata": {
            "duration_min": 2,
            "budget_typical": budget,
            "budget_filterable": budget_filterable,
        },
    }


def travel_row(
    destination_id: str,
    mode: str,
    minutes: int | None,
    *,
    railway: bool = False,
    ferry: bool = False,
    success_count: int = 1,
    failure_count: int = 0,
) -> dict:
    return {
        "destination_id": destination_id,
        "origin_city": "上海",
        "transport_mode": mode,
        "travel_minutes": minutes,
        "door_to_door_typical": minutes,
        "rail_segment_typical": 60 if railway else None,
        "railway_segments": (
            [{"trip": "G1", "type": "G字头高速动车", "duration_minutes": 60}]
            if railway
            else []
        ),
        "requires_ferry": ferry,
        "contains_ferry": ferry,
        "failure_reason": None if success_count else "amap_no_transit_route",
        "raw_status": {
            "success_count": success_count,
            "failure_count": failure_count,
        },
    }


class _InventoryRepository:
    def __init__(self, profiles: list[dict]):
        self.profiles = profiles

    def get_profiles(self, destination_ids: list[str]) -> list[dict]:
        wanted = set(destination_ids)
        return [dict(row) for row in self.profiles if row["destination_id"] in wanted]

    def get_active_profiles(self) -> list[dict]:
        return [dict(row) for row in self.profiles if row.get("status") == "active"]


class RetrievalCoverageP3Tests(unittest.TestCase):
    def test_semantic_rank_31_unique_hard_match_is_supplemented(self) -> None:
        profiles = [candidate(f"D{index:02d}") for index in range(1, 31)]
        profiles.append(candidate("D31", sea=1.0))
        retrieval_items = [
            {"metadata": {"destination_id": f"D{index:02d}", "score": 1.0 - index / 100}}
            for index in range(1, 31)
        ]
        hydrated = hydrate_candidates(
            retrieval_items,
            _InventoryRepository(profiles),
            include_active_inventory=True,
        )
        self.assertEqual(31, len(hydrated))
        supplemented = next(row for row in hydrated if row["destination_id"] == "D31")
        self.assertEqual("active_inventory", supplemented["recall_source"])
        self.assertEqual(0.0, supplemented["semantic_match"])

        result = filter_and_rank(
            hydrated,
            build_rule_query_plan("必须看海"),
            final_limit=1,
        )
        self.assertEqual("D31", result["selected"][0]["destination_id"])


class TransportSemanticsP3Tests(unittest.TestCase):
    def test_high_speed_rail_requires_a_real_railway_segment(self) -> None:
        plan = build_rule_query_plan("上海出发，必须坐高铁")
        plain_public = filter_and_rank(
            [candidate("D1")],
            plan,
            travel_rows=[travel_row("D1", "公共交通", 120, railway=False)],
            allow_unknown_hard_facts=False,
        )
        self.assertEqual([], plain_public["selected"])
        self.assertIn("transport_mode_unavailable:高铁", plain_public["rejected"][0]["reasons"])

        with_rail = filter_and_rank(
            [candidate("D1")],
            plan,
            travel_rows=[travel_row("D1", "公共交通", 120, railway=True)],
            allow_unknown_hard_facts=False,
        )
        self.assertEqual("D1", with_rail["selected"][0]["destination_id"])

    def test_ferry_requires_an_explicit_ferry_marker(self) -> None:
        plan = build_rule_query_plan("上海出发，这趟必须坐轮渡")
        without_marker = filter_and_rank(
            [candidate("D1")],
            plan,
            travel_rows=[travel_row("D1", "公共交通", 120, ferry=False)],
            allow_unknown_hard_facts=False,
        )
        self.assertIn("transport_mode_unavailable:轮渡", without_marker["rejected"][0]["reasons"])

        with_marker = filter_and_rank(
            [candidate("D1")],
            plan,
            travel_rows=[travel_row("D1", "公共交通", None, ferry=True, success_count=0)],
            allow_unknown_hard_facts=False,
        )
        self.assertEqual("D1", with_marker["selected"][0]["destination_id"])

    def test_unspecified_mode_uses_shortest_available_door_to_door_time(self) -> None:
        plan = build_rule_query_plan("上海出发，三小时内")
        result = filter_and_rank(
            [candidate("D1")],
            plan,
            travel_rows=[
                travel_row("D1", "自驾", 240),
                travel_row("D1", "公共交通", 150),
            ],
            allow_unknown_hard_facts=False,
        )
        self.assertEqual("D1", result["selected"][0]["destination_id"])

        too_slow = filter_and_rank(
            [candidate("D1")],
            plan,
            travel_rows=[
                travel_row("D1", "自驾", 240),
                travel_row("D1", "公共交通", 190),
            ],
            allow_unknown_hard_facts=False,
        )
        self.assertIn("travel_time_exceeded", too_slow["rejected"][0]["reasons"])

    def test_self_drive_and_public_transport_are_not_interchangeable(self) -> None:
        self_drive = filter_and_rank(
            [candidate("D1")],
            build_rule_query_plan("上海出发，只接受自驾"),
            travel_rows=[travel_row("D1", "公共交通", 100)],
            allow_unknown_hard_facts=False,
        )
        self.assertEqual([], self_drive["selected"])

        public = filter_and_rank(
            [candidate("D1")],
            build_rule_query_plan("上海出发，只接受公共交通"),
            travel_rows=[travel_row("D1", "公共交通", 100)],
            allow_unknown_hard_facts=False,
        )
        self.assertEqual("D1", public["selected"][0]["destination_id"])


class FactReliabilityP3Tests(unittest.TestCase):
    def test_unknown_and_partial_failure_receive_versioned_penalties(self) -> None:
        plan = build_rule_query_plan("上海出发，预算1000，三小时内")
        result = filter_and_rank(
            [
                candidate("CONFIRMED", budget=800, budget_filterable=True),
                candidate("UNCERTAIN", budget=None, budget_filterable=False),
            ],
            plan,
            travel_rows=[
                travel_row("CONFIRMED", "公共交通", 120),
                travel_row("UNCERTAIN", "公共交通", 120, failure_count=2),
            ],
            allow_unknown_hard_facts=True,
            final_limit=2,
        )
        by_id = {row["destination_id"]: row for row in result["eligible"]}
        self.assertGreater(by_id["CONFIRMED"]["final_score"], by_id["UNCERTAIN"]["final_score"])
        self.assertEqual("p3-v1", by_id["UNCERTAIN"]["fact_adjustment"]["version"])
        self.assertIn("budget_unknown", by_id["UNCERTAIN"]["assumptions"])
        self.assertIn("travel_partial_failure", by_id["UNCERTAIN"]["assumptions"])
        self.assertGreater(by_id["UNCERTAIN"]["fact_adjustment"]["penalty"], 0)

    def test_rejection_diagnostics_are_grouped_by_constraint_and_code(self) -> None:
        plan = build_rule_query_plan("上海出发，预算500，必须坐高铁")
        result = filter_and_rank(
            [candidate("OVER", budget=900), candidate("INACTIVE", budget=400, status="inactive")],
            plan,
            travel_rows=[
                travel_row("OVER", "公共交通", 100, railway=False),
                travel_row("INACTIVE", "公共交通", 100, railway=False),
            ],
            allow_unknown_hard_facts=False,
        )
        diagnostics = result["rejection_diagnostics"]
        self.assertEqual("p3-v1", diagnostics["version"])
        self.assertEqual(2, diagnostics["total_rejected"])
        self.assertEqual(1, diagnostics["by_code"]["budget_exceeded"])
        self.assertEqual(2, diagnostics["by_code"]["transport_mode_unavailable"])
        self.assertGreaterEqual(diagnostics["by_constraint"]["transport"], 2)
        self.assertTrue(all(item["reason_details"] for item in diagnostics["items"]))


if __name__ == "__main__":
    unittest.main()
