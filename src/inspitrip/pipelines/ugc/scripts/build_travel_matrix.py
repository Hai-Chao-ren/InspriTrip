#!/usr/bin/env python
from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

import requests
from dotenv import dotenv_values


from inspitrip.paths import DEFAULT_ENV_PATH, DEMO_DATA_DIR, REPO_ROOT

ROOT = REPO_ROOT
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from inspitrip.recommendation.v2_pipeline import load_jsonl, write_jsonl


ENV_PATH = DEFAULT_ENV_PATH
DATA_DIR = DEMO_DATA_DIR
DEFAULT_CACHE = DATA_DIR / "amap_travel_cache.json"
DEFAULT_OUTPUT = DATA_DIR / "travel_matrix.jsonl"
DEFAULT_FAILURE_LOG = DATA_DIR / "travel_matrix_failures.jsonl"
DEFAULT_FERRY_OVERRIDES = DEMO_DATA_DIR / "travel_route_overrides.json"
FERRY_NOTE = "该路线可能包含轮渡，实际班次和候船时间请出发前确认。"
TRANSIT_ESTIMATE_NOTE = "该结果为指定日期和时段下的高德路线估算，不是完整时刻表。"
RAIL_ESTIMATE_NOTE = "铁路段仅为高德路线估算，不代表完整班次、票价或余票。"

# 产品固定起点：用主要铁路站而不是含义不清的“城市中心”。名称、城市和类型写入每条记录。
ORIGIN_SPECS: dict[str, dict[str, str]] = {
    "上海": {"address": "上海虹桥站", "city": "上海", "label": "上海虹桥站", "type": "major_railway_station"},
    "杭州": {"address": "杭州东站", "city": "杭州", "label": "杭州东站", "type": "major_railway_station"},
    "苏州": {"address": "苏州站", "city": "苏州", "label": "苏州站", "type": "major_railway_station"},
}
DEFAULT_ORIGINS = list(ORIGIN_SPECS)
DEFAULT_SAMPLE_TIMES = ["07:00", "10:00", "14:00", "18:00"]
RETRIABLE_INFOCODES = {"10010", "10016", "10019", "10020", "10021"}
REQUIRED_CHECKPOINT_FIELDS = {
    "checked_at",
    "contains_ferry",
    "failure_reason",
    "raw_status",
    "route_sample_count",
    "source",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_amap_key(env_path: Path = ENV_PATH) -> str:
    """只读取仓库 .env；故意不接受 shell 环境变量覆盖。"""
    key = str(dotenv_values(env_path).get("AMAP_KEY") or "").strip()
    if not key:
        raise RuntimeError(f"AMAP_KEY is missing from the fixed config file: {env_path}")
    return key


def representative_sample_dates(today: date | None = None) -> list[str]:
    """返回未来一个工作日（周二）和周末（周六），避免请求已过去的当天时段。"""
    base = today or date.today()

    def next_weekday(target: int) -> date:
        delta = (target - base.weekday()) % 7
        if delta == 0:
            delta = 7
        return base + timedelta(days=delta)

    return [next_weekday(1).isoformat(), next_weekday(5).isoformat()]


def validate_sample_date(value: str) -> str:
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("date must use YYYY-MM-DD") from exc
    return value


def validate_sample_time(value: str) -> str:
    try:
        datetime.strptime(value, "%H:%M")
    except ValueError as exc:
        raise argparse.ArgumentTypeError("time must use HH:MM") from exc
    return value


def validate_origin(value: str) -> str:
    if value not in ORIGIN_SPECS:
        raise argparse.ArgumentTypeError("origin must be one of the three configured cities")
    return value


class PersistentJsonCache:
    """原子落盘的请求缓存；缓存键和内容都不包含 API Key。"""

    def __init__(self, path: Path, *, flush_every: int = 1, replace_retries: int = 8):
        self.path = path
        self.flush_every = max(1, int(flush_every))
        self.replace_retries = max(1, int(replace_retries))
        self._dirty_count = 0
        self.entries: dict[str, dict[str, Any]] = {}
        if path.exists():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(payload, dict) and isinstance(payload.get("entries"), dict):
                    self.entries = payload["entries"]
            except (OSError, json.JSONDecodeError):
                # 缓存损坏不应阻止事实构建；后续成功响应会重建文件。
                self.entries = {}

    @staticmethod
    def make_key(endpoint: str, params: dict[str, Any]) -> str:
        safe_params = {key: value for key, value in params.items() if key.lower() != "key"}
        payload = json.dumps(
            {"endpoint": endpoint, "params": safe_params},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def get(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any] | None:
        entry = self.entries.get(self.make_key(endpoint, params))
        if not isinstance(entry, dict) or not isinstance(entry.get("data"), dict):
            return None
        return entry

    def put(
        self,
        endpoint: str,
        params: dict[str, Any],
        data: dict[str, Any],
        *,
        observed_at: str,
    ) -> None:
        self.entries[self.make_key(endpoint, params)] = {"cached_at": observed_at, "data": data}
        self._dirty_count += 1
        if self._dirty_count >= self.flush_every:
            self.flush()

    def _replace(self, temp: Path) -> None:
        temp.replace(self.path)

    def flush(self) -> None:
        if self._dirty_count <= 0:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {"version": 1, "entries": self.entries},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        last_error: PermissionError | None = None
        for attempt in range(self.replace_retries):
            temp = self.path.with_name(f"{self.path.name}.{time.time_ns()}.{attempt}.tmp")
            temp.write_text(payload, encoding="utf-8")
            try:
                self._replace(temp)
                self._dirty_count = 0
                return
            except PermissionError as exc:
                last_error = exc
                try:
                    temp.unlink(missing_ok=True)
                except OSError:
                    pass
                time.sleep(min(0.05 * (attempt + 1), 0.4))
        if last_error:
            raise last_error


class QpsLimiter:
    def __init__(
        self,
        qps: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.interval = 1.0 / qps if qps > 0 else 0.0
        self.clock = clock
        self.sleep = sleep
        self.last_request_at: float | None = None

    def wait(self) -> None:
        if not self.interval:
            return
        now = self.clock()
        if self.last_request_at is not None:
            remaining = self.interval - (now - self.last_request_at)
            if remaining > 0:
                self.sleep(remaining)
                now = self.clock()
        self.last_request_at = now


@dataclass
class ApiResult:
    data: dict[str, Any] | None
    raw_status: dict[str, Any]
    failure_reason: str | None = None


class AmapApiClient:
    BASE_URL = "https://restapi.amap.com/v3"

    def __init__(
        self,
        api_key: str,
        cache: PersistentJsonCache,
        *,
        qps: float = 2.5,
        max_retries: int = 3,
        retry_backoff: float = 0.5,
        timeout: float = 15.0,
        session: Any | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.api_key = api_key
        self.cache = cache
        self.max_retries = max(0, max_retries)
        self.retry_backoff = max(0.0, retry_backoff)
        self.timeout = timeout
        self.session = session or requests.Session()
        self.sleep = sleep
        self.limiter = QpsLimiter(qps, clock=clock, sleep=sleep)

    def get(self, endpoint: str, params: dict[str, Any]) -> ApiResult:
        cached_entry = self.cache.get(endpoint, params)
        if cached_entry is not None:
            cached_data = cached_entry["data"]
            observed_at = str(cached_entry.get("cached_at") or utc_now())
            return ApiResult(
                cached_data,
                self._raw_status(cached_data, cache_hit=True, attempts=0, observed_at=observed_at),
            )

        last_status: dict[str, Any] = {
            "status": "NOT_REQUESTED",
            "info": "",
            "infocode": "",
            "http_status": None,
            "cache_hit": False,
            "attempts": 0,
            "observed_at": utc_now(),
        }
        last_reason = "request_not_completed"
        for attempt in range(self.max_retries + 1):
            if attempt:
                self.sleep(self.retry_backoff * (2 ** (attempt - 1)))
            self.limiter.wait()
            try:
                response = self.session.get(
                    f"{self.BASE_URL}/{endpoint}",
                    params={**params, "key": self.api_key},
                    timeout=self.timeout,
                )
            except Exception as exc:  # requests 和测试替身都归一为不含 URL/Key 的类别。
                last_status = {
                    "status": "NETWORK_ERROR",
                    "info": type(exc).__name__,
                    "infocode": "",
                    "http_status": None,
                    "cache_hit": False,
                    "attempts": attempt + 1,
                    "observed_at": utc_now(),
                }
                last_reason = f"network_error:{type(exc).__name__}"
                continue

            if response.status_code == 429 or response.status_code >= 500:
                last_status = {
                    "status": "HTTP_ERROR",
                    "info": "",
                    "infocode": "",
                    "http_status": response.status_code,
                    "cache_hit": False,
                    "attempts": attempt + 1,
                    "observed_at": utc_now(),
                }
                last_reason = f"http_error:{response.status_code}"
                continue
            if response.status_code >= 400:
                last_status = {
                    "status": "HTTP_ERROR",
                    "info": "",
                    "infocode": "",
                    "http_status": response.status_code,
                    "cache_hit": False,
                    "attempts": attempt + 1,
                    "observed_at": utc_now(),
                }
                return ApiResult(None, last_status, f"http_error:{response.status_code}")

            try:
                data = response.json()
            except Exception as exc:
                last_status = {
                    "status": "INVALID_JSON",
                    "info": type(exc).__name__,
                    "infocode": "",
                    "http_status": response.status_code,
                    "cache_hit": False,
                    "attempts": attempt + 1,
                    "observed_at": utc_now(),
                }
                last_reason = "invalid_json_response"
                continue
            if not isinstance(data, dict):
                last_status = {
                    "status": "INVALID_PAYLOAD",
                    "info": "",
                    "infocode": "",
                    "http_status": response.status_code,
                    "cache_hit": False,
                    "attempts": attempt + 1,
                    "observed_at": utc_now(),
                }
                last_reason = "invalid_json_payload"
                continue

            observed_at = utc_now()
            status = self._raw_status(
                data,
                cache_hit=False,
                attempts=attempt + 1,
                http_status=response.status_code,
                observed_at=observed_at,
            )
            if str(data.get("status")) == "1":
                # 空 geocode/route 结果不缓存，否则 --retry-failures 会永远命中旧失败。
                if self._has_usable_payload(endpoint, data):
                    self.cache.put(endpoint, params, data, observed_at=observed_at)
                return ApiResult(data, status)

            last_status = status
            info_code = str(data.get("infocode") or "")
            info = self._safe_text(data.get("info"))
            last_reason = f"amap_error:{info_code or info or 'unknown'}"
            if info_code not in RETRIABLE_INFOCODES:
                return ApiResult(data, status, last_reason)

        return ApiResult(None, last_status, last_reason)

    def geocode(self, address: str, city: str | None = None) -> ApiResult:
        params: dict[str, Any] = {"address": address, "output": "JSON"}
        if city:
            params["city"] = city
        return self.get("geocode/geo", params)

    def driving(self, origin: str, destination: str) -> ApiResult:
        return self.get(
            "direction/driving",
            {"origin": origin, "destination": destination, "strategy": 0, "extensions": "base"},
        )

    def transit(
        self,
        origin: str,
        destination: str,
        *,
        origin_city: str,
        destination_city: str,
        sample_date: str,
        sample_time: str,
    ) -> ApiResult:
        return self.get(
            "direction/transit/integrated",
            {
                "origin": origin,
                "destination": destination,
                "city": origin_city,
                "cityd": destination_city,
                "strategy": 0,
                "nightflag": 0,
                "date": sample_date,
                "time": sample_time,
            },
        )

    def _safe_text(self, value: Any) -> str:
        return str(value or "").replace(self.api_key, "[REDACTED]")

    @staticmethod
    def _has_usable_payload(endpoint: str, data: dict[str, Any]) -> bool:
        if endpoint == "geocode/geo":
            return bool(data.get("geocodes"))
        route = data.get("route")
        if not isinstance(route, dict):
            return False
        if endpoint == "direction/driving":
            return bool(route.get("paths"))
        if endpoint == "direction/transit/integrated":
            return bool(route.get("transits"))
        return True

    def _raw_status(
        self,
        data: dict[str, Any],
        *,
        cache_hit: bool,
        attempts: int,
        http_status: int | None = 200,
        observed_at: str | None = None,
    ) -> dict[str, Any]:
        return {
            "status": self._safe_text(data.get("status")),
            "info": self._safe_text(data.get("info")),
            "infocode": self._safe_text(data.get("infocode")),
            "http_status": http_status,
            "cache_hit": cache_hit,
            "attempts": attempts,
            "observed_at": observed_at or utc_now(),
        }


def _number(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number < 0:
        return None
    return round(number)


def _minutes(seconds: int | float | None) -> int | None:
    if seconds is None:
        return None
    return round(float(seconds) / 60)


def _stats_minutes(seconds_values: Iterable[int]) -> tuple[int | None, int | None, int | None]:
    values = sorted(value for value in seconds_values if value is not None and value >= 0)
    if not values:
        return None, None, None
    return _minutes(values[0]), _minutes(statistics.median(values)), _minutes(values[-1])


def _checked_at_from_raw_status(raw_status: Any) -> str:
    observed: list[str] = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            timestamp = value.get("observed_at")
            if isinstance(timestamp, str) and timestamp:
                observed.append(timestamp)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(raw_status)
    # 聚合记录以最老的组成样本为事实新鲜度，避免把旧缓存伪装成本次实时查询。
    return min(observed) if observed else utc_now()


def contains_walk_type_30(node: Any) -> bool:
    """递归扫描任意深度的 walking steps，兼容 walk_type 为数字或字符串。"""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "walk_type" and str(value).strip() == "30":
                return True
            if contains_walk_type_30(value):
                return True
    elif isinstance(node, list):
        return any(contains_walk_type_30(item) for item in node)
    return False


def _stop_name(value: Any) -> str | None:
    if isinstance(value, dict):
        return str(value.get("name") or "").strip() or None
    return None


def extract_railway_segments(node: Any) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            railway = value.get("railway")
            if isinstance(railway, dict) and railway:
                duration = _number(railway.get("time") or railway.get("duration"))
                segments.append(
                    {
                        "trip": str(railway.get("trip") or "").strip() or None,
                        "type": str(railway.get("type") or "").strip() or None,
                        "duration_minutes": _minutes(duration),
                        "departure_stop": _stop_name(railway.get("departure_stop")),
                        "arrival_stop": _stop_name(railway.get("arrival_stop")),
                        "departure_time": (
                            str((railway.get("departure_stop") or {}).get("time") or "").strip() or None
                            if isinstance(railway.get("departure_stop"), dict)
                            else None
                        ),
                        "arrival_time": (
                            str((railway.get("arrival_stop") or {}).get("time") or "").strip() or None
                            if isinstance(railway.get("arrival_stop"), dict)
                            else None
                        ),
                        "_duration_seconds": duration,
                    }
                )
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(node)
    return segments


def _valid_routes(data: dict[str, Any] | None, key: str) -> list[dict[str, Any]]:
    if not isinstance(data, dict):
        return []
    route = data.get("route")
    candidates = route.get(key) if isinstance(route, dict) else None
    if not isinstance(candidates, list):
        return []
    return [candidate for candidate in candidates if isinstance(candidate, dict) and _number(candidate.get("duration"))]


def parse_driving_result(result: ApiResult) -> tuple[dict[str, Any] | None, str | None]:
    routes = _valid_routes(result.data, "paths")
    if not routes:
        return None, result.failure_reason or "amap_no_driving_route"
    selected = min(routes, key=lambda route: _number(route.get("duration")) or sys.maxsize)
    return {
        "duration_seconds": _number(selected.get("duration")),
        "distance_m": _number(selected.get("distance")),
        "contains_ferry": contains_walk_type_30(selected),
        "route_option_count": len(routes),
    }, None


def parse_transit_result(result: ApiResult) -> tuple[dict[str, Any] | None, str | None]:
    routes = _valid_routes(result.data, "transits")
    if not routes:
        return None, result.failure_reason or "amap_no_transit_route"
    selected = min(routes, key=lambda route: _number(route.get("duration")) or sys.maxsize)
    railway_segments = extract_railway_segments(selected)
    return {
        "duration_seconds": _number(selected.get("duration")),
        "distance_m": _number(selected.get("distance")),
        "contains_ferry": contains_walk_type_30(selected),
        "route_option_count": len(routes),
        "railway_seconds": sum(segment.get("_duration_seconds") or 0 for segment in railway_segments) or None,
        "railway_segments": [
            {key: value for key, value in segment.items() if key != "_duration_seconds"}
            for segment in railway_segments
        ],
    }, None


def resolve_location(
    client: AmapApiClient,
    *,
    address: str,
    city: str,
    longitude: Any = None,
    latitude: Any = None,
    adcode: str | None = None,
) -> dict[str, Any]:
    lng = _number(longitude)
    lat = _number(latitude)
    # 经纬度需要保留小数，不能复用整数化的 _number。
    try:
        lng_float = float(longitude) if longitude is not None else None
        lat_float = float(latitude) if latitude is not None else None
    except (TypeError, ValueError):
        lng_float = lat_float = None
    if lng is not None and lat is not None and lng_float is not None and lat_float is not None:
        if 73 <= lng_float <= 135 and 3 <= lat_float <= 54:
            return {
                "location": f"{lng_float:.6f},{lat_float:.6f}",
                "city": city,
                "adcode": adcode,
                "source": "entity_coordinates",
                "raw_status": {
                    "status": "LOCAL",
                    "info": "entity_coordinates",
                    "infocode": "",
                    "cache_hit": True,
                    "observed_at": utc_now(),
                },
                "failure_reason": None,
            }

    result = client.geocode(address, city)
    geocodes = result.data.get("geocodes") if isinstance(result.data, dict) else None
    if isinstance(geocodes, list) and geocodes and isinstance(geocodes[0], dict):
        geocode = geocodes[0]
        location = str(geocode.get("location") or "").strip()
        if location:
            resolved_city = geocode.get("city") or geocode.get("province") or city
            return {
                "location": location,
                "city": resolved_city if isinstance(resolved_city, str) else city,
                "adcode": str(geocode.get("adcode") or adcode or "").strip() or None,
                "source": "amap_geocode",
                "raw_status": result.raw_status,
                "failure_reason": None,
            }
    return {
        "location": None,
        "city": city,
        "adcode": adcode,
        "source": "amap_geocode",
        "raw_status": result.raw_status,
        "failure_reason": result.failure_reason or "amap_geocode_no_result",
    }


def load_ferry_overrides(path: Path) -> tuple[set[str], set[str]]:
    if not path.exists():
        return set(), set()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Cannot parse ferry override file: {path}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"Invalid ferry override file: {path}")

    def keys_or_values(value: Any) -> set[str]:
        if isinstance(value, dict):
            return {str(item).strip() for item in value if str(item).strip()}
        if isinstance(value, list):
            return {str(item).strip() for item in value if str(item).strip()}
        return set()

    return keys_or_values(payload.get("destination_ids")), keys_or_values(payload.get("destination_names"))


def _ferry_fields(api_detected: bool, manual_fallback: bool) -> tuple[bool, list[str], str]:
    sources: list[str] = []
    if api_detected:
        sources.append("amap_walk_type_30")
    if manual_fallback:
        sources.append("manual_override")
    requires_ferry = bool(sources)
    return requires_ferry, sources, FERRY_NOTE if requires_ferry else ""


def _base_row(
    destination: dict[str, Any],
    origin_city: str,
    origin_spec: dict[str, str],
    mode: str,
    *,
    source: str,
) -> dict[str, Any]:
    return {
        "destination_id": destination["entity_id"],
        "destination_name": destination.get("name", ""),
        "origin_city": origin_city,
        "origin_name": origin_spec["label"],
        "origin_type": origin_spec["type"],
        "transport_mode": mode,
        "travel_minutes": None,
        "door_to_door_min": None,
        "door_to_door_typical": None,
        "door_to_door_max": None,
        "rail_segment_min": None,
        "rail_segment_typical": None,
        "rail_segment_max": None,
        "access_egress_min": None,
        "access_egress_typical": None,
        "access_egress_max": None,
        "distance_m": None,
        "contains_ferry": False,
        "requires_ferry": False,
        "ferry_detection_sources": [],
        "railway_segments": [],
        "source": source,
        "confidence": "低",
        "route_estimate": True,
        "route_sample_count": 0,
        "planned_sample_count": 0,
        "sample_dates": [],
        "sample_times": [],
        "failure_reason": None,
        "raw_status": {},
        "note": "",
        "checked_at": utc_now(),
    }


def build_failure_row(
    destination: dict[str, Any],
    origin_city: str,
    origin_spec: dict[str, str],
    mode: str,
    *,
    source: str,
    failure_reason: str,
    raw_status: dict[str, Any],
    manual_ferry: bool,
    sample_dates: list[str] | None = None,
    sample_times: list[str] | None = None,
) -> dict[str, Any]:
    row = _base_row(destination, origin_city, origin_spec, mode, source=source)
    requires_ferry, detection_sources, ferry_note = _ferry_fields(False, manual_ferry)
    row.update(
        {
            "requires_ferry": requires_ferry,
            "ferry_detection_sources": detection_sources,
            "planned_sample_count": max(1, len(sample_dates or []) * len(sample_times or [])),
            "sample_dates": sample_dates or [],
            "sample_times": sample_times or [],
            "failure_reason": failure_reason,
            "raw_status": raw_status,
            "note": ferry_note,
            "checked_at": _checked_at_from_raw_status(raw_status),
        }
    )
    return row


def build_driving_row(
    client: AmapApiClient,
    destination: dict[str, Any],
    origin_city: str,
    origin_spec: dict[str, str],
    origin_location: str,
    destination_location: str,
    *,
    manual_ferry: bool,
) -> dict[str, Any]:
    source = "高德地图 Web 服务 API / 驾车路径规划"
    result = client.driving(origin_location, destination_location)
    route, failure = parse_driving_result(result)
    if not route:
        return build_failure_row(
            destination,
            origin_city,
            origin_spec,
            "自驾",
            source=source,
            failure_reason=failure or "unknown_driving_failure",
            raw_status={"success_count": 0, "failure_count": 1, "samples": [result.raw_status]},
            manual_ferry=manual_ferry,
        )

    requires_ferry, detection_sources, ferry_note = _ferry_fields(route["contains_ferry"], manual_ferry)
    duration = route["duration_seconds"]
    minutes = _minutes(duration)
    row = _base_row(destination, origin_city, origin_spec, "自驾", source=source)
    row.update(
        {
            "travel_minutes": minutes,
            "door_to_door_min": minutes,
            "door_to_door_typical": minutes,
            "door_to_door_max": minutes,
            "distance_m": route["distance_m"],
            "contains_ferry": bool(route["contains_ferry"]),
            "requires_ferry": requires_ferry,
            "ferry_detection_sources": detection_sources,
            "confidence": "低" if requires_ferry else "中",
            "route_sample_count": 1,
            "planned_sample_count": 1,
            "raw_status": {"success_count": 1, "failure_count": 0, "samples": [result.raw_status]},
            "note": ferry_note,
            "checked_at": _checked_at_from_raw_status(result.raw_status),
        }
    )
    return row


def _unique_railway_segments(samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for sample in samples:
        for segment in sample.get("railway_segments", []):
            key = (
                segment.get("trip"),
                segment.get("type"),
                segment.get("departure_stop"),
                segment.get("arrival_stop"),
                segment.get("duration_minutes"),
            )
            if key not in seen:
                seen.add(key)
                result.append(segment)
    return result


def build_transit_row(
    client: AmapApiClient,
    destination: dict[str, Any],
    origin_city: str,
    origin_spec: dict[str, str],
    origin_location: str,
    destination_location: str,
    *,
    origin_city_code: str,
    destination_city_code: str,
    sample_dates: list[str],
    sample_times: list[str],
    manual_ferry: bool,
) -> dict[str, Any]:
    source = "高德地图 Web 服务 API / 公交路径规划（路线估算）"
    successes: list[dict[str, Any]] = []
    sample_statuses: list[dict[str, Any]] = []
    failures: list[str] = []

    for sample_date in sample_dates:
        for sample_time in sample_times:
            result = client.transit(
                origin_location,
                destination_location,
                origin_city=origin_city_code,
                destination_city=destination_city_code,
                sample_date=sample_date,
                sample_time=sample_time,
            )
            route, failure = parse_transit_result(result)
            status = {"sample_date": sample_date, "sample_time": sample_time, **result.raw_status}
            if route:
                status["route_parsed"] = True
                route["sample_date"] = sample_date
                route["sample_time"] = sample_time
                successes.append(route)
            else:
                status["route_parsed"] = False
                failures.append(failure or "unknown_transit_failure")
            sample_statuses.append(status)

    raw_status = {
        "success_count": len(successes),
        "failure_count": len(sample_statuses) - len(successes),
        "samples": sample_statuses,
    }
    if not successes:
        failure_reason = ";".join(dict.fromkeys(failures)) or "all_transit_samples_failed"
        row = build_failure_row(
            destination,
            origin_city,
            origin_spec,
            "公共交通",
            source=source,
            failure_reason=failure_reason,
            raw_status=raw_status,
            manual_ferry=manual_ferry,
            sample_dates=sample_dates,
            sample_times=sample_times,
        )
        row["note"] = "；".join(item for item in (TRANSIT_ESTIMATE_NOTE, row["note"]) if item)
        return row

    door_values = [sample["duration_seconds"] for sample in successes if sample.get("duration_seconds") is not None]
    rail_values = [sample["railway_seconds"] for sample in successes if sample.get("railway_seconds") is not None]
    access_values = [
        sample["duration_seconds"] - sample["railway_seconds"]
        for sample in successes
        if sample.get("duration_seconds") is not None
        and sample.get("railway_seconds") is not None
        and sample["duration_seconds"] >= sample["railway_seconds"]
    ]
    distance_values = sorted(sample["distance_m"] for sample in successes if sample.get("distance_m") is not None)
    door_min, door_typical, door_max = _stats_minutes(door_values)
    rail_min, rail_typical, rail_max = _stats_minutes(rail_values)
    access_min, access_typical, access_max = _stats_minutes(access_values)
    api_ferry = any(sample.get("contains_ferry") for sample in successes)
    requires_ferry, detection_sources, ferry_note = _ferry_fields(api_ferry, manual_ferry)
    notes = [TRANSIT_ESTIMATE_NOTE]
    if rail_values:
        notes.append(RAIL_ESTIMATE_NOTE)
    if ferry_note:
        notes.append(ferry_note)

    row = _base_row(destination, origin_city, origin_spec, "公共交通", source=source)
    row.update(
        {
            "travel_minutes": door_typical,
            "door_to_door_min": door_min,
            "door_to_door_typical": door_typical,
            "door_to_door_max": door_max,
            "rail_segment_min": rail_min,
            "rail_segment_typical": rail_typical,
            "rail_segment_max": rail_max,
            "access_egress_min": access_min,
            "access_egress_typical": access_typical,
            "access_egress_max": access_max,
            "distance_m": round(statistics.median(distance_values)) if distance_values else None,
            "contains_ferry": api_ferry,
            "requires_ferry": requires_ferry,
            "ferry_detection_sources": detection_sources,
            "railway_segments": _unique_railway_segments(successes),
            "confidence": "低",
            "route_sample_count": len(successes),
            "planned_sample_count": len(sample_statuses),
            "sample_dates": sample_dates,
            "sample_times": sample_times,
            "failure_reason": None,
            "partial_failure_reasons": list(dict.fromkeys(failures)),
            "raw_status": raw_status,
            "note": "；".join(notes),
            "checked_at": _checked_at_from_raw_status(raw_status),
        }
    )
    return row


def row_key(row: dict[str, Any]) -> tuple[str, str, str]:
    return str(row.get("destination_id")), str(row.get("origin_city")), str(row.get("transport_mode"))


class TravelCheckpoint:
    def __init__(self, output: Path, failure_log: Path):
        self.output = output
        self.failure_log = failure_log
        self.rows = {row_key(row): row for row in load_jsonl(output)}

    def should_skip(self, key: tuple[str, str, str], *, retry_failures: bool) -> bool:
        row = self.rows.get(key)
        if not row or not REQUIRED_CHECKPOINT_FIELDS.issubset(row):
            return False
        if retry_failures and row.get("travel_minutes") is None:
            return False
        return True

    def upsert(self, row: dict[str, Any]) -> None:
        self.rows[row_key(row)] = row
        self.flush()

    def flush(self) -> None:
        ordered = [self.rows[key] for key in sorted(self.rows)]
        write_jsonl(self.output, ordered)
        write_jsonl(self.failure_log, [item for item in ordered if item.get("travel_minutes") is None])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build the Amap route-estimate travel matrix.")
    parser.add_argument("--entities", type=Path, default=DATA_DIR / "entities.jsonl")
    parser.add_argument("--facts", type=Path, default=DATA_DIR / "destination_facts.jsonl")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--failure-log", type=Path, default=DEFAULT_FAILURE_LOG)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--ferry-overrides", type=Path, default=DEFAULT_FERRY_OVERRIDES)
    parser.add_argument("--origins", nargs="+", type=validate_origin, metavar="ORIGIN", default=DEFAULT_ORIGINS)
    parser.add_argument("--limit", type=int, default=0, help="Process only the first N destinations; use --limit 5 for P0 QA.")
    parser.add_argument("--qps", type=float, default=2.5, help="Maximum Amap request rate; default: 2.5 QPS.")
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument("--retry-backoff", type=float, default=0.5)
    parser.add_argument("--retry-failures", action="store_true", help="Retry checkpoint rows whose travel_minutes is null.")
    parser.add_argument(
        "--reapply-overrides-only",
        action="store_true",
        help="Reapply ferry overrides to existing checkpoint rows without any API request.",
    )
    parser.add_argument("--sample-dates", nargs="+", type=validate_sample_date)
    parser.add_argument("--sample-times", nargs="+", type=validate_sample_time, default=DEFAULT_SAMPLE_TIMES)
    return parser


def _manual_ferry(
    destination: dict[str, Any],
    fact_by_id: dict[str, dict[str, Any]],
    override_ids: set[str],
    override_names: set[str],
) -> bool:
    destination_id = str(destination["entity_id"])
    return bool(
        destination_id in override_ids
        or str(destination.get("name") or "") in override_names
        or fact_by_id.get(destination_id, {}).get("requires_ferry")
    )


def _replace_ferry_note(note: Any, *, requires_ferry: bool) -> str:
    parts = [part.strip() for part in str(note or "").split("；") if part.strip()]
    parts = [part for part in parts if part != FERRY_NOTE]
    if requires_ferry:
        parts.append(FERRY_NOTE)
    return "；".join(parts)


def reapply_ferry_overrides(
    checkpoint: TravelCheckpoint,
    destinations: list[dict[str, Any]],
    fact_by_id: dict[str, dict[str, Any]],
    override_ids: set[str],
    override_names: set[str],
) -> dict[str, int]:
    """只修改 checkpoint 中的轮渡派生字段，不发网络请求、不刷新 API checked_at。"""
    destination_by_id = {str(row["entity_id"]): row for row in destinations}
    changed_destinations: set[str] = set()
    changed_rows = 0
    manual_override_rows = 0
    api_detected_rows = 0

    for key, existing in list(checkpoint.rows.items()):
        row = dict(existing)
        destination_id = str(row.get("destination_id") or "")
        destination = destination_by_id.get(
            destination_id,
            {"entity_id": destination_id, "name": row.get("destination_name") or ""},
        )
        manual_ferry = _manual_ferry(destination, fact_by_id, override_ids, override_names)
        api_detected = bool(row.get("contains_ferry")) or "amap_walk_type_30" in set(
            row.get("ferry_detection_sources") or []
        )
        requires_ferry, detection_sources, _ = _ferry_fields(api_detected, manual_ferry)
        if manual_ferry:
            manual_override_rows += 1
        if api_detected:
            api_detected_rows += 1

        row["contains_ferry"] = api_detected
        row["requires_ferry"] = requires_ferry
        row["ferry_detection_sources"] = detection_sources
        row["note"] = _replace_ferry_note(row.get("note"), requires_ferry=requires_ferry)
        if row.get("transport_mode") == "自驾":
            row["confidence"] = "低" if requires_ferry or row.get("travel_minutes") is None else "中"
        else:
            row["confidence"] = "低"

        if row != existing:
            checkpoint.rows[key] = row
            changed_rows += 1
            changed_destinations.add(destination_id)

    checkpoint.flush()
    return {
        "rows_scanned": len(checkpoint.rows),
        "rows_changed": changed_rows,
        "destinations_changed": len(changed_destinations),
        "manual_override_rows": manual_override_rows,
        "api_detected_rows": api_detected_rows,
        "failure_rows": sum(1 for row in checkpoint.rows.values() if row.get("travel_minutes") is None),
    }


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    sample_dates = args.sample_dates or representative_sample_dates()
    checkpoint = TravelCheckpoint(args.output, args.failure_log)
    destinations = [row for row in load_jsonl(args.entities) if row.get("entity_type") == "destination"]
    fact_by_id = {row["destination_id"]: row for row in load_jsonl(args.facts)}
    override_ids, override_names = load_ferry_overrides(args.ferry_overrides)
    if args.reapply_overrides_only:
        stats = reapply_ferry_overrides(
            checkpoint,
            destinations,
            fact_by_id,
            override_ids,
            override_names,
        )
        print("override reapply " + " ".join(f"{key}={value}" for key, value in stats.items()))
        return 0

    api_key = load_amap_key()
    request_cache = PersistentJsonCache(args.cache, flush_every=8)
    client = AmapApiClient(
        api_key,
        request_cache,
        qps=args.qps,
        max_retries=args.max_retries,
        retry_backoff=args.retry_backoff,
    )
    if args.limit > 0:
        destinations = destinations[: args.limit]
    destination_locations: dict[str, dict[str, Any]] = {}
    written = skipped = 0

    for origin_city in args.origins:
        origin_spec = ORIGIN_SPECS[origin_city]
        pending_destinations = [
            destination
            for destination in destinations
            if any(
                not checkpoint.should_skip(
                    (destination["entity_id"], origin_city, mode),
                    retry_failures=args.retry_failures,
                )
                for mode in ("自驾", "公共交通")
            )
        ]
        skipped += (len(destinations) - len(pending_destinations)) * 2
        if not pending_destinations:
            continue

        origin = resolve_location(
            client,
            address=origin_spec["address"],
            city=origin_spec["city"],
        )
        if not origin["location"]:
            for destination in pending_destinations:
                manual_ferry = _manual_ferry(destination, fact_by_id, override_ids, override_names)
                for mode in ("自驾", "公共交通"):
                    key = (destination["entity_id"], origin_city, mode)
                    if checkpoint.should_skip(key, retry_failures=args.retry_failures):
                        skipped += 1
                        continue
                    source = (
                        "高德地图 Web 服务 API / 驾车路径规划"
                        if mode == "自驾"
                        else "高德地图 Web 服务 API / 公交路径规划（路线估算）"
                    )
                    row = build_failure_row(
                        destination,
                        origin_city,
                        origin_spec,
                        mode,
                        source=source,
                        failure_reason=f"origin_geocode_failed:{origin['failure_reason']}",
                        raw_status={"success_count": 0, "failure_count": 1, "samples": [origin["raw_status"]]},
                        manual_ferry=manual_ferry,
                        sample_dates=sample_dates if mode == "公共交通" else None,
                        sample_times=args.sample_times if mode == "公共交通" else None,
                    )
                    checkpoint.upsert(row)
                    written += 1
            continue

        for index, destination in enumerate(pending_destinations, 1):
            destination_id = destination["entity_id"]
            if destination_id not in destination_locations:
                destination_locations[destination_id] = resolve_location(
                    client,
                    address=destination["name"],
                    city=destination.get("city") or destination.get("province") or "",
                    longitude=destination.get("longitude"),
                    latitude=destination.get("latitude"),
                    adcode=destination.get("adcode"),
                )
            destination_location = destination_locations[destination_id]
            manual_ferry = _manual_ferry(destination, fact_by_id, override_ids, override_names)
            for mode in ("自驾", "公共交通"):
                key = (destination_id, origin_city, mode)
                if checkpoint.should_skip(key, retry_failures=args.retry_failures):
                    skipped += 1
                    continue
                if not destination_location["location"]:
                    source = (
                        "高德地图 Web 服务 API / 驾车路径规划"
                        if mode == "自驾"
                        else "高德地图 Web 服务 API / 公交路径规划（路线估算）"
                    )
                    row = build_failure_row(
                        destination,
                        origin_city,
                        origin_spec,
                        mode,
                        source=source,
                        failure_reason=f"destination_geocode_failed:{destination_location['failure_reason']}",
                        raw_status={
                            "success_count": 0,
                            "failure_count": 1,
                            "samples": [destination_location["raw_status"]],
                        },
                        manual_ferry=manual_ferry,
                        sample_dates=sample_dates if mode == "公共交通" else None,
                        sample_times=args.sample_times if mode == "公共交通" else None,
                    )
                elif mode == "自驾":
                    row = build_driving_row(
                        client,
                        destination,
                        origin_city,
                        origin_spec,
                        origin["location"],
                        destination_location["location"],
                        manual_ferry=manual_ferry,
                    )
                else:
                    row = build_transit_row(
                        client,
                        destination,
                        origin_city,
                        origin_spec,
                        origin["location"],
                        destination_location["location"],
                        origin_city_code=origin.get("adcode") or origin.get("city") or origin_city,
                        destination_city_code=(
                            destination_location.get("adcode")
                            or destination_location.get("city")
                            or destination.get("city")
                            or ""
                        ),
                        sample_dates=sample_dates,
                        sample_times=args.sample_times,
                        manual_ferry=manual_ferry,
                    )
                checkpoint.upsert(row)
                written += 1
                outcome = f"{row['travel_minutes']}min" if row["travel_minutes"] is not None else row["failure_reason"]
                origin_code = {"上海": "SH", "杭州": "HZ", "苏州": "SZ"}[origin_city]
                mode_code = "driving" if mode == "自驾" else "transit"
                print(
                    f"[{origin_code} {index}/{len(pending_destinations)}] "
                    f"{destination_id} {mode_code}: {outcome}"
                )

    request_cache.flush()
    print(
        f"travel rows={len(checkpoint.rows)} written={written} skipped={skipped} "
        f"failures={sum(1 for row in checkpoint.rows.values() if row.get('travel_minutes') is None)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
