from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from inspitrip.pipelines.ugc.scripts.build_travel_matrix import (
    AmapApiClient,
    ApiResult,
    DEFAULT_FERRY_OVERRIDES,
    ORIGIN_SPECS,
    PersistentJsonCache,
    TravelCheckpoint,
    build_driving_row,
    build_parser,
    build_transit_row,
    contains_walk_type_30,
    load_amap_key,
    load_ferry_overrides,
    main,
    parse_transit_result,
    reapply_ferry_overrides,
)


DESTINATION = {
    "entity_id": "DEST_TEST",
    "entity_type": "destination",
    "name": "测试岛",
    "city": "舟山",
    "province": "浙江",
}


def amap_result(data: dict, *, cache_hit: bool = False) -> ApiResult:
    return ApiResult(
        data=data,
        raw_status={
            "status": str(data.get("status", "")),
            "info": str(data.get("info", "")),
            "infocode": str(data.get("infocode", "")),
            "http_status": 200,
            "cache_hit": cache_hit,
            "attempts": 1,
        },
    )


def transit_payload(duration: int, rail_duration: int, *, ferry: bool = False) -> dict:
    walking_steps = [{"instruction": "步行接驳", "walk_type": "30" if ferry else "0"}]
    return {
        "status": "1",
        "info": "OK",
        "infocode": "10000",
        "route": {
            "transits": [
                {
                    "duration": str(duration),
                    "distance": "240000",
                    "segments": [
                        {"walking": {"steps": walking_steps}},
                        {
                            "railway": {
                                "trip": "G1234",
                                "type": "G",
                                "time": str(rail_duration),
                                "departure_stop": {"name": "上海虹桥", "time": "08:00"},
                                "arrival_stop": {"name": "宁波", "time": "09:30"},
                            }
                        },
                    ],
                }
            ]
        },
    }


class FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200):
        self.payload = payload
        self.status_code = status_code

    def json(self) -> dict:
        return self.payload


class SequencedSession:
    def __init__(self, responses: list[object]):
        self.responses = list(responses)
        self.calls: list[dict] = []

    def get(self, url: str, *, params: dict, timeout: float):
        self.calls.append({"url": url, "params": params, "timeout": timeout})
        item = self.responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


class TransitClient:
    def __init__(self, results: list[ApiResult]):
        self.results = list(results)

    def transit(self, *args, **kwargs) -> ApiResult:
        return self.results.pop(0)


class DrivingClient:
    def __init__(self, result: ApiResult):
        self.result = result

    def driving(self, *args, **kwargs) -> ApiResult:
        return self.result


class TravelParsingTests(unittest.TestCase):
    def test_walk_type_30_is_found_recursively(self) -> None:
        payload = {"route": {"transits": [{"segments": [{"walking": {"steps": [{"walk_type": 30}]}}]}]}}
        self.assertTrue(contains_walk_type_30(payload))
        self.assertFalse(contains_walk_type_30({"walking": {"steps": [{"walk_type": "0"}]}}))

    def test_railway_object_and_ferry_are_parsed(self) -> None:
        route, failure = parse_transit_result(amap_result(transit_payload(7200, 5400, ferry=True)))
        self.assertIsNone(failure)
        self.assertTrue(route["contains_ferry"])
        self.assertEqual(5400, route["railway_seconds"])
        self.assertEqual("G1234", route["railway_segments"][0]["trip"])
        self.assertEqual("上海虹桥", route["railway_segments"][0]["departure_stop"])

    def test_transit_samples_aggregate_door_rail_and_access_ranges(self) -> None:
        failure = ApiResult(
            data={"status": "0", "info": "SERVICE_NOT_AVAILABLE", "infocode": "10016"},
            raw_status={
                "status": "0",
                "info": "SERVICE_NOT_AVAILABLE",
                "infocode": "10016",
                "http_status": 200,
                "cache_hit": False,
                "attempts": 4,
            },
            failure_reason="amap_error:10016",
        )
        client = TransitClient(
            [
                amap_result(transit_payload(7200, 5400, ferry=True)),
                amap_result(transit_payload(9000, 6000)),
                failure,
            ]
        )
        row = build_transit_row(
            client,
            DESTINATION,
            "上海",
            ORIGIN_SPECS["上海"],
            "121.3,31.2",
            "122.6,30.1",
            origin_city_code="310000",
            destination_city_code="330900",
            sample_dates=["2026-07-21"],
            sample_times=["07:00", "10:00", "14:00"],
            manual_ferry=False,
        )
        self.assertEqual(120, row["door_to_door_min"])
        self.assertEqual(135, row["travel_minutes"])
        self.assertEqual(150, row["door_to_door_max"])
        self.assertEqual((90, 95, 100), (
            row["rail_segment_min"], row["rail_segment_typical"], row["rail_segment_max"]
        ))
        self.assertEqual((30, 40, 50), (
            row["access_egress_min"], row["access_egress_typical"], row["access_egress_max"]
        ))
        self.assertEqual(2, row["route_sample_count"])
        self.assertEqual(3, row["planned_sample_count"])
        self.assertEqual(1, row["raw_status"]["failure_count"])
        self.assertTrue(row["contains_ferry"])
        self.assertTrue(row["requires_ferry"])
        self.assertIn("amap_walk_type_30", row["ferry_detection_sources"])
        self.assertIn("不是完整时刻表", row["note"])
        self.assertIsNone(row["failure_reason"])

    def test_failed_route_is_persistable_and_manual_ferry_is_visible(self) -> None:
        client = DrivingClient(
            amap_result({"status": "1", "info": "OK", "infocode": "10000", "route": {"paths": []}})
        )
        row = build_driving_row(
            client,
            DESTINATION,
            "上海",
            ORIGIN_SPECS["上海"],
            "121.3,31.2",
            "122.6,30.1",
            manual_ferry=True,
        )
        self.assertIsNone(row["travel_minutes"])
        self.assertEqual("amap_no_driving_route", row["failure_reason"])
        self.assertEqual("高德地图 Web 服务 API / 驾车路径规划", row["source"])
        self.assertEqual("低", row["confidence"])
        self.assertTrue(row["requires_ferry"])
        self.assertFalse(row["contains_ferry"])
        self.assertIn("manual_override", row["ferry_detection_sources"])
        self.assertIn("实际班次", row["note"])
        self.assertTrue(row["checked_at"].endswith("+00:00"))
        self.assertEqual(1, row["raw_status"]["failure_count"])


class CacheAndRetryTests(unittest.TestCase):
    def test_cache_retries_windows_permission_error_during_atomic_replace(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / "cache.json"
            cache = PersistentJsonCache(cache_path, replace_retries=3)
            original_replace = PersistentJsonCache._replace
            attempts = {"count": 0}

            def flaky_replace(instance, temp):
                attempts["count"] += 1
                if attempts["count"] == 1:
                    raise PermissionError("temporary scanner lock")
                return original_replace(instance, temp)

            with patch.object(PersistentJsonCache, "_replace", new=flaky_replace), patch(
                "inspitrip.pipelines.ugc.scripts.build_travel_matrix.time.sleep"
            ):
                cache.put(
                    "geocode/geo",
                    {"address": "测试"},
                    {"status": "1"},
                    observed_at="now",
                )

            self.assertEqual(2, attempts["count"])
            payload = json.loads(cache_path.read_text(encoding="utf-8"))
            entry = payload["entries"][next(iter(cache.entries))]
            self.assertEqual("1", entry["data"]["status"])

    def test_empty_success_payload_is_not_cached_so_failures_can_retry(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            empty = FakeResponse({"status": "1", "info": "OK", "infocode": "10000", "geocodes": []})
            session = SequencedSession([empty, empty])
            client = AmapApiClient(
                "secret",
                PersistentJsonCache(Path(temp_dir) / "cache.json"),
                qps=0,
                max_retries=0,
                session=session,
            )
            client.geocode("不存在地点", "上海")
            second = client.geocode("不存在地点", "上海")
            self.assertFalse(second.raw_status["cache_hit"])
            self.assertEqual(2, len(session.calls))

    def test_retry_then_persistent_cache_and_no_key_at_rest(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / "cache.json"
            session = SequencedSession(
                [
                    TimeoutError("temporary"),
                    FakeResponse(
                        {
                            "status": "1",
                            "info": "OK",
                            "infocode": "10000",
                            "geocodes": [{"location": "121.1,31.1", "city": "上海", "adcode": "310000"}],
                        }
                    ),
                ]
            )
            sleeps: list[float] = []
            client = AmapApiClient(
                "super-secret-key",
                PersistentJsonCache(cache_path),
                qps=0,
                max_retries=1,
                retry_backoff=0.25,
                session=session,
                sleep=sleeps.append,
            )
            first = client.geocode("上海虹桥站", "上海")
            second = client.geocode("上海虹桥站", "上海")
            self.assertEqual("1", first.raw_status["status"])
            self.assertTrue(second.raw_status["cache_hit"])
            self.assertEqual(first.raw_status["observed_at"], second.raw_status["observed_at"])
            self.assertEqual(2, len(session.calls))
            self.assertEqual([0.25], sleeps)
            self.assertNotIn("super-secret-key", cache_path.read_text(encoding="utf-8"))

            unused_session = SequencedSession([])
            new_client = AmapApiClient(
                "super-secret-key",
                PersistentJsonCache(cache_path),
                qps=0,
                session=unused_session,
            )
            persisted = new_client.geocode("上海虹桥站", "上海")
            self.assertTrue(persisted.raw_status["cache_hit"])
            self.assertEqual([], unused_session.calls)

    def test_env_file_wins_and_shell_env_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env_path = Path(temp_dir) / ".env"
            env_path.write_text("AMAP_KEY=file-value\n", encoding="utf-8")
            with patch.dict(os.environ, {"AMAP_KEY": "shell-value"}):
                self.assertEqual("file-value", load_amap_key(env_path))


class ResumeAndCliTests(unittest.TestCase):
    def test_manual_ferry_overrides_are_exact_and_not_name_heuristics(self) -> None:
        override_ids, override_names = load_ferry_overrides(DEFAULT_FERRY_OVERRIDES)
        self.assertEqual({"DEMO_QINGLAN_ISLAND"}, override_ids)
        self.assertEqual(set(), override_names)

        entity_path = DEFAULT_FERRY_OVERRIDES.parent / "entities.jsonl"
        destinations = {
            row["entity_id"]: row["name"]
            for row in (json.loads(line) for line in entity_path.read_text(encoding="utf-8").splitlines())
            if row.get("entity_type") == "destination"
        }
        self.assertTrue(override_ids.issubset(destinations))
        self.assertNotIn("苏州西山岛", {destinations[item] for item in override_ids})
        self.assertNotIn("连岛", {destinations[item] for item in override_ids})

    def test_existing_checkpoint_can_reapply_overrides_without_touching_route_facts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "travel.jsonl"
            failures = Path(temp_dir) / "failures.jsonl"
            checkpoint = TravelCheckpoint(output, failures)
            base = {
                "origin_city": "上海",
                "transport_mode": "自驾",
                "travel_minutes": 90,
                "checked_at": "2026-07-17T00:00:00+00:00",
                "contains_ferry": False,
                "requires_ferry": False,
                "ferry_detection_sources": [],
                "failure_reason": None,
                "raw_status": {},
                "route_sample_count": 1,
                "source": "test",
                "confidence": "中",
                "note": "保留原路线说明",
            }
            checkpoint.upsert({**base, "destination_id": "DEST_5C5684F7BE", "destination_name": "干斜渔村"})
            checkpoint.upsert(
                {
                    **base,
                    "destination_id": "DEST_86498B7478",
                    "destination_name": "猴岛",
                    "requires_ferry": True,
                    "ferry_detection_sources": ["manual_override"],
                    "confidence": "低",
                    "note": "该路线可能包含轮渡，实际班次和候船时间请出发前确认。",
                }
            )
            checkpoint.upsert(
                {
                    **base,
                    "destination_id": "DEST_API",
                    "destination_name": "API轮渡样例",
                    "contains_ferry": True,
                    "requires_ferry": True,
                    "ferry_detection_sources": [],
                    "confidence": "低",
                }
            )
            destinations = [
                {"entity_id": "DEST_5C5684F7BE", "name": "干斜渔村"},
                {"entity_id": "DEST_86498B7478", "name": "猴岛"},
                {"entity_id": "DEST_API", "name": "API轮渡样例"},
            ]
            stats = reapply_ferry_overrides(
                checkpoint,
                destinations,
                {},
                {"DEST_5C5684F7BE"},
                set(),
            )
            manual = checkpoint.rows[("DEST_5C5684F7BE", "上海", "自驾")]
            monkey = checkpoint.rows[("DEST_86498B7478", "上海", "自驾")]
            api = checkpoint.rows[("DEST_API", "上海", "自驾")]
            self.assertTrue(manual["requires_ferry"])
            self.assertFalse(manual["contains_ferry"])
            self.assertEqual(["manual_override"], manual["ferry_detection_sources"])
            self.assertEqual(90, manual["travel_minutes"])
            self.assertEqual("2026-07-17T00:00:00+00:00", manual["checked_at"])
            self.assertIn("保留原路线说明", manual["note"])
            self.assertFalse(monkey["requires_ferry"])
            self.assertEqual([], monkey["ferry_detection_sources"])
            self.assertNotIn("轮渡", monkey["note"])
            self.assertTrue(api["contains_ferry"])
            self.assertEqual(["amap_walk_type_30"], api["ferry_detection_sources"])
            self.assertEqual(3, stats["rows_changed"])
            self.assertEqual(1, stats["manual_override_rows"])
            self.assertEqual(1, stats["api_detected_rows"])

    def test_reapply_cli_does_not_load_key_or_construct_network_client(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            entities = root / "entities.jsonl"
            facts = root / "facts.jsonl"
            output = root / "travel.jsonl"
            failures = root / "failures.jsonl"
            overrides = root / "overrides.json"
            entities.write_text(
                json.dumps({"entity_id": "DEST_X", "entity_type": "destination", "name": "测试村"}, ensure_ascii=False)
                + "\n",
                encoding="utf-8",
            )
            facts.write_text("", encoding="utf-8")
            output.write_text(
                json.dumps(
                    {
                        "destination_id": "DEST_X",
                        "destination_name": "测试村",
                        "origin_city": "上海",
                        "transport_mode": "自驾",
                        "travel_minutes": 60,
                        "checked_at": "2026-07-17T00:00:00+00:00",
                        "contains_ferry": False,
                        "requires_ferry": False,
                        "ferry_detection_sources": [],
                        "failure_reason": None,
                        "raw_status": {},
                        "route_sample_count": 1,
                        "source": "test",
                        "confidence": "中",
                        "note": "",
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            overrides.write_text(
                json.dumps({"destination_ids": {"DEST_X": {"name": "测试村"}}, "destination_names": {}}, ensure_ascii=False),
                encoding="utf-8",
            )
            with patch(
                "inspitrip.pipelines.ugc.scripts.build_travel_matrix.load_amap_key",
                side_effect=AssertionError("offline reapply must not load API key"),
            ):
                exit_code = main(
                    [
                        "--entities", str(entities),
                        "--facts", str(facts),
                        "--output", str(output),
                        "--failure-log", str(failures),
                        "--ferry-overrides", str(overrides),
                        "--reapply-overrides-only",
                    ]
                )
            self.assertEqual(0, exit_code)
            row = json.loads(output.read_text(encoding="utf-8"))
            self.assertTrue(row["requires_ferry"])
            self.assertEqual(["manual_override"], row["ferry_detection_sources"])

    def test_checkpoint_skips_complete_rows_and_can_retry_failures(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "travel.jsonl"
            failures = Path(temp_dir) / "failures.jsonl"
            checkpoint = TravelCheckpoint(output, failures)
            success = {
                "destination_id": "D1",
                "origin_city": "上海",
                "transport_mode": "自驾",
                "travel_minutes": 90,
                "checked_at": "2026-07-17T00:00:00+00:00",
                "contains_ferry": False,
                "failure_reason": None,
                "raw_status": {},
                "route_sample_count": 1,
                "source": "test",
            }
            failure = {
                **success,
                "transport_mode": "公共交通",
                "travel_minutes": None,
                "failure_reason": "no_route",
                "route_sample_count": 0,
            }
            checkpoint.upsert(success)
            checkpoint.upsert(failure)
            self.assertTrue(checkpoint.should_skip(("D1", "上海", "自驾"), retry_failures=True))
            self.assertTrue(checkpoint.should_skip(("D1", "上海", "公共交通"), retry_failures=False))
            self.assertFalse(checkpoint.should_skip(("D1", "上海", "公共交通"), retry_failures=True))
            failure_rows = [json.loads(line) for line in failures.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(1, len(failure_rows))

    def test_limit_5_and_reproducible_origins_are_exposed_by_cli(self) -> None:
        args = build_parser().parse_args(["--limit", "5"])
        self.assertEqual(5, args.limit)
        self.assertEqual(["上海", "杭州", "苏州"], args.origins)
        self.assertEqual("上海虹桥站", ORIGIN_SPECS["上海"]["label"])
        self.assertEqual("杭州东站", ORIGIN_SPECS["杭州"]["label"])
        self.assertEqual("苏州站", ORIGIN_SPECS["苏州"]["label"])


if __name__ == "__main__":
    unittest.main()
