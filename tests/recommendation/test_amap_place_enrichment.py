from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import requests
from jsonschema import validate


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from inspitrip.recommendation.amap_place_enrichment import (
    AmapPlaceClient,
    apply_enrichment_to_entities,
    build_enrichment_records,
    build_failure_records,
    enrich_destination,
    load_amap_key,
    load_overrides,
    read_jsonl,
)


class FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self) -> dict:
        return self.payload


class FakeSession:
    def __init__(self, responses: list[dict | Exception]) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    def get(self, endpoint: str, *, params: dict, timeout: float) -> FakeResponse:
        self.calls.append({"endpoint": endpoint, "params": params, "timeout": timeout})
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return FakeResponse(response)


def entity(
    destination_id: str = "DEST_TEST",
    name: str = "测试岛",
    *,
    city: str = "舟山",
    province: str = "浙江",
) -> dict:
    return {
        "entity_id": destination_id,
        "legacy_poi_id": "POI_TEST",
        "legacy_poi_ids": ["POI_TEST"],
        "entity_type": "destination",
        "parent_id": None,
        "name": name,
        "aliases": [],
        "city": city,
        "province": province,
        "category": "scenic",
        "longitude": None,
        "latitude": None,
        "map_poi_id": None,
        "status": "active",
    }


def place_payload(
    *,
    name: str = "测试岛",
    city: str = "舟山市",
    province: str = "浙江省",
    district: str = "普陀区",
    poi_id: str = "B000TEST",
    poi_type: str = "风景名胜;风景名胜相关;旅游景点",
    typecode: str = "110200",
) -> dict:
    return {
        "status": "1",
        "pois": [
            {
                "id": poi_id,
                "name": name,
                "location": "122.1001,30.2002",
                "pname": province,
                "cityname": city,
                "adname": district,
                "adcode": "330903",
                "address": "测试地址",
                "type": poi_type,
                "typecode": typecode,
                "tel": [],
                "business_area": [],
                "biz_ext": {},
            }
        ],
    }


def geocode_payload(
    *, name: str = "连云港市", city: str = "连云港市", province: str = "江苏省"
) -> dict:
    return {
        "status": "1",
        "geocodes": [
            {
                "formatted_address": name,
                "province": province,
                "city": city,
                "district": [],
                "adcode": "320700",
                "location": "119.2216,34.5967",
            }
        ],
    }


class AmapPlaceEnrichmentTests(unittest.TestCase):
    def client(self, cache_dir: Path, responses: list[dict | Exception], **kwargs) -> AmapPlaceClient:
        return AmapPlaceClient(
            "test-key",
            cache_dir,
            qps=100000,
            max_retries=kwargs.pop("max_retries", 1),
            session=FakeSession(responses),
            sleep=lambda _seconds: None,
            **kwargs,
        )

    def test_key_is_loaded_only_from_fixed_env_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text("AMAP_KEY=file-key\n", encoding="utf-8")
            with patch.dict(os.environ, {"AMAP_KEY": "process-key"}):
                self.assertEqual("file-key", load_amap_key(env_path))

    def test_exact_place_and_admin_match_is_bound(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session = FakeSession([place_payload()])
            client = AmapPlaceClient(
                "test-key", Path(tmp), qps=100000, max_retries=1, session=session
            )
            row = enrich_destination(
                entity(),
                {"inventory_review": "approved", "binding_policy": "auto"},
                client,
                checked_at="2026-07-17T00:00:00+00:00",
            )
            self.assertEqual("matched", row["geocode_status"])
            self.assertEqual("auto_approved", row["map_review_status"])
            self.assertEqual("B000TEST", row["map_poi_id"])
            self.assertEqual({"longitude": 122.1001, "latitude": 30.2002}, row["coordinates"])
            self.assertEqual("浙江省", row["standard_admin"]["province"])
            self.assertEqual("unknown", row["basic_operations"]["opening_hours"])
            self.assertEqual("unknown", row["basic_operations"]["operational_status"])
            self.assertGreaterEqual(row["match_confidence"], 0.88)

    def test_admin_mismatch_never_binds_wrong_poi(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client = self.client(Path(tmp), [place_payload(city="三亚市", province="海南省")])
            row = enrich_destination(
                entity(),
                {"inventory_review": "approved", "binding_policy": "auto"},
                client,
                checked_at="2026-07-17T00:00:00+00:00",
            )
            self.assertEqual("review_required", row["geocode_status"])
            self.assertIsNone(row["map_poi_id"])
            self.assertIsNone(row["coordinates"])
            self.assertEqual("三亚市", row["review_candidate"]["standard_city"])
            self.assertIn("city_mismatch", row["match_reasons"])

    def test_region_uses_geocode_first_without_poi_binding(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session = FakeSession([geocode_payload()])
            client = AmapPlaceClient(
                "test-key", Path(tmp), qps=100000, max_retries=1, session=session
            )
            row = enrich_destination(
                entity(name="连云港", city="连云港", province="江苏"),
                {"inventory_review": "approved", "binding_policy": "region"},
                client,
                checked_at="2026-07-17T00:00:00+00:00",
            )
            self.assertEqual("region_geocoded", row["geocode_status"])
            self.assertEqual("manual_approved_region", row["map_review_status"])
            self.assertIsNone(row["map_poi_id"])
            self.assertEqual({"longitude": 119.2216, "latitude": 34.5967}, row["coordinates"])
            self.assertEqual(1, len(session.calls))
            self.assertIn("geocode/geo", session.calls[0]["endpoint"])

    def test_county_level_input_can_match_candidate_district(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client = self.client(
                Path(tmp),
                [place_payload(city="杭州市", province="浙江省", district="桐庐县")],
            )
            row = enrich_destination(
                entity(city="桐庐", province="浙江"),
                {"inventory_review": "approved", "binding_policy": "auto"},
                client,
            )
            self.assertEqual("matched", row["geocode_status"])
            self.assertIn("district_match", row["match_reasons"])

    def test_high_name_score_with_wrong_poi_type_is_not_bound(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client = self.client(
                Path(tmp),
                [
                    place_payload(
                        poi_type="住宿服务;旅馆招待所;旅馆招待所",
                        typecode="100200",
                    )
                ],
            )
            row = enrich_destination(
                entity(),
                {"inventory_review": "approved", "binding_policy": "auto"},
                client,
            )
            self.assertEqual("review_required", row["geocode_status"])
            self.assertIsNone(row["map_poi_id"])
            self.assertIn("poi_type_mismatch", row["match_reasons"])

    def test_auto_search_continues_until_name_admin_and_type_all_match(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client = self.client(
                Path(tmp),
                [
                    place_payload(
                        poi_id="BWRONGTYPE",
                        poi_type="住宿服务;旅馆招待所;旅馆招待所",
                        typecode="100200",
                    ),
                    place_payload(poi_id="BRIGHTTYPE"),
                ],
            )
            row = enrich_destination(
                entity(),
                {
                    "inventory_review": "approved",
                    "binding_policy": "auto",
                    "query_terms": ["第一轮", "第二轮"],
                },
                client,
            )
            self.assertEqual("matched", row["geocode_status"])
            self.assertEqual("BRIGHTTYPE", row["map_poi_id"])
            self.assertEqual(2, len(client.session.calls))
            self.assertIn("poi_type_match", row["match_reasons"])

    def test_manual_only_keeps_candidate_but_does_not_bind(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client = self.client(Path(tmp), [place_payload()])
            row = enrich_destination(
                entity(),
                {"inventory_review": "review_required", "binding_policy": "manual_only"},
                client,
            )
            self.assertEqual("review_required", row["geocode_status"])
            self.assertIsNone(row["map_poi_id"])
            self.assertIsNone(row["coordinates"])
            self.assertEqual("B000TEST", row["review_candidate"]["map_poi_id"])

    def test_manual_override_requires_traceability_and_can_bind(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client = self.client(Path(tmp), [])
            override = {
                "inventory_review": "approved",
                "binding_policy": "auto",
                "manual_match": {
                    "map_poi_id": "BMANUAL",
                    "name": "测试岛",
                    "longitude": 122.1,
                    "latitude": 30.2,
                    "source": "人工核验记录",
                    "checked_at": "2026-07-17T08:00:00+08:00",
                },
            }
            row = enrich_destination(entity(), override, client)
            self.assertEqual("manual_override", row["geocode_status"])
            self.assertEqual("BMANUAL", row["map_poi_id"])
            self.assertEqual("人工核验记录", row["source"])

    def test_cache_is_idempotent_and_never_persists_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            online_session = FakeSession([place_payload()])
            online = AmapPlaceClient(
                "secret-never-cache", cache, qps=100000, max_retries=1, session=online_session
            )
            pois, cache_hit = online.search_place("测试岛", "舟山")
            self.assertFalse(cache_hit)
            self.assertEqual(1, len(pois))

            offline_session = FakeSession([])
            offline = AmapPlaceClient(
                None, cache, qps=100000, max_retries=1, session=offline_session, offline=True
            )
            cached_pois, cache_hit = offline.search_place("测试岛", "舟山")
            self.assertTrue(cache_hit)
            self.assertEqual(pois, cached_pois)
            self.assertEqual([], offline_session.calls)
            cache_text = "".join(path.read_text(encoding="utf-8") for path in cache.glob("*.json"))
            self.assertNotIn("secret-never-cache", cache_text)
            self.assertNotIn('"key"', cache_text)

    def test_retry_recovers_and_failure_record_is_sanitized(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session = FakeSession([requests.Timeout("timeout"), place_payload()])
            client = AmapPlaceClient(
                "test-key",
                Path(tmp),
                qps=100000,
                max_retries=2,
                session=session,
                sleep=lambda _seconds: None,
            )
            row = enrich_destination(
                entity(), {"inventory_review": "approved", "binding_policy": "auto"}, client
            )
            self.assertEqual("matched", row["geocode_status"])
            self.assertEqual(2, len(session.calls))

            failed = dict(row)
            failed.update({"geocode_status": "api_failed", "failure_reason": "timeout"})
            failures = build_failure_records([failed])
            self.assertEqual("timeout", failures[0]["failure_reason"])
            self.assertNotIn("test-key", json.dumps(failures, ensure_ascii=False))

    def test_invalid_key_is_not_retried(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session = FakeSession(
                [{"status": "0", "info": "INVALID_USER_KEY", "infocode": "10001"}]
            )
            client = AmapPlaceClient(
                "test-key",
                Path(tmp),
                qps=100000,
                max_retries=3,
                session=session,
                sleep=lambda _seconds: None,
            )
            row = enrich_destination(
                entity(), {"inventory_review": "approved", "binding_policy": "auto"}, client
            )
            self.assertEqual("api_failed", row["geocode_status"])
            self.assertEqual(1, len(session.calls))

    def test_resume_limit_preserves_one_explicit_status_per_destination(self) -> None:
        entities = [entity(f"DEST_{index:02d}", f"目的地{index}") for index in range(3)]
        overrides = {
            row["entity_id"]: {"inventory_review": "approved", "binding_policy": "auto"}
            for row in entities
        }
        with tempfile.TemporaryDirectory() as tmp:
            client = self.client(Path(tmp), [])
            rows = build_enrichment_records(entities, overrides, client, limit=0)
        self.assertEqual(3, len(rows))
        self.assertTrue(all(row["geocode_status"] == "pending" for row in rows))

    def test_apply_is_idempotent_and_keeps_unknown_for_missing_operations(self) -> None:
        original = entity()
        record = {
            "destination_id": "DEST_TEST",
            "coordinates": {"longitude": 122.1, "latitude": 30.2},
            "map_poi_id": "BTEST",
            "standard_admin": {"province": "浙江省", "city": "舟山市", "district": "普陀区", "adcode": "330903"},
            "address": "测试地址",
            "basic_operations": {"telephone": "unknown", "business_area": "unknown", "opening_hours": "unknown", "operational_status": "unknown"},
            "match_confidence": 1.0,
            "match_level": "high",
            "geocode_status": "matched",
            "map_review_status": "auto_approved",
            "checked_at": "2026-07-17T00:00:00+00:00",
            "source": "amap_place_v3",
        }
        once = apply_enrichment_to_entities([original], [record])
        twice = apply_enrichment_to_entities(once, [record])
        self.assertEqual(once, twice)
        self.assertEqual("unknown", once[0]["opening_hours"])
        self.assertEqual("active", once[0]["status"])


class RepositoryMapInventoryTests(unittest.TestCase):
    def test_public_inventory_contains_only_synthetic_destinations(self) -> None:
        entities = read_jsonl(ROOT / "data" / "demo" / "entities.jsonl")
        destinations = [row for row in entities if row.get("entity_type") == "destination"]
        self.assertEqual(6, len(destinations))
        self.assertTrue(all(row["entity_id"].startswith("DEMO_") for row in destinations))

    def test_pending_snapshot_and_entity_projection_validate_against_schemas(self) -> None:
        entities = read_jsonl(ROOT / "data" / "demo" / "entities.jsonl")
        with tempfile.TemporaryDirectory() as tmp:
            client = AmapPlaceClient(None, Path(tmp), offline=True)
            records = build_enrichment_records(entities, {}, client, limit=0)
        map_schema = json.loads(
            (ROOT / "schemas" / "destination_map_enrichment_schema.json").read_text(
                encoding="utf-8"
            )
        )
        entity_schema = json.loads(
            (ROOT / "schemas" / "entity_schema.json").read_text(encoding="utf-8")
        )
        for record in records:
            validate(record, map_schema)
        for row in apply_enrichment_to_entities(entities, records):
            validate(row, entity_schema)

    def test_public_snapshot_has_no_real_map_bindings(self) -> None:
        entities = read_jsonl(ROOT / "data" / "demo" / "entities.jsonl")
        self.assertTrue(all(not row.get("map_poi_id") for row in entities))


if __name__ == "__main__":
    unittest.main()
