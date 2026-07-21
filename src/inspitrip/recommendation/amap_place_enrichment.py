from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Callable, Iterable

import requests
from dotenv import dotenv_values

from inspitrip.paths import DEFAULT_ENV_PATH, REPO_ROOT

ROOT = REPO_ROOT
UNKNOWN = "unknown"
PLACE_ENDPOINT = "https://restapi.amap.com/v3/place/text"
GEOCODE_ENDPOINT = "https://restapi.amap.com/v3/geocode/geo"
TERMINAL_STATUSES = {
    "matched",
    "region_geocoded",
    "review_required",
    "no_match",
    "manual_override",
    "excluded",
}


class AmapApiError(RuntimeError):
    """A sanitized API error that never contains the request key."""

    def __init__(self, message: str, *, code: str = "unknown", retriable: bool = True) -> None:
        super().__init__(message)
        self.code = code
        self.retriable = retriable


class CacheMissError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_amap_key(env_path: Path = DEFAULT_ENV_PATH) -> str:
    """Read AMAP_KEY only from the repository .env, never from process env."""
    key = str(dotenv_values(env_path).get("AMAP_KEY") or "").strip()
    if not key:
        raise RuntimeError(f"{env_path} 未配置 AMAP_KEY")
    return key


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def write_jsonl_atomic(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with temp_path.open("w", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    temp_path.replace(path)


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temp_path.replace(path)


def load_overrides(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    destinations = payload.get("destinations", payload)
    if not isinstance(destinations, dict):
        raise ValueError("地图覆盖文件的 destinations 必须是对象")
    return {
        str(destination_id): value
        for destination_id, value in destinations.items()
        if not str(destination_id).startswith("_") and isinstance(value, dict)
    }


def _text(value: Any) -> str:
    if isinstance(value, list):
        value = value[0] if value else ""
    return str(value or "").strip()


def _unknown(value: Any) -> str:
    text = _text(value)
    return text if text else UNKNOWN


def normalize_name(value: Any) -> str:
    text = unicodedata.normalize("NFKC", _text(value)).lower()
    return re.sub(r"[\s·•・—_\-（）()【】\[\]，,。./\\]+", "", text)


def normalize_admin(value: Any) -> str:
    text = normalize_name(value)
    return re.sub(r"(特别行政区|自治区|自治州|地区|省|市|区|县)$", "", text)


def _parse_location(value: Any) -> tuple[float, float] | None:
    text = _text(value)
    if not text or "," not in text:
        return None
    try:
        longitude, latitude = (float(part) for part in text.split(",", 1))
    except (TypeError, ValueError):
        return None
    if not (-180 <= longitude <= 180 and -90 <= latitude <= 90):
        return None
    return longitude, latitude


def _cache_digest(endpoint: str, params: dict[str, Any]) -> str:
    canonical = json.dumps(
        {"endpoint": endpoint, "params": params},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass
class RateLimiter:
    qps: float = 2.0
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic

    def __post_init__(self) -> None:
        self._lock = threading.Lock()
        self._last_call = 0.0

    def wait(self) -> None:
        interval = 1.0 / max(self.qps, 0.01)
        with self._lock:
            now = self.clock()
            delay = self._last_call + interval - now
            if delay > 0:
                self.sleep(delay)
                now = self.clock()
            self._last_call = now


class AmapPlaceClient:
    def __init__(
        self,
        api_key: str | None,
        cache_dir: Path,
        *,
        qps: float = 2.0,
        max_retries: int = 3,
        timeout_seconds: float = 15.0,
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
        offline: bool = False,
    ) -> None:
        if not api_key and not offline:
            raise RuntimeError("在线高德 Place 补全需要 AMAP_KEY")
        self.api_key = api_key or ""
        self.cache_dir = cache_dir
        self.max_retries = max(1, max_retries)
        self.timeout_seconds = timeout_seconds
        self.session = session or requests.Session()
        self.sleep = sleep
        self.offline = offline
        self.rate_limiter = RateLimiter(qps=qps, sleep=sleep)

    def _request_json(self, endpoint: str, params: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        cache_key = _cache_digest(endpoint, params)
        cache_path = self.cache_dir / f"{cache_key}.json"
        if cache_path.exists():
            payload = json.loads(cache_path.read_text(encoding="utf-8"))
            return dict(payload.get("response") or {}), True
        if self.offline:
            raise CacheMissError("离线模式下没有对应缓存")

        last_error = "高德 API 请求失败"
        for attempt in range(self.max_retries):
            try:
                self.rate_limiter.wait()
                response = self.session.get(
                    endpoint,
                    params={**params, "key": self.api_key},
                    timeout=self.timeout_seconds,
                )
                response.raise_for_status()
                data = response.json()
                if str(data.get("status")) != "1":
                    info = _text(data.get("info")) or "API_STATUS_0"
                    infocode = _text(data.get("infocode")) or "unknown"
                    # Configuration, authorization and quota failures are not healed by retrying.
                    non_retriable = infocode in {"10001", "10002", "10003", "10005", "10006", "10007"}
                    raise AmapApiError(
                        f"高德 API 返回失败: {info} ({infocode})",
                        code=infocode,
                        retriable=not non_retriable,
                    )
                self.cache_dir.mkdir(parents=True, exist_ok=True)
                cache_payload = {
                    "endpoint": endpoint,
                    "params": params,
                    "cached_at": utc_now(),
                    "response": data,
                }
                temp_path = cache_path.with_suffix(".json.tmp")
                temp_path.write_text(
                    json.dumps(cache_payload, ensure_ascii=False, separators=(",", ":")),
                    encoding="utf-8",
                )
                temp_path.replace(cache_path)
                return data, False
            except (requests.RequestException, ValueError, AmapApiError) as exc:
                last_error = str(exc)
                if isinstance(exc, AmapApiError) and not exc.retriable:
                    break
                if attempt + 1 < self.max_retries:
                    self.sleep(min(0.5 * (2**attempt), 4.0))
        raise AmapApiError(last_error)

    def search_place(self, keyword: str, city: str) -> tuple[list[dict[str, Any]], bool]:
        data, cache_hit = self._request_json(
            PLACE_ENDPOINT,
            {
                "keywords": keyword,
                "city": city,
                "citylimit": "true",
                "extensions": "all",
                "offset": 20,
                "page": 1,
                "output": "JSON",
            },
        )
        pois = data.get("pois") or []
        return [dict(item) for item in pois if isinstance(item, dict)], cache_hit

    def geocode(self, address: str, city: str) -> tuple[list[dict[str, Any]], bool]:
        data, cache_hit = self._request_json(
            GEOCODE_ENDPOINT,
            {"address": address, "city": city, "output": "JSON"},
        )
        rows = data.get("geocodes") or []
        return [dict(item) for item in rows if isinstance(item, dict)], cache_hit


def _candidate_from_poi(raw: dict[str, Any]) -> dict[str, Any]:
    biz_ext = raw.get("biz_ext") if isinstance(raw.get("biz_ext"), dict) else {}
    location = _parse_location(raw.get("location"))
    return {
        "map_poi_id": _text(raw.get("id")) or None,
        "name": _text(raw.get("name")),
        "longitude": location[0] if location else None,
        "latitude": location[1] if location else None,
        "standard_province": _unknown(raw.get("pname")),
        "standard_city": _unknown(raw.get("cityname")),
        "standard_district": _unknown(raw.get("adname")),
        "adcode": _unknown(raw.get("adcode")),
        "address": _unknown(raw.get("address")),
        "poi_type": _unknown(raw.get("type")),
        "poi_typecode": _unknown(raw.get("typecode")),
        "telephone": _unknown(raw.get("tel")),
        "business_area": _unknown(raw.get("business_area")),
        "opening_hours": _unknown(biz_ext.get("open_time")),
        # POI existence does not prove current operation; keep it unknown.
        "operational_status": UNKNOWN,
    }


def score_candidate(
    entity: dict[str, Any],
    candidate: dict[str, Any],
    override: dict[str, Any] | None = None,
) -> tuple[float, list[str]]:
    override = override or {}
    expected_names = [entity.get("name"), *(entity.get("aliases") or []), *(override.get("query_terms") or [])]
    expected_names = [normalize_name(value) for value in expected_names if normalize_name(value)]
    candidate_name = normalize_name(candidate.get("name"))
    ratios = [SequenceMatcher(None, expected, candidate_name).ratio() for expected in expected_names]
    best_ratio = max(ratios, default=0.0)
    exact = candidate_name in set(expected_names)
    contains = any(expected in candidate_name or candidate_name in expected for expected in expected_names)
    name_score = 1.0 if exact else max(0.84 if contains else 0.0, best_ratio)

    expected_city = normalize_admin(override.get("expected_city") or entity.get("city"))
    expected_province = normalize_admin(override.get("expected_province") or entity.get("province"))
    candidate_city = normalize_admin(candidate.get("standard_city"))
    candidate_district = normalize_admin(candidate.get("standard_district"))
    candidate_province = normalize_admin(candidate.get("standard_province"))
    city_match = bool(expected_city and candidate_city and expected_city == candidate_city)
    district_match = bool(
        expected_city and candidate_district and expected_city == candidate_district
    )
    locality_match = city_match or district_match
    province_match = bool(expected_province and candidate_province and expected_province == candidate_province)
    city_mismatch = bool(
        expected_city
        and (candidate_city or candidate_district)
        and not locality_match
    )
    province_mismatch = bool(expected_province and candidate_province and expected_province != candidate_province)
    has_location = candidate.get("longitude") is not None and candidate.get("latitude") is not None

    score = 0.68 * name_score
    score += 0.18 if locality_match else 0.0
    score += 0.09 if province_match else 0.0
    score += 0.05 if has_location else 0.0
    if city_mismatch:
        score -= 0.28
    if province_mismatch:
        score -= 0.35
    score = max(0.0, min(1.0, score))

    reasons: list[str] = []
    if exact:
        reasons.append("name_exact")
    elif contains:
        reasons.append("name_contains")
    else:
        reasons.append(f"name_similarity={best_ratio:.2f}")
    if city_match:
        reasons.append("city_match")
    elif district_match:
        reasons.append("district_match")
    elif city_mismatch:
        reasons.append("city_mismatch")
    if province_match:
        reasons.append("province_match")
    elif province_mismatch:
        reasons.append("province_mismatch")
    if has_location:
        reasons.append("has_location")
    reasons.append(
        "poi_type_match" if _candidate_type_matches(entity, candidate) else "poi_type_mismatch"
    )
    return round(score, 4), reasons


def _candidate_type_matches(entity: dict[str, Any], candidate: dict[str, Any]) -> bool:
    """Accept only destination-level POI types, with stricter village/town semantics."""
    codes = {
        part.strip()
        for part in _text(candidate.get("poi_typecode")).split("|")
        if part.strip() and part.strip() != UNKNOWN
    }
    if not codes:
        return False

    name = _text(entity.get("name"))
    if name.endswith(("村", "渔村")):
        return any(code.startswith("11") or code in {"190108", "130106"} for code in codes)
    if name.endswith("镇"):
        return any(code.startswith("11") or code in {"190107", "130105"} for code in codes)
    if "草原" in name:
        return any(code.startswith(("08", "11", "1902")) for code in codes)
    return any(code.startswith(("11", "1902")) for code in codes)


def _name_is_high_confidence(reasons: list[str]) -> bool:
    return "name_exact" in reasons or "name_contains" in reasons


def _record_fingerprint(entity: dict[str, Any], override: dict[str, Any]) -> str:
    payload = {
        "entity_id": entity.get("entity_id"),
        "name": entity.get("name"),
        "aliases": entity.get("aliases") or [],
        "city": entity.get("city"),
        "province": entity.get("province"),
        "override": override,
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:16]


def _base_record(entity: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    return {
        "destination_id": entity["entity_id"],
        "name": entity.get("name", ""),
        "input_city": entity.get("city", ""),
        "input_province": entity.get("province", ""),
        "inventory_review": override.get("inventory_review", "review_required"),
        "binding_policy": override.get("binding_policy", "auto"),
        "geocode_status": "pending",
        "map_review_status": "review_required",
        "coordinates": None,
        "map_poi_id": None,
        "standard_admin": {
            "province": UNKNOWN,
            "city": UNKNOWN,
            "district": UNKNOWN,
            "adcode": UNKNOWN,
        },
        "address": UNKNOWN,
        "basic_operations": {
            "telephone": UNKNOWN,
            "business_area": UNKNOWN,
            "opening_hours": UNKNOWN,
            "operational_status": UNKNOWN,
        },
        "poi_type": UNKNOWN,
        "poi_typecode": UNKNOWN,
        "match_confidence": 0.0,
        "match_level": UNKNOWN,
        "match_reasons": [],
        "review_candidate": None,
        "failure_reason": None,
        "source": UNKNOWN,
        "checked_at": None,
        "cache_hit": False,
        "input_fingerprint": _record_fingerprint(entity, override),
    }


def _apply_candidate(
    record: dict[str, Any],
    candidate: dict[str, Any],
    *,
    score: float,
    reasons: list[str],
    status: str,
    review_status: str,
    source: str,
    checked_at: str,
    bind_poi: bool,
) -> dict[str, Any]:
    result = dict(record)
    result.update(
        {
            "geocode_status": status,
            "map_review_status": review_status,
            "coordinates": {
                "longitude": candidate.get("longitude"),
                "latitude": candidate.get("latitude"),
            }
            if candidate.get("longitude") is not None and candidate.get("latitude") is not None
            else None,
            "map_poi_id": candidate.get("map_poi_id") if bind_poi else None,
            "standard_admin": {
                "province": candidate.get("standard_province", UNKNOWN),
                "city": candidate.get("standard_city", UNKNOWN),
                "district": candidate.get("standard_district", UNKNOWN),
                "adcode": candidate.get("adcode", UNKNOWN),
            },
            "address": candidate.get("address", UNKNOWN),
            "basic_operations": {
                "telephone": candidate.get("telephone", UNKNOWN),
                "business_area": candidate.get("business_area", UNKNOWN),
                "opening_hours": candidate.get("opening_hours", UNKNOWN),
                "operational_status": candidate.get("operational_status", UNKNOWN),
            },
            "poi_type": candidate.get("poi_type", UNKNOWN),
            "poi_typecode": candidate.get("poi_typecode", UNKNOWN),
            "match_confidence": score,
            "match_level": "high" if score >= 0.88 else "medium" if score >= 0.7 else "low",
            "match_reasons": reasons,
            "source": source,
            "checked_at": checked_at,
            "failure_reason": None,
        }
    )
    return result


def _sanitize_review_candidate(candidate: dict[str, Any], score: float, reasons: list[str]) -> dict[str, Any]:
    return {
        "map_poi_id": candidate.get("map_poi_id"),
        "name": candidate.get("name"),
        "coordinates": {
            "longitude": candidate.get("longitude"),
            "latitude": candidate.get("latitude"),
        }
        if candidate.get("longitude") is not None and candidate.get("latitude") is not None
        else None,
        "standard_province": candidate.get("standard_province", UNKNOWN),
        "standard_city": candidate.get("standard_city", UNKNOWN),
        "standard_district": candidate.get("standard_district", UNKNOWN),
        "adcode": candidate.get("adcode", UNKNOWN),
        "address": candidate.get("address", UNKNOWN),
        "poi_type": candidate.get("poi_type", UNKNOWN),
        "poi_typecode": candidate.get("poi_typecode", UNKNOWN),
        "match_confidence": score,
        "match_reasons": reasons,
    }


def _manual_candidate(manual: dict[str, Any]) -> dict[str, Any]:
    longitude = manual.get("longitude")
    latitude = manual.get("latitude")
    if longitude is not None and latitude is not None:
        longitude = float(longitude)
        latitude = float(latitude)
        if not (-180 <= longitude <= 180 and -90 <= latitude <= 90):
            raise ValueError("manual_match 坐标超出范围")
    return {
        "map_poi_id": manual.get("map_poi_id"),
        "name": manual.get("name", ""),
        "longitude": longitude,
        "latitude": latitude,
        "standard_province": _unknown(manual.get("standard_province")),
        "standard_city": _unknown(manual.get("standard_city")),
        "standard_district": _unknown(manual.get("standard_district")),
        "adcode": _unknown(manual.get("adcode")),
        "address": _unknown(manual.get("address")),
        "poi_type": _unknown(manual.get("poi_type")),
        "poi_typecode": _unknown(manual.get("poi_typecode")),
        "telephone": _unknown(manual.get("telephone")),
        "business_area": _unknown(manual.get("business_area")),
        "opening_hours": _unknown(manual.get("opening_hours")),
        "operational_status": _unknown(manual.get("operational_status")),
    }


def _candidate_from_geocode(raw: dict[str, Any]) -> dict[str, Any]:
    location = _parse_location(raw.get("location"))
    return {
        "map_poi_id": None,
        "name": _text(raw.get("formatted_address")),
        "longitude": location[0] if location else None,
        "latitude": location[1] if location else None,
        "standard_province": _unknown(raw.get("province")),
        "standard_city": _unknown(raw.get("city")),
        "standard_district": _unknown(raw.get("district")),
        "adcode": _unknown(raw.get("adcode")),
        "address": _unknown(raw.get("formatted_address")),
        "poi_type": "administrative_geocode",
        "poi_typecode": UNKNOWN,
        "telephone": UNKNOWN,
        "business_area": UNKNOWN,
        "opening_hours": UNKNOWN,
        "operational_status": UNKNOWN,
    }


def _admin_matches(entity: dict[str, Any], candidate: dict[str, Any], override: dict[str, Any]) -> bool:
    expected_city = normalize_admin(override.get("expected_city") or entity.get("city"))
    expected_province = normalize_admin(override.get("expected_province") or entity.get("province"))
    candidate_city = normalize_admin(candidate.get("standard_city"))
    candidate_district = normalize_admin(candidate.get("standard_district"))
    candidate_province = normalize_admin(candidate.get("standard_province"))
    city_ok = not expected_city or expected_city in {candidate_city, candidate_district}
    province_ok = not expected_province or expected_province == candidate_province
    return city_ok and province_ok


def enrich_destination(
    entity: dict[str, Any],
    override: dict[str, Any],
    client: AmapPlaceClient,
    *,
    checked_at: str | None = None,
) -> dict[str, Any]:
    checked_at = checked_at or utc_now()
    record = _base_record(entity, override)
    policy = override.get("binding_policy", "auto")
    inventory_review = override.get("inventory_review", "review_required")

    if policy == "exclude" or inventory_review == "excluded":
        record.update(
            {
                "geocode_status": "excluded",
                "map_review_status": "excluded",
                "failure_reason": override.get("review_note") or "人工库存审核排除",
                "checked_at": checked_at,
                "source": "manual_inventory_review",
            }
        )
        return record

    manual = override.get("manual_match")
    if isinstance(manual, dict):
        candidate = _manual_candidate(manual)
        if candidate.get("longitude") is None or candidate.get("latitude") is None:
            raise ValueError(f"{entity['entity_id']} manual_match 必须提供合法坐标")
        if policy != "region" and not candidate.get("map_poi_id"):
            raise ValueError(f"{entity['entity_id']} 非区域 manual_match 必须提供 map_poi_id")
        source = _text(manual.get("source"))
        manual_checked_at = _text(manual.get("checked_at"))
        if not source or not manual_checked_at:
            raise ValueError(f"{entity['entity_id']} manual_match 必须提供 source 和 checked_at")
        return _apply_candidate(
            record,
            candidate,
            score=1.0,
            reasons=["manual_override"],
            status="manual_override",
            review_status="manual_approved",
            source=source,
            checked_at=manual_checked_at,
            bind_poi=policy != "region",
        )

    query_terms = [*(override.get("query_terms") or []), entity.get("name"), *(entity.get("aliases") or [])]
    queries: list[str] = []
    for term in query_terms:
        text = _text(term)
        if text and text not in queries:
            queries.append(text)
    city = _text(override.get("expected_city") or entity.get("city"))

    # Region-like destinations use administrative geocoding first and never bind a POI.
    if policy == "region":
        region_query = queries[0] if queries else _text(entity.get("name"))
        try:
            geocodes, cache_hit = client.geocode(region_query, city)
            record["cache_hit"] = cache_hit
        except CacheMissError as exc:
            record.update({"geocode_status": "pending", "failure_reason": str(exc), "source": "cache_only"})
            return record
        except AmapApiError as exc:
            record.update(
                {
                    "geocode_status": "api_failed",
                    "failure_reason": str(exc),
                    "source": "amap_geocode_v3",
                    "checked_at": checked_at,
                }
            )
            return record
        for raw in geocodes:
            candidate = _candidate_from_geocode(raw)
            if _admin_matches(entity, candidate, override) and candidate.get("longitude") is not None:
                return _apply_candidate(
                    record,
                    candidate,
                    score=0.8,
                    reasons=["region_policy", "admin_match", "geocode_location"],
                    status="region_geocoded",
                    review_status="manual_approved_region",
                    source="amap_geocode_v3",
                    checked_at=checked_at,
                    bind_poi=False,
                )

    ranked: list[tuple[float, dict[str, Any], list[str]]] = []
    cache_hits: list[bool] = []
    try:
        for query in queries:
            pois, cache_hit = client.search_place(query, city)
            cache_hits.append(cache_hit)
            for raw in pois:
                candidate = _candidate_from_poi(raw)
                score, reasons = score_candidate(entity, candidate, override)
                ranked.append((score, candidate, reasons))
            if ranked:
                if policy == "auto" and any(
                    score >= 0.88
                    and _name_is_high_confidence(reasons)
                    and _admin_matches(entity, candidate, override)
                    and _candidate_type_matches(entity, candidate)
                    for score, candidate, reasons in ranked
                ):
                    break
                if policy != "auto" and max(item[0] for item in ranked) >= 0.88:
                    break
    except CacheMissError as exc:
        record.update(
            {
                "geocode_status": "pending",
                "failure_reason": str(exc),
                "source": "cache_only",
                "checked_at": None,
            }
        )
        return record
    except AmapApiError as exc:
        record.update(
            {
                "geocode_status": "api_failed",
                "failure_reason": str(exc),
                "source": "amap_place_v3",
                "checked_at": checked_at,
            }
        )
        return record

    if policy == "auto":
        ranked.sort(
            key=lambda item: (_candidate_type_matches(entity, item[1]), item[0]),
            reverse=True,
        )
    else:
        ranked.sort(key=lambda item: item[0], reverse=True)
    if cache_hits:
        record["cache_hit"] = record["cache_hit"] and all(cache_hits) if policy == "region" else all(cache_hits)
    if ranked:
        best_score, best_candidate, best_reasons = ranked[0]
        record["review_candidate"] = _sanitize_review_candidate(best_candidate, best_score, best_reasons)
        if (
            policy == "auto"
            and inventory_review == "approved"
            and best_score >= 0.88
            and _name_is_high_confidence(best_reasons)
            and _admin_matches(entity, best_candidate, override)
            and _candidate_type_matches(entity, best_candidate)
            and best_candidate.get("map_poi_id")
            and best_candidate.get("longitude") is not None
            and best_candidate.get("latitude") is not None
        ):
            return _apply_candidate(
                record,
                best_candidate,
                score=best_score,
                reasons=best_reasons,
                status="matched",
                review_status="auto_approved",
                source="amap_place_v3",
                checked_at=checked_at,
                bind_poi=True,
            )

    if ranked:
        best_score, _candidate, _reasons = ranked[0]
        record.update(
            {
                "geocode_status": "review_required",
                "map_review_status": "review_required",
                "match_confidence": best_score,
                "match_level": "medium" if best_score >= 0.7 else "low",
                "match_reasons": _reasons,
                "failure_reason": "候选未满足自动绑定条件或目的地要求人工审核",
                "source": "amap_place_v3",
                "checked_at": checked_at,
            }
        )
        return record

    record.update(
        {
            "geocode_status": "no_match",
            "map_review_status": "review_required",
            "failure_reason": "高德 Place 搜索未返回可审核候选",
            "source": "amap_place_v3",
            "checked_at": checked_at,
        }
    )
    return record


def build_enrichment_records(
    entities: list[dict[str, Any]],
    overrides: dict[str, dict[str, Any]],
    client: AmapPlaceClient,
    *,
    existing: list[dict[str, Any]] | None = None,
    limit: int | None = None,
    resume: bool = True,
    refresh: bool = False,
) -> list[dict[str, Any]]:
    destinations = sorted(
        (row for row in entities if row.get("entity_type") == "destination"),
        key=lambda row: row["entity_id"],
    )
    existing_by_id = {row.get("destination_id"): row for row in (existing or [])}
    result_by_id: dict[str, dict[str, Any]] = {}
    targets: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for entity in destinations:
        destination_id = entity["entity_id"]
        override = overrides.get(destination_id, {})
        base = _base_record(entity, override)
        old = existing_by_id.get(destination_id)
        unchanged = old and old.get("input_fingerprint") == base["input_fingerprint"]
        terminal = old and old.get("geocode_status") in TERMINAL_STATUSES
        if resume and not refresh and unchanged and terminal:
            result_by_id[destination_id] = old
        else:
            # Keep the last explicit non-terminal outcome until this destination is
            # actually selected by the current limit. This makes limited resume runs
            # a real checkpoint instead of silently reverting failures to pending.
            result_by_id[destination_id] = old if resume and not refresh and unchanged and old else base
            targets.append((entity, override))

    if limit is not None:
        targets = targets[: max(limit, 0)]
    for entity, override in targets:
        result_by_id[entity["entity_id"]] = enrich_destination(entity, override, client)
    return [result_by_id[row["entity_id"]] for row in destinations]


def apply_enrichment_to_entities(
    entities: list[dict[str, Any]], records: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    by_id = {record["destination_id"]: record for record in records}
    output: list[dict[str, Any]] = []
    for original in entities:
        entity = dict(original)
        record = by_id.get(entity.get("entity_id"))
        if not record:
            output.append(entity)
            continue
        coordinates = record.get("coordinates") or {}
        admin = record.get("standard_admin") or {}
        operations = record.get("basic_operations") or {}
        entity.update(
            {
                "longitude": coordinates.get("longitude"),
                "latitude": coordinates.get("latitude"),
                "map_poi_id": record.get("map_poi_id"),
                "standard_province": admin.get("province", UNKNOWN),
                "standard_city": admin.get("city", UNKNOWN),
                "standard_district": admin.get("district", UNKNOWN),
                "adcode": admin.get("adcode", UNKNOWN),
                "address": record.get("address", UNKNOWN),
                "telephone": operations.get("telephone", UNKNOWN),
                "business_area": operations.get("business_area", UNKNOWN),
                "opening_hours": operations.get("opening_hours", UNKNOWN),
                "operational_status": operations.get("operational_status", UNKNOWN),
                "map_match_confidence": record.get("match_confidence", 0.0),
                "map_match_level": record.get("match_level", UNKNOWN),
                "geocode_status": record.get("geocode_status", "pending"),
                "map_review_status": record.get("map_review_status", "review_required"),
                "map_checked_at": record.get("checked_at"),
                "map_source": record.get("source", UNKNOWN),
            }
        )
        output.append(entity)
    return output


def build_failure_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "destination_id": row["destination_id"],
            "name": row["name"],
            "geocode_status": row["geocode_status"],
            "failure_reason": row.get("failure_reason"),
            "checked_at": row.get("checked_at"),
            "source": row.get("source", UNKNOWN),
        }
        for row in records
        if row.get("geocode_status") in {"api_failed", "no_match"}
    ]


def status_counts(records: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        status = str(record.get("geocode_status") or "pending")
        counts[status] = counts.get(status, 0) + 1
    return dict(sorted(counts.items()))
