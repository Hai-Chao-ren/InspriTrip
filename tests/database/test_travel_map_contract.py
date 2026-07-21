from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from inspitrip.pipelines.database import load_v2
from scripts.verify_v2_database import (
    REQUIRED_MAP_COLUMNS,
    REQUIRED_TRAVEL_COLUMNS,
    interval_invalid_sql,
    load_jsonl as verifier_load_jsonl,
    report_is_ok,
)


class RecordingCursor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple | None]] = []
        self.rowcount = 0

    def execute(self, query: str, params: tuple | None = None) -> None:
        self.calls.append((query, params))

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None


class RecordingConnection:
    def __init__(self) -> None:
        self.recording_cursor = RecordingCursor()
        self.committed = False

    def cursor(self) -> RecordingCursor:
        return self.recording_cursor

    def commit(self) -> None:
        self.committed = True

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def entity() -> dict:
    return {
        "entity_id": "DEST_TEST",
        "legacy_poi_id": "POI_TEST",
        "entity_type": "destination",
        "parent_id": None,
        "name": "测试岛",
        "aliases": [],
        "city": "舟山",
        "province": "浙江",
        "category": "scenic",
        "longitude": 122.1,
        "latitude": 30.1,
        "map_poi_id": "BTEST",
        "standard_province": "浙江省",
        "standard_city": "舟山市",
        "standard_district": "普陀区",
        "adcode": "330903",
        "address": "测试地址",
        "telephone": "unknown",
        "business_area": "unknown",
        "opening_hours": "unknown",
        "operational_status": "unknown",
        "map_match_confidence": 1.0,
        "map_match_level": "high",
        "geocode_status": "matched",
        "map_review_status": "auto_approved",
        "map_checked_at": "2026-07-17T00:00:00+00:00",
        "map_source": "amap_place_v3",
        "status": "active",
    }


def map_record() -> dict:
    return {
        "destination_id": "DEST_TEST",
        "binding_policy": "auto",
        "inventory_review": "approved",
        "geocode_status": "matched",
        "match_reasons": [
            "name_exact",
            "city_match",
            "province_match",
            "poi_type_match",
        ],
        "review_candidate": None,
    }


def travel_record() -> dict:
    return {
        "destination_id": "DEST_TEST",
        "destination_name": "测试岛",
        "origin_city": "上海",
        "origin_name": "上海虹桥站",
        "origin_type": "major_railway_station",
        "transport_mode": "公共交通",
        "travel_minutes": 120,
        "door_to_door_min": 110,
        "door_to_door_typical": 120,
        "door_to_door_max": 135,
        "rail_segment_min": 80,
        "rail_segment_typical": 90,
        "rail_segment_max": 95,
        "access_egress_min": 30,
        "access_egress_typical": 30,
        "access_egress_max": 40,
        "distance_m": 200000,
        "contains_ferry": False,
        "requires_ferry": True,
        "ferry_detection_sources": ["manual_override"],
        "railway_segments": [{"trip": "G1"}],
        "source": "test",
        "confidence": "低",
        "route_estimate": True,
        "route_sample_count": 2,
        "planned_sample_count": 3,
        "sample_dates": ["2026-07-18"],
        "sample_times": ["07:00"],
        "failure_reason": None,
        "partial_failure_reasons": ["one_sample_failed"],
        "raw_status": {"success_count": 2, "failure_count": 1},
        "note": "route estimate",
        "checked_at": "2026-07-17T00:00:00+00:00",
    }


def claim() -> dict:
    return {
        "claim_id": "CLM_TEST",
        "evidence_id": "EV_TEST",
        "entity_id": "DEST_TEST",
        "destination_id": "DEST_TEST",
        "note_id": "NOTE_TEST",
        "aspect": "transport",
        "polarity": "neutral",
        "claim": "测试",
        "key_quote": "测试",
        "mood": [],
        "vibe": [],
        "activity": [],
        "conditions": {},
        "author_hash": "AUTHOR",
        "publish_date": "2026-07-01",
        "collected_date": "2026-07-17",
        "source_quality": 0.8,
        "is_suspected_ad": False,
        "source_url": "https://example.com/test",
    }


class DatabaseContractTests(unittest.TestCase):
    def test_ddl_carries_map_payload_and_all_extended_travel_fields(self) -> None:
        ddl = (ROOT / "database" / "001_recommendation_v2.sql").read_text(
            encoding="utf-8"
        )
        for column in REQUIRED_MAP_COLUMNS | REQUIRED_TRAVEL_COLUMNS:
            self.assertIn(column, ddl)

    def test_loader_preserves_map_snapshot_and_partial_travel_failures(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            write_jsonl(data_dir / "entities.jsonl", [entity()])
            write_jsonl(data_dir / "destination_map_enrichment.jsonl", [map_record()])
            write_jsonl(data_dir / "destination_facts.jsonl", [])
            write_jsonl(data_dir / "travel_matrix.jsonl", [travel_record()])
            write_jsonl(data_dir / "ugc_claims.jsonl", [claim()])
            write_jsonl(data_dir / "destination_profiles.jsonl", [])
            ddl = data_dir / "schema.sql"
            ddl.write_text("SELECT 1;", encoding="utf-8")
            connection = RecordingConnection()

            with patch.object(load_v2, "ENV_VALUES", {"DATABASE_URL": "postgresql://test"}), patch.object(
                load_v2.psycopg, "connect", return_value=connection
            ), patch.object(
                sys,
                "argv",
                ["load_v2.py", "--data-dir", str(data_dir), "--ddl", str(ddl)],
            ), patch("builtins.print"):
                self.assertEqual(0, load_v2.main())

            entity_sql, entity_params = next(
                call for call in connection.recording_cursor.calls if "INSERT INTO recommendation_entities" in call[0]
            )
            travel_sql, travel_params = next(
                call for call in connection.recording_cursor.calls if "INSERT INTO travel_matrix" in call[0]
            )
            self.assertEqual(entity_sql.count("%s"), len(entity_params or ()))
            self.assertEqual(travel_sql.count("%s"), len(travel_params or ()))
            self.assertEqual(map_record(), json.loads((entity_params or ())[-2]))
            self.assertIn(["one_sample_failed"], travel_params or ())
            self.assertIn("", travel_params or ())
            self.assertTrue(connection.committed)

    def test_loader_rejects_incomplete_map_snapshot_before_connecting(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            write_jsonl(data_dir / "entities.jsonl", [entity()])
            for filename in (
                "destination_map_enrichment.jsonl",
                "destination_facts.jsonl",
                "travel_matrix.jsonl",
                "destination_profiles.jsonl",
            ):
                write_jsonl(data_dir / filename, [])
            write_jsonl(data_dir / "ugc_claims.jsonl", [claim()])
            with patch.object(load_v2, "ENV_VALUES", {"DATABASE_URL": "postgresql://test"}), patch.object(
                sys, "argv", ["load_v2.py", "--data-dir", str(data_dir)]
            ):
                with self.assertRaisesRegex(SystemExit, "未一一覆盖"):
                    load_v2.main()

    def test_verifier_interval_sql_and_success_contract(self) -> None:
        self.assertIn("door_to_door_min <= door_to_door_typical", interval_invalid_sql("door_to_door"))
        report = {
            "entities": 342,
            "facts": 60,
            "claims": 772,
            "claim_snapshot_rows": 772,
            "profiles": 60,
            "destinations": 60,
            "map_snapshot_rows": 60,
            "travel": 360,
            "travel_snapshot_rows": 360,
            "travel_missing_snapshot_keys": 0,
            "travel_extra_database_keys": 0,
            "travel_payload_rows": 360,
            "travel_payload_mismatches": 0,
            "travel_invalid_outcomes": 0,
            "travel_invalid_intervals": 0,
            "travel_invalid_samples": 0,
            "travel_invalid_ferry": 0,
            "travel_railway_rows": 136,
            "snapshot_travel_railway_rows": 136,
            "travel_partial_failure_rows": 2,
            "snapshot_travel_partial_failure_rows": 2,
            "travel_requires_ferry": 150,
            "snapshot_travel_requires_ferry": 150,
            "travel_contains_ferry": 0,
            "snapshot_travel_contains_ferry": 0,
            "map_payload_rows": 60,
            "map_status_counts": {"matched": 37, "region_geocoded": 9, "review_required": 14},
            "snapshot_map_status_counts": {
                "matched": 37,
                "region_geocoded": 9,
                "review_required": 14,
            },
            "map_invalid_rows": 0,
            "map_invalid_auto_bindings": 0,
            "map_api_failed": 0,
            "map_pending": 0,
            "missing_map_columns": [],
            "missing_travel_columns": [],
        }
        self.assertTrue(report_is_ok(report))
        report["travel_invalid_outcomes"] = 1
        self.assertFalse(report_is_ok(report))

    def test_verifier_reports_partial_jsonl_without_leaking_row_content(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "snapshot.jsonl"
            path.write_text('{"claim":"sensitive', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "snapshot.jsonl 第 1 行不是完整 JSON"):
                verifier_load_jsonl(path)

    def test_jsonl_reader_preserves_unicode_line_separator_inside_claim(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "snapshot.jsonl"
            payload = {"claim": "第一段\u2028第二段", "claim_id": "CLM_TEST"}
            path.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")
            self.assertEqual([payload], verifier_load_jsonl(path))
            self.assertEqual([payload], load_v2.load_jsonl(path))


if __name__ == "__main__":
    unittest.main()
