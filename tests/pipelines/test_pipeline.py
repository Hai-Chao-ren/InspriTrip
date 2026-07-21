from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from inspitrip.pipelines.ugc.db import load_notes
from inspitrip.pipelines.ugc.client import OpenAIExtractor, _coerce_responses_response
from inspitrip.pipelines.ugc.prompt import bind_taxonomy_enums, build_system_prompt
from inspitrip.pipelines.ugc.writer import (
    aggregate_pois,
    build_mention_and_evidence_records,
    load_schema,
    normalize_budget,
)


class PipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rag_root = ROOT
        cls.extraction_schema = bind_taxonomy_enums(
            load_schema(cls.rag_root / "schemas" / "note_extraction_schema.json"),
            cls.rag_root / "schemas" / "intent_taxonomy.json",
        )
        cls.evidence_schema = load_schema(
            cls.rag_root / "schemas" / "ugc_evidence_schema.json"
        )
        cls.poi_schema = load_schema(cls.rag_root / "schemas" / "poi_schema.json")

    def test_database_loader_handles_synthetic_sample(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "demo.db"
            connection = sqlite3.connect(path)
            connection.execute(
                'CREATE TABLE explore_data ("作品ID" TEXT, "作品标题" TEXT, "作品描述" TEXT, "作品链接" TEXT)'
            )
            connection.execute(
                'INSERT INTO explore_data VALUES (?,?,?,?)',
                ("DEMO_NOTE", "合成标题", "合成内容", "demo://synthetic/note"),
            )
            connection.commit()
            connection.close()
            notes = load_notes(path, limit=1)
        self.assertEqual(1, len(notes))
        self.assertEqual("DEMO_NOTE", notes[0]["note_id"])
        self.assertEqual("合成内容", notes[0]["content"])

    def test_prompt_and_schema_share_taxonomy(self) -> None:
        prompt = build_system_prompt(
            self.rag_root / "schemas" / "intent_taxonomy.json"
        )
        mood_enum = self.extraction_schema["properties"]["mentions"]["items"][
            "properties"
        ]["mood"]["items"]["enum"]
        self.assertIn("mood_unwind", prompt)
        self.assertIn("mood_unwind", mood_enum)
        self.assertNotIn("invented_tag", mood_enum)

    def test_budget_normalization(self) -> None:
        self.assertEqual(
            (800, "direct", "高"),
            normalize_budget(
                [
                    {
                        "raw_quote": "人均800",
                        "amount": 800,
                        "basis": "per_person_trip",
                        "group_size": None,
                    }
                ],
                2,
            ),
        )
        self.assertEqual(
            (600, "direct", "中"),
            normalize_budget(
                [
                    {
                        "raw_quote": "两个人一共1200",
                        "amount": 1200,
                        "basis": "per_group_trip",
                        "group_size": 2,
                    }
                ],
                2,
            ),
        )
        self.assertEqual(
            (None, "none", "低"),
            normalize_budget(
                [
                    {
                        "raw_quote": "两岛人均800",
                        "amount": 800,
                        "basis": "per_person_trip",
                        "group_size": None,
                    }
                ],
                2,
                trip_level=True,
            ),
        )

    def test_compatible_responses_string_is_accepted(self) -> None:
        extraction = {
            "note_id": "N1",
            "is_suspected_ad": False,
            "ad_reason": "",
            "mentions": [],
        }
        text, response_id, model, usage = _coerce_responses_response(
            json.dumps(extraction, ensure_ascii=False), "test-model"
        )
        self.assertEqual(extraction, json.loads(text))
        self.assertEqual("", response_id)
        self.assertEqual("test-model", model)
        self.assertEqual({}, usage)

    def test_compatible_responses_envelope_string_is_accepted(self) -> None:
        extraction = {
            "note_id": "N2",
            "is_suspected_ad": False,
            "ad_reason": "",
            "mentions": [],
        }
        envelope = {
            "id": "resp_test",
            "model": "test-model",
            "usage": {"total_tokens": 9},
            "output": [
                {
                    "type": "message",
                    "content": [
                        {
                            "type": "output_text",
                            "text": json.dumps(extraction, ensure_ascii=False),
                        }
                    ],
                }
            ],
        }
        text, response_id, model, usage = _coerce_responses_response(
            json.dumps(envelope, ensure_ascii=False), "fallback-model"
        )
        self.assertEqual(extraction, json.loads(text))
        self.assertEqual("resp_test", response_id)
        self.assertEqual("test-model", model)
        self.assertEqual(9, usage["total_tokens"])

    def test_empty_gateway_response_backs_off_without_json_mode_burst(self) -> None:
        extraction = {
            "note_id": "N3",
            "is_suspected_ad": False,
            "ad_reason": "",
            "mentions": [],
        }
        extractor = OpenAIExtractor(
            api_key="test-key",
            model="test-model",
            schema=self.extraction_schema,
            output_mode="auto",
        )
        create = Mock(
            side_effect=["", json.dumps(extraction, ensure_ascii=False)]
        )
        extractor.client.responses.create = create
        with patch("inspitrip.pipelines.ugc.client.time.sleep") as sleep, patch(
            "inspitrip.pipelines.ugc.client.random.uniform", return_value=0.0
        ):
            result = extractor.extract(
                instructions="JSON",
                user_input="{}",
                retries=1,
                retry_base_delay=5,
            )
        self.assertEqual(extraction, result.data)
        self.assertEqual("structured", result.output_mode)
        self.assertEqual(2, create.call_count)
        sleep.assert_called_once_with(5)

    def test_evidence_and_final_poi_validate(self) -> None:
        note = {
            "note_id": "TEST_NOTE",
            "note_title": "枸杞岛两天一夜",
            "source_url": "https://www.xiaohongshu.com/explore/TEST_NOTE",
            "author_hash": "abc123",
            "likes": 10,
            "collects": 12,
            "comments": 2,
            "publish_date": "2026-06-01",
            "collected_date": "2026-07-10",
        }
        extraction = {
            "note_id": "TEST_NOTE",
            "is_suspected_ad": False,
            "ad_reason": "",
            "mentions": [
                {
                    "raw_place_name": "枸杞岛",
                    "canonical_name": "枸杞岛",
                    "city": "舟山",
                    "province": "浙江",
                    "place_status": "specific",
                    "mood": ["mood_unwind"],
                    "vibe": ["vibe_niche"],
                    "activity": ["act_sea"],
                    "key_quote": "人少，适合一个人发呆看海",
                    "trip_level": False,
                    "budget_signals": [
                        {
                            "raw_quote": "两天一夜人均800",
                            "amount": 800,
                            "basis": "per_person_trip",
                            "group_size": None,
                        }
                    ],
                    "duration_raw_quote": "两天一夜",
                    "duration_days_observed": 2,
                    "duration_confidence": "高",
                }
            ],
        }
        mentions, evidence = build_mention_and_evidence_records(
            note, extraction, aliases={}, evidence_schema=self.evidence_schema
        )
        self.assertEqual(1, len(evidence))
        poi_id = mentions[0]["poi_id"]
        final_rows, candidates, discarded = aggregate_pois(
            mentions,
            poi_schema=self.poi_schema,
            enrichment={
                poi_id: {
                    "reachable_from": ["上海"],
                    "travel_time_min": 180,
                    "travel_time_source": "地图API",
                    "transport": ["高铁", "轮渡"],
                }
            },
            today=date(2026, 7, 10),
        )
        self.assertEqual([], discarded)
        self.assertEqual([], candidates[0]["missing_required_fields"])
        self.assertEqual(1, len(final_rows))
        self.assertEqual(800, final_rows[0]["budget_per_capita"])


if __name__ == "__main__":
    unittest.main()
