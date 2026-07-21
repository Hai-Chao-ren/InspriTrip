from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from inspitrip.recommendation.query_plan import build_rule_query_plan, normalize_query_plan
from inspitrip.recommendation.ranking import filter_and_rank, score_candidate
from inspitrip.recommendation.build_destination_docs import build_metadata, render_destination_document
from inspitrip.recommendation.claim_reranker import XinferenceClaimReranker
from inspitrip.recommendation.repository import JsonlRecommendationRepository
from inspitrip.recommendation.v2_pipeline import build_v2_dataset, infer_polarity
from inspitrip.recommendation.service import attach_evidence, rank_retrieval_items


def profile(
    destination_id: str,
    name: str,
    *,
    city: str,
    category: str,
    mood: dict[str, float],
    vibe: dict[str, float],
    activity: dict[str, float],
    semantic: float = 0.7,
    budget: int | None = 800,
    budget_filterable: bool = True,
) -> dict:
    return {
        "destination_id": destination_id,
        "name": name,
        "aliases": [],
        "city": city,
        "province": "浙江",
        "category": category,
        "status": "active",
        "mood_scores": mood,
        "vibe_scores": vibe,
        "activity_scores": activity,
        "core_feeling": "适合安静放空",
        "atmosphere": "自然安静",
        "suitable_scenes": ["一个人看海发呆"],
        "activities": ["看海赶海"],
        "limitations": [],
        "positive_evidence_count": 3,
        "limitation_evidence_count": 0,
        "evidence_quality": 0.8,
        "freshness_score": 0.9,
        "private_discovery_value": 0.7,
        "source_count": 3,
        "semantic_match": semantic,
        "metadata": {
            "duration_min": 2,
            "budget_typical": budget,
            "budget_filterable": budget_filterable,
        },
    }


class DestinationDocumentTests(unittest.TestCase):
    def test_document_keeps_semantic_labels_and_omits_statistics(self) -> None:
        row = profile(
            "DEST_DOC", "测试海岛", city="舟山", category="scenic",
            mood={"mood_unwind": 0.9}, vibe={"vibe_nature": 0.8}, activity={"act_sea": 0.9},
        )
        text = render_destination_document(row)
        self.assertIn("适合安静放空", text)
        self.assertIn("自然安静", text)
        self.assertIn("看海赶海", text)
        self.assertNotIn("类型：周末目的地", text)
        self.assertNotIn("UGC共识", text)
        metadata = build_metadata(row)
        self.assertEqual(3, metadata["positive_evidence_count"])
        self.assertEqual(0, metadata["limitation_evidence_count"])
        self.assertEqual(0.8, metadata["evidence_quality"])

    def test_document_omits_empty_placeholder_sections(self) -> None:
        row = profile(
            "DEST_EMPTY", "证据不足目的地", city="舟山", category="scenic",
            mood={}, vibe={}, activity={},
        )
        row.update(
            {
                "core_feeling": "体验感待更多 UGC 补充",
                "atmosphere": "氛围待核实",
                "suitable_scenes": ["待更多 UGC 补充"],
                "activities": ["待核实"],
                "limitations": [],
                "positive_evidence_count": 0,
                "limitation_evidence_count": 0,
            }
        )
        text = render_destination_document(row)
        for placeholder in (
            "体验感待更多 UGC 补充", "氛围待核实", "待更多 UGC 补充",
            "待核实", "暂无高置信限制信息", "0 条正向体验证据",
        ):
            self.assertNotIn(placeholder, text)
        for heading in ("核心感觉：", "氛围特征：", "适合场景：", "主要活动：", "不适合与限制："):
            self.assertNotIn(heading, text)


class QueryPlanTests(unittest.TestCase):
    def test_rule_parser_preserves_feeling_and_hard_activity(self) -> None:
        plan = build_rule_query_plan("上海出发，周末两天，人均1000内，想一个人安静看海，不要网红")
        self.assertEqual("上海", plan["hard_constraints"]["origin"])
        self.assertEqual(2, plan["hard_constraints"]["days_max"])
        self.assertEqual(1000, plan["hard_constraints"]["budget_max"])
        self.assertIn("act_sea", plan["hard_constraints"]["must_have_activities"])
        self.assertIn("mood_unwind", [item["id"] for item in plan["soft_preferences"]["mood"]])
        self.assertIn("网红", plan["exclusions"])

    def test_low_confidence_tag_is_removed(self) -> None:
        plan = normalize_query_plan(
            {
                "scope": "in_domain",
                "task_type": "destination_discovery",
                "hard_constraints": {},
                "exclusions": [],
                "semantic_query": "想放松",
                "soft_preferences": {
                    "mood": [{"id": "mood_heal", "confidence": 0.54}],
                    "vibe": [],
                    "activity": [],
                },
                "evidence_aspects": ["mood_fit"],
            }
        )
        self.assertEqual([], plan["soft_preferences"]["mood"])


class RankingTests(unittest.TestCase):
    def test_mood_weight_changes_order_for_same_activity(self) -> None:
        plan = normalize_query_plan(
            {
                "scope": "in_domain",
                "task_type": "destination_discovery",
                "hard_constraints": {
                    "origin": None,
                    "days_max": 2,
                    "budget_max": None,
                    "travel_time_max": None,
                    "transport_modes": [],
                    "must_have_activities": ["act_sea"],
                },
                "exclusions": [],
                "semantic_query": "一个人安静看海",
                "soft_preferences": {
                    "mood": [{"id": "mood_unwind", "confidence": 0.95}],
                    "vibe": [],
                    "activity": [],
                },
                "evidence_aspects": ["mood_fit", "scenery"],
            }
        )
        quiet = profile(
            "DEST_Q", "安静海岛", city="舟山", category="island",
            mood={"mood_unwind": 0.95}, vibe={"vibe_nature": 0.8}, activity={"act_sea": 0.9},
        )
        lively = profile(
            "DEST_L", "热闹海岛", city="宁波", category="island",
            mood={"mood_social": 0.95, "mood_unwind": 0.1}, vibe={"vibe_nature": 0.8}, activity={"act_sea": 0.9},
        )
        result = filter_and_rank([lively, quiet], plan, final_limit=2)
        self.assertEqual("DEST_Q", result["selected"][0]["destination_id"])
        self.assertGreater(
            score_candidate(quiet, plan)["score_components"]["mood"],
            score_candidate(lively, plan)["score_components"]["mood"],
        )

    def test_low_confidence_budget_is_not_used_as_hard_fact(self) -> None:
        plan = build_rule_query_plan("预算500元以内，想安静看海")
        unknown = profile(
            "DEST_U", "预算待核海岛", city="舟山", category="island",
            mood={"mood_unwind": 0.8}, vibe={"vibe_nature": 0.8}, activity={"act_sea": 0.9},
            budget=1200, budget_filterable=False,
        )
        result = filter_and_rank([unknown], plan, final_limit=1, allow_unknown_hard_facts=True)
        self.assertEqual(1, len(result["selected"]))
        self.assertIn("budget_unknown", result["selected"][0]["assumptions"])

    def test_mmr_prefers_category_diversity(self) -> None:
        plan = build_rule_query_plan("想放松一下")
        rows = [
            profile("D1", "海岛一", city="舟山", category="island", mood={"mood_heal": 0.9}, vibe={}, activity={}, semantic=0.95),
            profile("D2", "海岛二", city="舟山", category="island", mood={"mood_heal": 0.9}, vibe={}, activity={}, semantic=0.94),
            profile("D3", "古镇", city="湖州", category="town", mood={"mood_heal": 0.8}, vibe={}, activity={}, semantic=0.90),
        ]
        result = filter_and_rank(rows, plan, top_n=3, final_limit=2)
        self.assertEqual({"D1", "D3"}, {row["destination_id"] for row in result["selected"]})


class PolarityTests(unittest.TestCase):
    def test_negated_negative_phrases_are_positive(self) -> None:
        self.assertEqual("positive", infer_polarity("海超美，没有很商业化，个人觉得海更蓝"))
        self.assertEqual("positive", infer_polarity("不用请假、不用人挤人"))
        self.assertEqual("positive", infer_polarity("晚上不怎么需要排队"))
        self.assertEqual("positive", infer_polarity("人少、原生态，避开商业化人潮"))

    def test_real_negative_and_substring_false_positive(self) -> None:
        self.assertEqual("negative", infer_polarity("海鲜面将就吃，不推荐"))
        self.assertEqual("positive", infer_polarity("落差190米，景色很震撼"))


class MigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        schema_dir = ROOT / "schemas"
        cls.schemas = {
            "entity": json.loads((schema_dir / "entity_schema.json").read_text(encoding="utf-8")),
            "claim": json.loads((schema_dir / "ugc_claim_schema.json").read_text(encoding="utf-8")),
            "profile": json.loads((schema_dir / "destination_profile_schema.json").read_text(encoding="utf-8")),
        }

    def test_destination_and_child_claim_are_linked(self) -> None:
        poi_rows = [
            {
                "poi_id": "POI_DEST", "name": "测试岛", "city": "舟山", "province": "浙江",
                "category": "scenic", "duration_days": 2, "duration_source": "证据",
                "mood": ["mood_unwind"], "vibe": ["vibe_nature"], "activity": ["act_sea"],
                "independent_sources": 1,
            },
            {
                "poi_id": "POI_EXP", "name": "测试岛观景台", "city": "舟山", "province": "浙江",
                "category": "scenic", "duration_days": 1, "mood": [], "vibe": ["vibe_nature"],
                "activity": ["act_sea"], "independent_sources": 1,
            },
        ]
        mentions = [
            {
                "note_id": "N1", "evidence_id": "EV1", "poi_id": "POI_DEST", "author_hash": "A1",
                "mood": ["mood_unwind"], "vibe": ["vibe_nature"], "activity": ["act_sea"],
            },
            {
                "note_id": "N1", "evidence_id": "EV2", "poi_id": "POI_EXP", "author_hash": "A1",
                "mood": ["mood_unwind"], "vibe": ["vibe_nature"], "activity": ["act_sea"],
            },
        ]
        evidence = [
            {
                "evidence_id": "EV1", "poi_id": "POI_DEST", "key_quote": "人少安静适合一个人发呆",
                "author_hash": "A1", "publish_date": "2026-06-01", "collected_date": "2026-07-01",
                "is_suspected_ad": False, "source_url": "https://example.com/1",
            },
            {
                "evidence_id": "EV2", "poi_id": "POI_EXP", "key_quote": "观景台看海很好看",
                "author_hash": "A1", "publish_date": "2026-06-01", "collected_date": "2026-07-01",
                "is_suspected_ad": False, "source_url": "https://example.com/1",
            },
        ]
        result = build_v2_dataset(
            poi_rows=poi_rows,
            mention_rows=mentions,
            evidence_rows=evidence,
            alias_map_path=ROOT / "data" / "demo" / "alias_map.csv",
            taxonomy_path=ROOT / "schemas" / "intent_taxonomy.json",
            schemas=self.schemas,
            today=date(2026, 7, 1),
        )
        destinations = [row for row in result["entities"] if row["entity_type"] == "destination"]
        experiences = [row for row in result["entities"] if row["entity_type"] == "experience"]
        self.assertEqual(1, len(destinations))
        self.assertEqual(destinations[0]["entity_id"], experiences[0]["parent_id"])
        child_claim = next(row for row in result["claims"] if row["entity_id"] == experiences[0]["entity_id"])
        self.assertEqual(destinations[0]["entity_id"], child_claim["destination_id"])

    def test_service_claim_does_not_pollute_destination_profile(self) -> None:
        poi_rows = [
            {
                "poi_id": "POI_DEST", "name": "测试岛", "city": "舟山", "province": "浙江",
                "category": "scenic", "duration_days": 2, "duration_source": "证据",
                "mood": ["mood_unwind"], "vibe": ["vibe_nature"], "activity": ["act_sea"],
                "independent_sources": 1,
            },
            {
                "poi_id": "POI_FOOD", "name": "测试海鲜面", "city": "舟山", "province": "浙江",
                "category": "food", "duration_days": 1, "mood": [], "vibe": [],
                "activity": ["act_food"], "independent_sources": 1,
            },
        ]
        mentions = [
            {
                "note_id": "N1", "evidence_id": "EV_DEST", "poi_id": "POI_DEST", "author_hash": "A1",
                "mood": ["mood_unwind"], "vibe": ["vibe_nature"], "activity": ["act_sea"],
            },
            {
                "note_id": "N1", "evidence_id": "EV_FOOD", "poi_id": "POI_FOOD", "author_hash": "A1",
                "mood": [], "vibe": [], "activity": ["act_food"],
            },
            {
                "note_id": "N2", "evidence_id": "EV_ROUTE", "poi_id": "POI_DEST", "author_hash": "A2",
                "mood": ["mood_unwind"], "vibe": ["vibe_nature"], "activity": ["act_sea"],
            },
            {
                "note_id": "N3", "evidence_id": "EV_SCHEDULE", "poi_id": "POI_DEST", "author_hash": "A3",
                "mood": ["mood_unwind"], "vibe": ["vibe_nature"], "activity": ["act_sea"],
            },
        ]
        evidence = [
            {
                "evidence_id": "EV_DEST", "poi_id": "POI_DEST",
                "key_quote": "这里人少安静，适合一个人看海发呆",
                "author_hash": "A1", "publish_date": "2026-06-01", "collected_date": "2026-07-01",
                "is_suspected_ad": False, "source_url": "https://example.com/destination",
            },
            {
                "evidence_id": "EV_FOOD", "poi_id": "POI_FOOD",
                "key_quote": "海鲜面味道普通，不推荐",
                "author_hash": "A1", "publish_date": "2026-06-01", "collected_date": "2026-07-01",
                "is_suspected_ad": False, "source_url": "https://example.com/food",
            },
            {
                "evidence_id": "EV_ROUTE", "poi_id": "POI_DEST",
                "key_quote": "上海→测试岛→返程",
                "author_hash": "A2", "publish_date": "2026-06-01", "collected_date": "2026-07-01",
                "is_suspected_ad": False, "source_url": "https://example.com/route",
            },
            {
                "evidence_id": "EV_SCHEDULE", "poi_id": "POI_DEST",
                "key_quote": "15:00 环岛骑行 or 看日落",
                "author_hash": "A3", "publish_date": "2026-06-01", "collected_date": "2026-07-01",
                "is_suspected_ad": False, "source_url": "https://example.com/schedule",
            },
        ]
        result = build_v2_dataset(
            poi_rows=poi_rows,
            mention_rows=mentions,
            evidence_rows=evidence,
            alias_map_path=ROOT / "data" / "demo" / "alias_map.csv",
            taxonomy_path=ROOT / "schemas" / "intent_taxonomy.json",
            schemas=self.schemas,
            today=date(2026, 7, 1),
        )
        destination_profile = result["profiles"][0]
        rendered = "；".join(
            destination_profile["suitable_scenes"] + destination_profile["limitations"]
        )
        self.assertNotIn("海鲜面", rendered)
        self.assertNotIn("上海→测试岛", rendered)
        self.assertNotIn("15:00", rendered)
        service_claim = next(row for row in result["claims"] if row["entity_id"].startswith("SVC_"))
        self.assertEqual("negative", service_claim["polarity"])


class _StaticReranker:
    def rerank(self, query: str, rows: list[dict]) -> tuple[list[dict], str]:
        return sorted(
            (dict(row) for row in rows),
            key=lambda row: (
                -float(row.get("query_match_score") or 0),
                -float(row.get("source_quality") or 0),
            ),
        ), "test_reranker"


class _LowScoreBgeReranker:
    def rerank(self, query: str, rows: list[dict]) -> tuple[list[dict], str]:
        ranked = []
        for row in rows:
            enriched = dict(row)
            enriched["rerank_score"] = 0.01
            ranked.append(enriched)
        return ranked, "xinference_bge"


class _MemoryRepository:
    def __init__(
        self,
        profiles: list[dict],
        claims: list[dict],
        *,
        apply_filters: bool = True,
        travel_rows: list[dict] | None = None,
    ):
        self.profiles = profiles
        self.claims = claims
        self.apply_filters = apply_filters
        self.travel_rows = travel_rows or []
        self.claim_calls: list[dict] = []

    def get_profiles(self, destination_ids: list[str]) -> list[dict]:
        wanted = set(destination_ids)
        return [row for row in self.profiles if row["destination_id"] in wanted]

    def get_claims(
        self,
        destination_ids: list[str],
        aspects: list[str],
        *,
        per_destination: int = 4,
        polarities: tuple[str, ...] = (),
        entity_types: tuple[str, ...] = (),
        tag_ids: tuple[str, ...] = (),
    ) -> dict[str, list[dict]]:
        self.claim_calls.append(
            {
                "aspects": tuple(aspects),
                "per_destination": per_destination,
                "polarities": polarities,
                "entity_types": entity_types,
                "tag_ids": tag_ids,
            }
        )
        result = {destination_id: [] for destination_id in destination_ids}
        for row in self.claims:
            destination_id = row.get("destination_id")
            if destination_id not in result:
                continue
            if self.apply_filters:
                claim_tags = (
                    set(row.get("mood") or [])
                    | set(row.get("vibe") or [])
                    | set(row.get("activity") or [])
                )
                if polarities and row.get("polarity") not in polarities:
                    continue
                if entity_types and row.get("entity_type") not in entity_types:
                    continue
                if (aspects or tag_ids) and not (
                    row.get("aspect") in aspects or claim_tags & set(tag_ids)
                ):
                    continue
            if len(result[destination_id]) < per_destination:
                result[destination_id].append(dict(row))
        return result

    def get_travel_rows(self, destination_ids: list[str]) -> list[dict]:
        wanted = set(destination_ids)
        return [row for row in self.travel_rows if row.get("destination_id") in wanted]


def _claim(
    claim_id: str,
    *,
    polarity: str,
    aspect: str,
    text: str,
    author: str,
    entity_type: str = "destination",
    entity_id: str = "DEST_Q",
    mood: list[str] | None = None,
    vibe: list[str] | None = None,
    activity: list[str] | None = None,
    quality: float = 0.8,
) -> dict:
    return {
        "claim_id": claim_id,
        "evidence_id": f"EV_{claim_id}",
        "entity_id": entity_id,
        "entity_type": entity_type,
        "destination_id": "DEST_Q",
        "note_id": f"NOTE_{claim_id}",
        "aspect": aspect,
        "polarity": polarity,
        "claim": text,
        "key_quote": text,
        "mood": mood or [],
        "vibe": vibe or [],
        "activity": activity or [],
        "conditions": {},
        "author_hash": author,
        "publish_date": "2026-07-01",
        "source_quality": quality,
        "is_suspected_ad": False,
    }


class RuntimeEvidenceTests(unittest.TestCase):
    def test_selected_candidate_includes_origin_filtered_travel_failure(self) -> None:
        candidate = profile(
            "DEST_Q", "测试海岛", city="舟山", category="island",
            mood={"mood_unwind": 0.9}, vibe={"vibe_nature": 0.8}, activity={"act_sea": 0.9},
        )
        supporting = _claim(
            "SUPPORT", polarity="positive", aspect="scenery",
            text="适合一个人安静看海", author="A1",
            mood=["mood_unwind"], activity=["act_sea"],
        )
        repository = _MemoryRepository(
            [candidate],
            [supporting],
            travel_rows=[
                {
                    "destination_id": "DEST_Q",
                    "origin_city": "上海",
                    "transport_mode": "公共交通",
                    "travel_minutes": None,
                    "failure_reason": "origin_geocode_failed",
                    "confidence": "低",
                },
                {
                    "destination_id": "DEST_Q",
                    "origin_city": "杭州",
                    "transport_mode": "公共交通",
                    "travel_minutes": 180,
                    "confidence": "中",
                },
            ],
        )
        plan = build_rule_query_plan("上海出发，一个人安静看海，公共交通")
        result = rank_retrieval_items(
            raw_query="上海出发，一个人安静看海，公共交通",
            query_plan_payload=plan,
            retrieval_items=[{"metadata": {"destination_id": "DEST_Q", "score": 0.9}}],
            repository=repository,
            final_limit=1,
            evidence_reranker=_StaticReranker(),
        )
        self.assertEqual(1, len(result["selected"]))
        travel = result["selected"][0]["travel_options"]
        self.assertEqual(1, len(travel))
        self.assertEqual("上海", travel[0]["origin_city"])
        self.assertIsNone(travel[0]["travel_minutes"])
        self.assertEqual("origin_geocode_failed", travel[0]["failure_reason"])

    def test_quiet_sea_uses_query_matched_independent_support_and_separate_caveat(self) -> None:
        plan = build_rule_query_plan("一个人安静看海")
        claims = [
            _claim(
                "QUIET_A", polarity="positive", aspect="crowd",
                text="人少安静，适合一个人看海发呆", author="A1",
                mood=["mood_unwind"], activity=["act_sea"], quality=0.95,
            ),
            _claim(
                "QUIET_DUP", polarity="positive", aspect="scenery",
                text="海边安静适合独处", author="A1",
                mood=["mood_unwind"], activity=["act_sea"], quality=0.94,
            ),
            _claim(
                "SEA_B", polarity="positive", aspect="scenery",
                text="观景台视野开阔，可以安静看海", author="A2",
                mood=["mood_unwind"], activity=["act_sea"], quality=0.9,
            ),
            _claim(
                "FOOD", polarity="positive", aspect="food",
                text="招牌菜份量很大", author="A3", activity=["act_food"], quality=0.99,
            ),
            _claim(
                "TASTE", polarity="negative", aspect="food",
                text="餐厅味道普通", author="S1", entity_type="service", entity_id="SVC_FOOD",
                activity=["act_food"], quality=0.99,
            ),
            _claim(
                "CROWD", polarity="negative", aspect="crowd",
                text="节假日核心观景台会拥挤", author="A4", quality=0.85,
            ),
        ]
        repository = _MemoryRepository([], claims)
        result = attach_evidence(
            [{"destination_id": "DEST_Q", "name": "测试海岛"}],
            plan,
            repository,
            raw_query="一个人安静看海",
            reranker=_StaticReranker(),
        )[0]

        supporting = result["evidence"]["supporting"]
        self.assertEqual(2, len(supporting))
        self.assertEqual(2, len({row["author_hash"] for row in supporting}))
        self.assertTrue(all("看海" in row["claim"] or "海边" in row["claim"] for row in supporting))
        self.assertEqual(["CROWD"], [row["claim_id"] for row in result["evidence"]["caveats"]])
        self.assertNotIn("TASTE", result["evidence"]["evidence_ids"])
        self.assertEqual(("positive",), repository.claim_calls[0]["polarities"])
        self.assertEqual(("negative", "mixed"), repository.claim_calls[1]["polarities"])
        self.assertEqual(("destination", "experience"), repository.claim_calls[0]["entity_types"])

    def test_low_bge_relevance_is_a_transparent_gap_despite_noisy_tags(self) -> None:
        plan = build_rule_query_plan("一个人安静看海")
        noisy = _claim(
            "NOISY_TAGS", polarity="positive", aspect="activity",
            text="带家人度过两天慢生活", author="A1",
            mood=["mood_unwind"], activity=["act_sea"],
        )
        repository = _MemoryRepository([], [noisy])
        result = attach_evidence(
            [{"destination_id": "DEST_Q", "name": "标签噪声目的地"}],
            plan,
            repository,
            raw_query="一个人安静看海",
            reranker=_LowScoreBgeReranker(),
        )[0]
        self.assertTrue(result["evidence_gap"])
        self.assertEqual("claim_rerank_below_threshold", result["evidence_gap_reason"])
        self.assertEqual([], result["evidence"]["supporting"])

    def test_unmatched_support_is_transparently_rejected(self) -> None:
        candidate = profile(
            "DEST_Q", "证据不匹配海岛", city="舟山", category="island",
            mood={"mood_unwind": 0.9}, vibe={"vibe_nature": 0.8}, activity={"act_sea": 0.9},
        )
        irrelevant = _claim(
            "IRRELEVANT", polarity="positive", aspect="food",
            text="餐厅菜量很足", author="A1", activity=["act_food"],
        )
        repository = _MemoryRepository([candidate], [irrelevant], apply_filters=False)
        result = rank_retrieval_items(
            raw_query="一个人安静看海",
            query_plan_payload=build_rule_query_plan("一个人安静看海"),
            retrieval_items=[{"metadata": {"destination_id": "DEST_Q", "score": 0.9}}],
            repository=repository,
            final_limit=1,
            evidence_reranker=_StaticReranker(),
        )
        self.assertEqual([], result["selected"])
        self.assertEqual(["DEST_Q"], result["evidence_rejected"])
        self.assertEqual(
            "no_query_matched_supporting_claim",
            result["evidence_gaps"][0]["reason"],
        )


class RepositoryClaimIsolationTests(unittest.TestCase):
    def test_jsonl_repository_filters_service_and_deduplicates_authors(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            data_dir = Path(temporary)
            entities = [
                {"entity_id": "DEST_Q", "entity_type": "destination"},
                {"entity_id": "EXP_SEA", "entity_type": "experience"},
                {"entity_id": "SVC_FOOD", "entity_type": "service"},
            ]
            claims = [
                _claim(
                    "D1", polarity="positive", aspect="crowd", text="人少安静", author="A1",
                    mood=["mood_unwind"], entity_id="DEST_Q", quality=0.95,
                ),
                _claim(
                    "D2", polarity="positive", aspect="scenery", text="安静看海", author="A1",
                    activity=["act_sea"], entity_type="experience", entity_id="EXP_SEA", quality=0.94,
                ),
                _claim(
                    "D3", polarity="positive", aspect="scenery", text="海景开阔", author="A2",
                    activity=["act_sea"], entity_type="experience", entity_id="EXP_SEA", quality=0.9,
                ),
                _claim(
                    "S1", polarity="negative", aspect="food", text="餐厅味道普通", author="S1",
                    entity_type="service", entity_id="SVC_FOOD", quality=0.99,
                ),
            ]
            for name, rows in (
                ("entities.jsonl", entities),
                ("ugc_claims.jsonl", claims),
                ("destination_profiles.jsonl", []),
                ("travel_matrix.jsonl", []),
            ):
                (data_dir / name).write_text(
                    "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
                    encoding="utf-8",
                )

            repository = JsonlRecommendationRepository(data_dir)
            supporting = repository.get_claims(
                ["DEST_Q"],
                ["crowd", "scenery"],
                per_destination=4,
                polarities=("positive",),
                entity_types=("destination", "experience"),
                tag_ids=("mood_unwind", "act_sea"),
            )["DEST_Q"]
            caveats = repository.get_claims(
                ["DEST_Q"],
                [],
                per_destination=4,
                polarities=("negative", "mixed"),
                entity_types=("destination", "experience"),
            )["DEST_Q"]

        self.assertEqual(["D1", "D3"], [row["claim_id"] for row in supporting])
        self.assertEqual([], caveats)


class ClaimRerankerTests(unittest.TestCase):
    def test_timeout_uses_safe_deterministic_fallback(self) -> None:
        reranker = XinferenceClaimReranker(
            base_url="http://127.0.0.1:9997/v1",
            model="bge-reranker",
            timeout_seconds=0.1,
        )
        rows = [
            _claim("LOW", polarity="positive", aspect="scenery", text="海景", author="A1", quality=0.5),
            _claim("HIGH", polarity="positive", aspect="scenery", text="安静看海", author="A2", quality=0.9),
        ]
        rows[0]["query_match_score"] = 1
        rows[1]["query_match_score"] = 3
        with patch(
            "inspitrip.recommendation.claim_reranker.requests.post",
            side_effect=__import__("requests").Timeout(),
        ):
            ranked, status = reranker.rerank("安静看海", rows)
        self.assertEqual("fallback_error", status)
        self.assertEqual(["HIGH", "LOW"], [row["claim_id"] for row in ranked])


if __name__ == "__main__":
    unittest.main()
