from __future__ import annotations

import sys
import unittest
import json
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from inspitrip.recommendation.ugc_claim_enhancement import (
    ENHANCED_CLAIM_PREFIX,
    generate_enhanced_claims,
    merge_claims,
    load_raw_notes,
)
from inspitrip.recommendation.v2_pipeline import build_claims, infer_conditions, infer_polarity, load_jsonl


class RawClaimEnhancementTests(unittest.TestCase):
    def setUp(self) -> None:
        self.entities = [
            {
                "entity_id": "DEST_A",
                "legacy_poi_id": "POI_A",
                "legacy_poi_ids": ["POI_A"],
                "entity_type": "destination",
                "parent_id": None,
                "name": "测试岛",
                "aliases": [],
            },
            {
                "entity_id": "EXP_A",
                "legacy_poi_id": "POI_EXP",
                "legacy_poi_ids": ["POI_EXP"],
                "entity_type": "experience",
                "parent_id": "DEST_A",
                "name": "测试观景台",
                "aliases": [],
            },
            {
                "entity_id": "DEST_B",
                "legacy_poi_id": "POI_B",
                "legacy_poi_ids": ["POI_B"],
                "entity_type": "destination",
                "parent_id": None,
                "name": "另一岛",
                "aliases": [],
            },
        ]

    def _mention(self, note_id: str, evidence_id: str, poi_id: str, name: str) -> dict:
        return {
            "note_id": note_id,
            "evidence_id": evidence_id,
            "poi_id": poi_id,
            "canonical_name": name,
            "raw_place_name": name,
            "author_hash": "AUTHOR_1",
            "mood": ["mood_unwind"],
            "vibe": ["vibe_nature"],
            "activity": ["act_hike"],
            "is_suspected_ad": False,
        }

    def _evidence(self, evidence_id: str, poi_id: str, url: str) -> dict:
        return {
            "evidence_id": evidence_id,
            "poi_id": poi_id,
            "source_url": url,
            "author_hash": "AUTHOR_1",
            "publish_date": "2026-06-01",
            "collected_date": "2026-07-01",
            "likes": 10,
            "collects": 5,
            "comments": 2,
            "is_suspected_ad": False,
        }

    def test_grounded_fragments_keep_source_author_and_entity_isolation(self) -> None:
        url = "https://example.com/item/NOTE_A"
        content = (
            "测试岛节假日游客很多，公交也不方便。"
            "测试观景台雨天很滑，建议晴天再去。"
            "商务合作私信我领取优惠券。"
        )
        raw_notes = [
            {
                "source_url": url,
                "note_title": "测试岛实测",
                "content": content,
                "is_suspected_ad": "false",
            }
        ]
        mentions = [
            self._mention("NOTE_A", "EV_A", "POI_A", "测试岛"),
            self._mention("NOTE_A", "EV_EXP", "POI_EXP", "测试观景台"),
        ]
        evidence = [
            self._evidence("EV_A", "POI_A", url),
            self._evidence("EV_EXP", "POI_EXP", url),
        ]

        claims, report = generate_enhanced_claims(
            raw_notes=raw_notes,
            mentions=mentions,
            evidence_rows=evidence,
            entities=self.entities,
            existing_claims=[],
            today=date(2026, 7, 17),
        )

        destination_claims = [row for row in claims if row["entity_id"] == "DEST_A"]
        experience_claims = [row for row in claims if row["entity_id"] == "EXP_A"]
        self.assertEqual({"crowd", "transport"}, {row["aspect"] for row in destination_claims})
        self.assertTrue(experience_claims)
        self.assertTrue(all(row["destination_id"] == "DEST_A" for row in experience_claims))
        self.assertTrue(all(row["author_hash"] == "AUTHOR_1" for row in claims))
        self.assertTrue(all(row["source_url"] == url for row in claims))
        self.assertTrue(all(row["key_quote"] in content for row in claims))
        self.assertTrue(all(not row["is_suspected_ad"] for row in claims))
        self.assertGreaterEqual(report["scan_counts"].get("ad_fragment_skipped", 0), 1)

    def test_ambiguous_note_context_is_not_copied_to_multiple_destinations(self) -> None:
        url = "https://example.com/item/NOTE_MULTI"
        raw_notes = [
            {
                "source_url": url,
                "note_title": "双岛行程",
                "content": "节假日公交不方便。测试岛节假日公交不方便。",
                "is_suspected_ad": "false",
            }
        ]
        mentions = [
            self._mention("NOTE_MULTI", "EV_A", "POI_A", "测试岛"),
            self._mention("NOTE_MULTI", "EV_B", "POI_B", "另一岛"),
        ]
        evidence = [
            self._evidence("EV_A", "POI_A", url),
            self._evidence("EV_B", "POI_B", url),
        ]

        claims, report = generate_enhanced_claims(
            raw_notes=raw_notes,
            mentions=mentions,
            evidence_rows=evidence,
            entities=self.entities,
            existing_claims=[],
            today=date(2026, 7, 17),
        )

        self.assertEqual({"DEST_A"}, {row["entity_id"] for row in claims})
        self.assertGreaterEqual(report["scan_counts"].get("ambiguous_note_context", 0), 1)

    def test_unmapped_named_place_is_not_assigned_to_note_destination(self) -> None:
        url = "https://example.com/item/NOTE_UNKNOWN_PLACE"
        raw_notes = [
            {
                "source_url": url,
                "note_title": "测试岛行程",
                "content": "龙泉沙滩门票20元。岛上公交不方便。",
                "is_suspected_ad": "false",
            }
        ]
        mentions = [self._mention("NOTE_UNKNOWN_PLACE", "EV_A", "POI_A", "测试岛")]
        evidence = [self._evidence("EV_A", "POI_A", url)]
        claims, report = generate_enhanced_claims(
            raw_notes=raw_notes,
            mentions=mentions,
            evidence_rows=evidence,
            entities=self.entities,
            existing_claims=[],
            today=date(2026, 7, 17),
        )
        self.assertEqual(["岛上公交不方便"], [row["claim"] for row in claims])
        self.assertGreaterEqual(report["scan_counts"].get("unmapped_explicit_place", 0), 1)

    def test_suspected_ads_are_excluded_and_merge_is_idempotent(self) -> None:
        url = "https://example.com/item/NOTE_AD"
        raw_notes = [
            {
                "source_url": url,
                "note_title": "测试岛交通",
                "content": "测试岛公交不方便",
                "is_suspected_ad": "true",
            }
        ]
        mentions = [self._mention("NOTE_AD", "EV_AD", "POI_A", "测试岛")]
        evidence = [self._evidence("EV_AD", "POI_A", url)]
        generated, _ = generate_enhanced_claims(
            raw_notes=raw_notes,
            mentions=mentions,
            evidence_rows=evidence,
            entities=self.entities,
            existing_claims=[],
            today=date(2026, 7, 17),
        )
        self.assertEqual([], generated)

        enhanced = {
            "claim_id": f"{ENHANCED_CLAIM_PREFIX}ONE",
            "is_suspected_ad": False,
            "claim": "公交不方便",
        }
        ad_claim = {"claim_id": "CLM_AD", "is_suspected_ad": True}
        first, first_report = merge_claims([ad_claim], [enhanced])
        second, second_report = merge_claims(first, [enhanced])
        self.assertEqual(first, second)
        self.assertEqual(1, first_report["removed_ads"])
        self.assertEqual(0, second_report["added"])
        self.assertEqual(1, second_report["unchanged"])


class ConditionAndPolarityTests(unittest.TestCase):
    def test_conditions_cover_time_party_peak_and_transport(self) -> None:
        conditions = infer_conditions("周末带娃自驾，建议提前预约，傍晚错峰去")
        self.assertTrue(conditions["weekend"])
        self.assertEqual("family_with_children", conditions["companion"])
        self.assertEqual("driving", conditions["transport_mode"])
        self.assertEqual("evening", conditions["time_of_day"])
        self.assertEqual("off_peak", conditions["travel_period"])
        self.assertTrue(conditions["advance_booking"])

    def test_cost_and_transport_negation_do_not_flip_positive(self) -> None:
        self.assertEqual("positive", infer_polarity("船票不算贵，交通也不折腾"))
        self.assertEqual("negative", infer_polarity("公交不方便而且游客很多"))
        self.assertEqual("neutral", infer_polarity("人多可以包车，六趟300元"))
        self.assertEqual("positive", infer_polarity("下午沙滩人不多，很舒服"))
        self.assertEqual("negative", infer_polarity("岛上物价偏高，餐厅没有价格表"))

    def test_base_claim_builder_drops_suspected_ads(self) -> None:
        claims = build_claims(
            [
                {
                    "evidence_id": "EV_AD",
                    "poi_id": "POI_AD",
                    "key_quote": "公交不方便",
                    "is_suspected_ad": True,
                }
            ],
            [{"evidence_id": "EV_AD", "note_id": "N_AD"}],
            [{"entity_id": "DEST_AD", "entity_type": "destination", "parent_id": None}],
            {"POI_AD": "DEST_AD"},
            today=date(2026, 7, 17),
        )
        self.assertEqual([], claims)


class RepositoryUgcEnhancementTests(unittest.TestCase):
    def test_snapshot_claims_are_grounded_non_ad_and_entity_isolated(self) -> None:
        data_dir = ROOT / "data" / "demo"
        claims = load_jsonl(data_dir / "ugc_claims.jsonl")
        entities = load_jsonl(data_dir / "entities.jsonl")
        entity_by_id = {row["entity_id"]: row for row in entities}
        self.assertEqual(len(claims), len({row["claim_id"] for row in claims}))
        self.assertEqual(0, sum(bool(row.get("is_suspected_ad")) for row in claims))
        self.assertTrue(claims)
        for claim in claims:
            self.assertIn(claim["entity_id"], entity_by_id)
            self.assertEqual(claim["entity_id"], claim["destination_id"])
            self.assertTrue(claim["source_url"].startswith("demo://synthetic/"))
            self.assertTrue(claim["author_hash"].startswith("synthetic_author_"))



if __name__ == "__main__":
    unittest.main()
