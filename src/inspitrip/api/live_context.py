from __future__ import annotations

import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

import requests
from dotenv import dotenv_values

from inspitrip.paths import DEFAULT_ENV_PATH

ENV_PATH = DEFAULT_ENV_PATH
AMAP_WEATHER_URL = "https://restapi.amap.com/v3/weather/weatherInfo"
TAVILY_SEARCH_URL = "https://api.tavily.com/search"
TAVILY_SNIPPET_MAX_CHARS = 600
TAVILY_TITLE_MAX_CHARS = 200
DEFAULT_LIVE_CONTEXT_DEADLINE_SECONDS = 3.0
WEATHER_SEASON_ASPECT = "weather_season"
RECENT_WEB_ASPECTS = frozenset({"crowd", "commercialization"})
TAVILY_ASPECTS = frozenset({WEATHER_SEASON_ASPECT, *RECENT_WEB_ASPECTS})


class AmapAPIError(RuntimeError):
    def __init__(self, info: str, infocode: str = ""):
        super().__init__(info)
        self.info = info
        self.infocode = infocode


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _configured(name: str) -> str:
    return str(dotenv_values(ENV_PATH).get(name) or "").strip()


def _clip_text(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "…"


class TTLCache:
    def __init__(
        self,
        ttl_seconds: int,
        clock: Callable[[], float] = time.monotonic,
        *,
        max_entries: int = 256,
    ):
        self.ttl_seconds = max(1, int(ttl_seconds))
        self.clock = clock
        self.max_entries = max(1, int(max_entries))
        self._values: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: str) -> Any | None:
        now = self.clock()
        with self._lock:
            item = self._values.get(key)
            if not item:
                return None
            expires_at, value = item
            if expires_at <= now:
                self._values.pop(key, None)
                return None
            self._values.move_to_end(key)
            return value

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._values.pop(key, None)
            self._values[key] = (self.clock() + self.ttl_seconds, value)
            while len(self._values) > self.max_entries:
                self._values.popitem(last=False)

    def __len__(self) -> int:
        with self._lock:
            return len(self._values)


class SingleFlight:
    """Shares one in-flight result between callers using the same cache key."""

    class _Call:
        def __init__(self) -> None:
            self.event = threading.Event()
            self.result: Any = None
            self.error: BaseException | None = None

    def __init__(self) -> None:
        self._calls: dict[str, SingleFlight._Call] = {}
        self._lock = threading.Lock()

    def do(self, key: str, function: Callable[[], Any]) -> tuple[Any, bool]:
        with self._lock:
            call = self._calls.get(key)
            leader = call is None
            if call is None:
                call = self._Call()
                self._calls[key] = call

        if not leader:
            call.event.wait()
            if call.error is not None:
                raise call.error
            return call.result, True

        try:
            call.result = function()
        except BaseException as exc:
            call.error = exc
        finally:
            call.event.set()
            with self._lock:
                self._calls.pop(key, None)
        if call.error is not None:
            raise call.error
        return call.result, False


@dataclass
class AmapWeatherClient:
    api_key: str
    timeout: float = 8.0
    session: Any = requests
    cache: TTLCache | None = None
    singleflight: SingleFlight | None = None

    def __post_init__(self) -> None:
        if not self.api_key:
            raise RuntimeError("未配置 AMAP_KEY")
        if self.cache is None:
            self.cache = TTLCache(45 * 60, max_entries=256)
        if self.singleflight is None:
            self.singleflight = SingleFlight()

    def _request(self, adcode: str, extensions: str) -> dict[str, Any]:
        response = self.session.get(
            AMAP_WEATHER_URL,
            params={
                "key": self.api_key,
                "city": adcode,
                "extensions": extensions,
                "output": "JSON",
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("status") != "1":
            raise AmapAPIError(
                str(payload.get("info") or "AMAP weather failed"),
                str(payload.get("infocode") or ""),
            )
        return payload

    def get_weather(self, adcode: str) -> dict[str, Any]:
        cache_key = str(adcode).strip()
        if not cache_key:
            return {
                "available": False,
                "source": "高德天气 API",
                "reason": "missing_adcode",
                "checked_at": _utc_now(),
            }
        assert self.cache is not None
        cached = self.cache.get(cache_key)
        if cached is not None:
            return {**cached, "cache_hit": True}

        def load() -> dict[str, Any]:
            # A caller may have populated the cache while this call was waiting to lead.
            cached_inside = self.cache.get(cache_key)
            if cached_inside is not None:
                return cached_inside
            checked_at = _utc_now()
            try:
                live_payload = self._request(cache_key, "base")
                forecast_payload = self._request(cache_key, "all")
                live = (live_payload.get("lives") or [{}])[0]
                forecast = (forecast_payload.get("forecasts") or [{}])[0]
                result = {
                    "available": bool(live or forecast.get("casts")),
                    "source": "高德天气 API",
                    "confidence": "高",
                    "checked_at": checked_at,
                    "reporttime": live.get("reporttime") or forecast.get("reporttime"),
                    "current": {
                        "weather": live.get("weather"),
                        "temperature_c": live.get("temperature"),
                        "humidity_percent": live.get("humidity"),
                        "wind_direction": live.get("winddirection"),
                        "wind_power": live.get("windpower"),
                    },
                    "forecast": [
                        {
                            "date": row.get("date"),
                            "day_weather": row.get("dayweather"),
                            "night_weather": row.get("nightweather"),
                            "day_temperature_c": row.get("daytemp"),
                            "night_temperature_c": row.get("nighttemp"),
                            "day_wind": row.get("daywind"),
                            "night_wind": row.get("nightwind"),
                            "day_wind_power": row.get("daypower"),
                            "night_wind_power": row.get("nightpower"),
                        }
                        for row in (forecast.get("casts") or [])[:4]
                    ],
                    "safety_assessment": None,
                    "cache_hit": False,
                }
            except Exception as exc:
                result = {
                    "available": False,
                    "source": "高德天气 API",
                    "checked_at": checked_at,
                    "reason": type(exc).__name__,
                    "safety_assessment": None,
                    "cache_hit": False,
                }
                if isinstance(exc, AmapAPIError):
                    result["api_info"] = exc.info
                    result["api_infocode"] = exc.infocode
            self.cache.set(cache_key, result)
            return result

        assert self.singleflight is not None
        result, shared = self.singleflight.do(cache_key, load)
        return {**result, "singleflight_shared": shared}


@dataclass
class TavilyLowConfidenceVerifier:
    api_key: str
    timeout: float = 12.0
    session: Any = requests
    cache: TTLCache | None = None
    singleflight: SingleFlight | None = None

    def __post_init__(self) -> None:
        if not self.api_key:
            raise RuntimeError("未配置 TAVILY_API_KEY")
        if self.cache is None:
            self.cache = TTLCache(6 * 60 * 60, max_entries=256)
        if self.singleflight is None:
            self.singleflight = SingleFlight()

    def _search(self, query: str, *, time_range: str | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {
            "api_key": self.api_key,
            "query": query,
            "topic": "general",
            "search_depth": "basic",
            "max_results": 4,
            "include_answer": False,
            "include_raw_content": False,
            "include_images": False,
        }
        if time_range:
            body["time_range"] = time_range
        response = self.session.post(TAVILY_SEARCH_URL, json=body, timeout=self.timeout)
        response.raise_for_status()
        payload = response.json()
        return {
            "results": [
                {
                    "title": _clip_text(item.get("title"), TAVILY_TITLE_MAX_CHARS),
                    "url": item.get("url"),
                    "snippet": _clip_text(item.get("content"), TAVILY_SNIPPET_MAX_CHARS),
                    "published_date": item.get("published_date"),
                    "search_score": item.get("score"),
                }
                for item in (payload.get("results") or [])[:4]
            ]
        }

    def verify(
        self,
        name: str,
        city: str = "",
        province: str = "",
        *,
        aspects: list[str] | tuple[str, ...] | set[str] | None = None,
    ) -> dict[str, Any]:
        place = " ".join(part for part in (province, city, name) if part).strip()
        requested = TAVILY_ASPECTS if aspects is None else TAVILY_ASPECTS.intersection(aspects)
        requested_aspects = sorted(requested)
        cache_key = f"{place}|{','.join(requested_aspects)}"
        assert self.cache is not None
        cached = self.cache.get(cache_key)
        if cached is not None:
            return {**cached, "cache_hit": True}

        def load() -> dict[str, Any]:
            cached_inside = self.cache.get(cache_key)
            if cached_inside is not None:
                return cached_inside
            checked_at = _utc_now()
            result: dict[str, Any] = {
                "available": False,
                "source": "Tavily Search",
                "confidence": "低",
                "verification_label": "低置信近期核验",
                "checked_at": checked_at,
                "usage": "仅作最佳季节与近期拥挤/网红化变化提示，不参与硬过滤或永久事实写入",
                "requested_aspects": requested_aspects,
                "cache_hit": False,
            }
            try:
                season_results: list[dict[str, Any]] = []
                recent_results: list[dict[str, Any]] = []
                if WEATHER_SEASON_ASPECT in requested:
                    season_results = self._search(
                        f"{place} 最佳旅游季节 气候 官方文旅"
                    )["results"]
                    result["best_season_sources"] = season_results
                if requested.intersection(RECENT_WEB_ASPECTS):
                    recent_results = self._search(
                        f"{place} 近期 拥挤 排队 游客 网红化 变化",
                        time_range="month",
                    )["results"]
                    result["recent_crowd_and_trend_sources"] = recent_results
                result["available"] = bool(season_results or recent_results)
                if not requested:
                    result["reason"] = "no_requested_aspects"
            except Exception as exc:
                result["reason"] = type(exc).__name__
            self.cache.set(cache_key, result)
            return result

        assert self.singleflight is not None
        result, shared = self.singleflight.do(cache_key, load)
        return {**result, "singleflight_shared": shared}


def candidate_adcode(candidate: dict[str, Any]) -> str:
    metadata = candidate.get("metadata") or {}
    fact_payload = metadata.get("fact_payload") or {}
    map_enrichment = metadata.get("map_enrichment") or fact_payload.get("map_enrichment") or {}
    for mapping in (candidate, metadata, fact_payload, map_enrichment):
        if isinstance(mapping, dict):
            value = mapping.get("adcode") or mapping.get("district_adcode")
            if value:
                return str(value)
    return ""


class LiveContextService:
    def __init__(
        self,
        weather: AmapWeatherClient | None,
        verifier: TavilyLowConfidenceVerifier | None,
        *,
        max_workers: int = 6,
        deadline_seconds: float = DEFAULT_LIVE_CONTEXT_DEADLINE_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.weather = weather
        self.verifier = verifier
        self.max_workers = max(1, max_workers)
        self.deadline_seconds = max(0.01, float(deadline_seconds))
        self.clock = clock
        self._executor = ThreadPoolExecutor(
            max_workers=self.max_workers,
            thread_name_prefix="live-context",
        )

    def enrich(
        self,
        candidates: list[dict[str, Any]],
        limit: int = 3,
        *,
        evidence_aspects: list[str] | tuple[str, ...] | set[str] | None = None,
        deadline_seconds: float | None = None,
    ) -> dict[str, Any]:
        aspects = {str(value).strip() for value in (evidence_aspects or []) if str(value).strip()}
        use_weather = bool(self.weather and WEATHER_SEASON_ASPECT in aspects)
        tavily_aspects = sorted(TAVILY_ASPECTS.intersection(aspects))
        use_tavily = bool(self.verifier and tavily_aspects)
        request_deadline = self.deadline_seconds if deadline_seconds is None else max(
            0.01, float(deadline_seconds)
        )
        deadline_at = self.clock() + request_deadline
        selected = candidates[: max(0, min(limit, 5))]
        context: dict[str, dict[str, Any]] = {
            row["destination_id"]: {} for row in selected if row.get("destination_id")
        }
        futures: dict[Any, tuple[str, str]] = {}
        for row in selected:
            destination_id = row.get("destination_id")
            if not destination_id:
                continue
            if use_weather:
                futures[
                    self._executor.submit(self.weather.get_weather, candidate_adcode(row))
                ] = (destination_id, "weather")
            if use_tavily:
                futures[
                    self._executor.submit(
                        self.verifier.verify,
                        str(row.get("name") or ""),
                        str(row.get("city") or ""),
                        str(row.get("province") or ""),
                        aspects=tavily_aspects,
                    )
                ] = (destination_id, "web_verification")

        remaining = max(0.0, deadline_at - self.clock())
        completed, pending = wait(futures, timeout=remaining)
        for future in completed:
            destination_id, key = futures[future]
            try:
                context[destination_id][key] = future.result()
            except Exception as exc:
                context[destination_id][key] = {
                    "available": False,
                    "reason": type(exc).__name__,
                    "checked_at": _utc_now(),
                }
        timed_out_tasks: list[dict[str, str]] = []
        for future in pending:
            destination_id, key = futures[future]
            future.cancel()
            context[destination_id][key] = {
                "available": False,
                "reason": "deadline_exceeded",
                "checked_at": _utc_now(),
            }
            timed_out_tasks.append({"destination_id": destination_id, "context": key})
        return {
            "candidate_count": len(selected),
            "items": context,
            "requested_aspects": sorted(aspects),
            "triggered": {
                "weather": use_weather,
                "season_verification": bool(
                    use_tavily and WEATHER_SEASON_ASPECT in tavily_aspects
                ),
                "recent_verification": bool(
                    use_tavily and RECENT_WEB_ASPECTS.intersection(tavily_aspects)
                ),
            },
            "deadline_seconds": request_deadline,
            "deadline_exceeded": bool(pending),
            "timed_out_tasks": timed_out_tasks,
            "weather_persisted": False,
            "web_findings_persisted": False,
        }


def build_live_context_service() -> LiveContextService:
    amap_key = _configured("AMAP_KEY")
    tavily_key = _configured("TAVILY_API_KEY")
    weather = AmapWeatherClient(amap_key) if amap_key else None
    verifier = TavilyLowConfidenceVerifier(tavily_key) if tavily_key else None
    return LiveContextService(weather, verifier)
