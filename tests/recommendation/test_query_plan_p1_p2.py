from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

from jsonschema import Draft7Validator


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from inspitrip.recommendation.query_plan import (
    build_rule_query_delta,
    build_rule_query_plan,
    normalize_query_plan,
    parse_query_plan_output,
    should_enter_retrieval,
)
from inspitrip.recommendation.query_state import decide_clarification, merge_query_plan


SCHEMA_PATH = ROOT / "schemas" / "query_plan_schema.json"
STATE_SCHEMA_PATH = ROOT / "schemas" / "query_state_schema.json"


def tag_ids(plan: dict, dimension: str) -> set[str]:
    return {
        str(item["id"])
        for item in plan["soft_preferences"][dimension]
    }


class QueryPlanP1Tests(unittest.TestCase):
    def test_phrase_level_activity_modality(self) -> None:
        plan = build_rule_query_plan("必须看海，顺便喝咖啡")
        self.assertEqual(["act_sea"], plan["hard_constraints"]["must_have_activities"])
        self.assertEqual({"act_cafe"}, tag_ids(plan, "activity"))

    def test_negative_activity_is_excluded_never_required(self) -> None:
        plan = build_rule_query_plan("不想徒步，只想看海")
        self.assertNotIn("act_hike", plan["hard_constraints"]["must_have_activities"])
        self.assertNotIn("act_hike", tag_ids(plan, "activity"))
        self.assertIn("act_hike", plan["exclusions"])
        self.assertIn("act_sea", plan["hard_constraints"]["must_have_activities"])

    def test_double_negative_is_not_treated_as_exclusion(self) -> None:
        plan = build_rule_query_plan("不是不能徒步，但更想看海")
        self.assertNotIn("act_hike", plan["exclusions"])
        self.assertIn("act_hike", tag_ids(plan, "activity"))
        self.assertIn("act_sea", plan["hard_constraints"]["must_have_activities"])

    def test_chinese_numbers_and_ranges_use_upper_bound(self) -> None:
        plan = build_rule_query_plan("上海出发，预算一千五到两千，两到三天，三小时以内")
        hard = plan["hard_constraints"]
        self.assertEqual(2000, hard["budget_max"])
        self.assertEqual(3, hard["days_max"])
        self.assertEqual(180, hard["travel_time_max"])

    def test_half_hour_and_chinese_budget(self) -> None:
        plan = build_rule_query_plan("杭州出发，一个半小时内，预算一千五以内")
        self.assertEqual(90, plan["hard_constraints"]["travel_time_max"])
        self.assertEqual(1500, plan["hard_constraints"]["budget_max"])

    def test_non_commercialized_is_positive_vibe_and_exclusion(self) -> None:
        plan = build_rule_query_plan("想找不商业化、没那么网红的安静地方")
        self.assertIn("vibe_unspoiled", tag_ids(plan, "vibe"))
        self.assertIn("商业化", plan["exclusions"])
        self.assertIn("网红", plan["exclusions"])
        self.assertIn("commercialization", plan["evidence_aspects"])
        self.assertIn("crowd", plan["evidence_aspects"])

    def test_scope_and_task_routes(self) -> None:
        out_of_region = build_rule_query_plan("周末想去云南看雪山")
        self.assertEqual(("out_of_region", "unsupported"), (out_of_region["scope"], out_of_region["task_type"]))
        self.assertFalse(should_enter_retrieval(out_of_region))

        chitchat = build_rule_query_plan("你好，你是谁？")
        self.assertEqual(("not_travel", "chitchat"), (chitchat["scope"], chitchat["task_type"]))
        self.assertFalse(should_enter_retrieval(chitchat))

        service = build_rule_query_plan("帮我订一家上海的酒店")
        self.assertEqual(("not_supported_yet", "unsupported"), (service["scope"], service["task_type"]))
        self.assertFalse(should_enter_retrieval(service))

        lookup = build_rule_query_plan("青岚岛怎么玩，有哪些值得去的景点？")
        self.assertEqual(("in_domain", "experience_lookup"), (lookup["scope"], lookup["task_type"]))
        self.assertEqual("示例·青岚岛", lookup["target_destination"])
        self.assertTrue(should_enter_retrieval(lookup))

    def test_non_domain_plan_is_cleared(self) -> None:
        plan = normalize_query_plan(
            {
                "scope": "out_of_region",
                "task_type": "destination_discovery",
                "target_destination": "云南",
                "hard_constraints": {"origin": "上海", "budget_max": 1000},
                "exclusions": ["拥挤"],
                "semantic_query": "云南",
                "soft_preferences": {"mood": ["mood_heal"]},
                "evidence_aspects": ["scenery"],
            }
        )
        self.assertEqual("unsupported", plan["task_type"])
        self.assertIsNone(plan["target_destination"])
        self.assertEqual("", plan["semantic_query"])
        self.assertEqual([], plan["exclusions"])
        self.assertEqual([], plan["evidence_aspects"])

    def test_invalid_json_falls_back_to_rule_plan(self) -> None:
        raw_query = "上海出发，想安静看海"
        fallback = parse_query_plan_output("```json\n{not json}\n```", raw_query=raw_query)
        self.assertEqual(build_rule_query_plan(raw_query), fallback)

    def test_unknown_enum_falls_back_instead_of_default_allow(self) -> None:
        raw_query = "去云南旅行"
        invalid = build_rule_query_plan("上海周末想看海")
        invalid["scope"] = "maybe_in_domain"
        result = parse_query_plan_output(json.dumps(invalid, ensure_ascii=False), raw_query=raw_query)
        self.assertEqual("out_of_region", result["scope"])
        self.assertFalse(should_enter_retrieval(result))

    def test_rewrite_fallback_never_adds_unmentioned_facts(self) -> None:
        raw_query = "想找个安静点的地方"
        plan = build_rule_query_plan(raw_query)
        self.assertNotRegex(plan["semantic_query"], r"看海|徒步|古镇|咖啡|上海|杭州|苏州|\d")

    def test_query_plan_schema_is_strict_and_taxonomy_bound(self) -> None:
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        plan = build_rule_query_plan("上海出发，想安静看海")
        self.assertEqual([], list(Draft7Validator(schema).iter_errors(plan)))
        plan["soft_preferences"]["mood"] = [{"id": "mood_invented", "confidence": 0.9}]
        self.assertTrue(list(Draft7Validator(schema).iter_errors(plan)))

    def test_coordinated_and_verb_negations_use_latest_user_intent(self) -> None:
        coordinated = build_rule_query_plan("不要徒步和露营，只想逛老街")
        self.assertEqual(["act_town"], coordinated["hard_constraints"]["must_have_activities"])
        self.assertEqual({"act_hike", "act_camp"}, set(coordinated["exclusions"]))

        corrected = build_rule_query_plan("上次不想看海，这次必须看海")
        self.assertEqual(["act_sea"], corrected["hard_constraints"]["must_have_activities"])
        self.assertNotIn("act_sea", corrected["exclusions"])

        reported = build_rule_query_plan("朋友说别爬山，但我本人想徒步")
        self.assertEqual(["act_hike"], reported["hard_constraints"]["must_have_activities"])
        self.assertNotIn("act_hike", reported["exclusions"])

    def test_weak_suffixes_do_not_create_hard_activity(self) -> None:
        for query, activity in (
            ("想看自然风景，露营不是必须", "act_camp"),
            ("想找灵感，美术馆不是硬要求", "act_art"),
            ("想放松，能骑行更好", "act_ride"),
            ("想治愈一下，有温泉更好", "act_hotspring"),
            ("想去文艺点的地方，咖啡店有就更好", "act_cafe"),
        ):
            with self.subTest(query=query):
                plan = build_rule_query_plan(query)
                self.assertNotIn(activity, plan["hard_constraints"]["must_have_activities"])
                self.assertIn(activity, tag_ids(plan, "activity"))

    def test_conditional_negative_demotes_activity_instead_of_excluding(self) -> None:
        plan = build_rule_query_plan("如果天气不好就不露营")
        self.assertNotIn("act_camp", plan["hard_constraints"]["must_have_activities"])
        self.assertNotIn("act_camp", plan["exclusions"])
        self.assertIn("act_camp", tag_ids(plan, "activity"))
        self.assertIn("weather_season", plan["evidence_aspects"])

    def test_non_activity_negation_and_cancellation(self) -> None:
        plan = build_rule_query_plan("拒绝网红和人挤人，也不想去商业化严重的地方")
        self.assertEqual({"网红", "拥挤", "商业化"}, set(plan["exclusions"]))
        cancelled = build_rule_query_plan("不要求人少，商业化不是硬伤")
        self.assertEqual([], cancelled["exclusions"])

    def test_soft_transport_is_not_hard_mode(self) -> None:
        self.assertEqual([], build_rule_query_plan("地铁能到最好，但不是必须")["hard_constraints"]["transport_modes"])
        self.assertEqual([], build_rule_query_plan("上海出发，三小时内，高铁优先")["hard_constraints"]["transport_modes"])

    def test_budget_unit_soft_limit_and_compound_amount(self) -> None:
        self.assertEqual(2000, build_rule_query_plan("最多花2k")["hard_constraints"]["budget_max"])
        self.assertIsNone(build_rule_query_plan("人均三百左右，不是硬上限")["hard_constraints"]["budget_max"])
        self.assertEqual(650, build_rule_query_plan("人均不要超过六百五十元")["hard_constraints"]["budget_max"])
        self.assertEqual(2000, build_rule_query_plan("苏州出发，最多三天两千元，公共交通四小时内")["hard_constraints"]["budget_max"])

    def test_route_boundaries_cover_ticket_abroad_chitchat_and_lookup(self) -> None:
        for query in ("帮我买景区门票", "查完整轮渡班次并替我订票"):
            with self.subTest(query=query):
                plan = build_rule_query_plan(query)
                self.assertEqual(("not_supported_yet", "unsupported"), (plan["scope"], plan["task_type"]))
        self.assertEqual("out_of_region", build_rule_query_plan("想去国外海岛度假")["scope"])
        self.assertEqual("not_travel", build_rule_query_plan("你好呀")["scope"])
        self.assertEqual("not_travel", build_rule_query_plan("今天心情怎么样")["scope"])
        lookup = build_rule_query_plan("青岚岛去那里做什么")
        self.assertEqual(("experience_lookup", "示例·青岚岛"), (lookup["task_type"], lookup["target_destination"]))


class QueryStateP2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.first = build_rule_query_plan("上海出发，周末想一个人安静看海")

    def test_second_turn_budget_preserves_origin_and_mood(self) -> None:
        merged = merge_query_plan(self.first, build_rule_query_delta("预算1500以内"))
        self.assertEqual("上海", merged["hard_constraints"]["origin"])
        self.assertEqual(1500, merged["hard_constraints"]["budget_max"])
        self.assertIn("mood_unwind", tag_ids(merged, "mood"))

    def test_explicit_budget_overrides_form_and_previous(self) -> None:
        previous = build_rule_query_plan("上海出发，预算1000，想放松")
        merged = merge_query_plan(
            previous,
            build_rule_query_delta("预算改成1500"),
            form_values={"budget_max": 1200},
        )
        self.assertEqual(1500, merged["hard_constraints"]["budget_max"])

    def test_form_value_overrides_previous_when_turn_does_not_change_slot(self) -> None:
        previous = build_rule_query_plan("上海出发，预算1000，想放松")
        merged = merge_query_plan(
            previous,
            build_rule_query_delta("再安静一点"),
            form_values={"budget_max": 1200},
        )
        self.assertEqual(1200, merged["hard_constraints"]["budget_max"])

    def test_budget_unlimited_clears_old_value(self) -> None:
        previous = build_rule_query_plan("上海出发，预算1000，想放松")
        merged = merge_query_plan(previous, build_rule_query_delta("预算不限了"))
        self.assertIsNone(merged["hard_constraints"]["budget_max"])
        self.assertNotIn("cost", merged["evidence_aspects"])

    def test_incremental_preference_and_negative_activity_merge(self) -> None:
        previous = build_rule_query_plan("上海出发，周末想看海徒步")
        merged = merge_query_plan(previous, build_rule_query_delta("再安静一点，但不要徒步"))
        self.assertIn("mood_unwind", tag_ids(merged, "mood"))
        self.assertNotIn("act_hike", merged["hard_constraints"]["must_have_activities"])
        self.assertIn("act_hike", merged["exclusions"])
        self.assertIn("act_sea", merged["hard_constraints"]["must_have_activities"])

    def test_clarifies_missing_origin_once_only(self) -> None:
        plan = build_rule_query_plan("想找三小时内能到的地方")
        first = decide_clarification(plan, 0)
        self.assertTrue(first["should_clarify"])
        self.assertEqual(["hard_constraints.origin"], first["missing_slots"])
        second = decide_clarification(plan, 1)
        self.assertFalse(second["should_clarify"])
        self.assertEqual("clarification_limit_reached", second["reason"])

    def test_does_not_clarify_non_blocking_missing_slots(self) -> None:
        decision = decide_clarification(build_rule_query_plan("想放松一下"), 0)
        self.assertFalse(decision["should_clarify"])
        self.assertEqual("enough_information", decision["reason"])

    def test_query_state_schema_accepts_one_clarification_state(self) -> None:
        schema = json.loads(STATE_SCHEMA_PATH.read_text(encoding="utf-8"))
        state = {
            "version": "1.0",
            "plan": self.first,
            "clarification_count": 1,
            "pending_clarification": None,
        }
        resolver = Draft7Validator(schema)
        self.assertEqual([], list(resolver.iter_errors(state)))

    def test_granular_activity_demotion_and_removal(self) -> None:
        previous = build_rule_query_plan("必须看海和徒步")
        merged = merge_query_plan(previous, build_rule_query_delta("徒步去掉"))
        self.assertEqual(["act_sea"], merged["hard_constraints"]["must_have_activities"])
        self.assertIn("act_hike", merged["exclusions"])

        previous = build_rule_query_plan("想文艺看展")
        merged = merge_query_plan(previous, build_rule_query_delta("看展不是必须，咖啡店加分"))
        self.assertNotIn("act_art", merged["hard_constraints"]["must_have_activities"])
        self.assertEqual({"act_cafe"}, tag_ids(merged, "activity"))

    def test_state_cancellation_and_replacement_cues(self) -> None:
        previous = build_rule_query_plan("想去不商业化的地方")
        self.assertEqual([], merge_query_plan(previous, build_rule_query_delta("商业化无所谓了"))["exclusions"])

        previous = build_rule_query_plan("想小众人少")
        merged = merge_query_plan(previous, build_rule_query_delta("不用人少了，想和朋友热闹点"))
        self.assertEqual([], merged["exclusions"])
        self.assertEqual({"mood_social"}, tag_ids(merged, "mood"))
        self.assertNotIn("vibe_niche", tag_ids(merged, "vibe"))

    def test_state_budget_and_origin_update_phrases(self) -> None:
        previous = build_rule_query_plan("上海出发，预算1000")
        merged = merge_query_plan(previous, build_rule_query_delta("预算补充到1500以内"))
        self.assertEqual(1500, merged["hard_constraints"]["budget_max"])
        merged = merge_query_plan(merged, build_rule_query_delta("预算加到1800"))
        self.assertEqual(1800, merged["hard_constraints"]["budget_max"])
        merged = merge_query_plan(merged, build_rule_query_delta("改成杭州出发"))
        self.assertEqual("杭州", merged["hard_constraints"]["origin"])


if __name__ == "__main__":
    unittest.main()
