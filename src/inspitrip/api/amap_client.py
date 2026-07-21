# -*- coding: utf-8 -*-
"""
amap_client.py —— 高德路径规划封装（源自 geo/api_test.ipynb 的 AmapRoutePlanner）。
改动：key 从环境变量读，不硬编码；方法返回结构化 dict，供 FastAPI 直接序列化为 JSON。
"""
from __future__ import annotations
import requests
from dotenv import dotenv_values 

from inspitrip.paths import DEFAULT_ENV_PATH

ENV_PATH = DEFAULT_ENV_PATH

class AmapRoutePlanner:
    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or dotenv_values(ENV_PATH).get("AMAP_KEY")
        if not self.api_key:
            raise RuntimeError("未配置高德 Key，请设置环境变量 AMAP_KEY")
        self.geocode_url = "https://restapi.amap.com/v3/geocode/geo"
        self.reverse_geocode_url = "https://restapi.amap.com/v3/geocode/regeo"
        self.driving_url = "https://restapi.amap.com/v3/direction/driving"
        self.walking_url = "https://restapi.amap.com/v3/direction/walking"
        self.bicycling_url = "https://restapi.amap.com/v4/direction/bicycling"
        self.transit_url = "https://restapi.amap.com/v3/direction/transit/integrated"

    # ---------- 1. 地理编码：地址 -> 坐标/城市/adcode ----------
    def get_coordinates(self, address: str):
        params = {"key": self.api_key, "address": address, "output": "JSON"}
        try:
            data = requests.get(self.geocode_url, params=params, timeout=10).json()
            if data.get("status") == "1" and data.get("geocodes"):
                g = data["geocodes"][0]
                city = g.get("city") or g.get("province")
                return g["location"], (city if isinstance(city, str) else None), g.get("adcode")
        except Exception as exc:
            print(f"geocode 异常 {address}: {exc}")
        return None, None, None

    def reverse_geocode(self, longitude: float, latitude: float) -> dict | None:
        """坐标 -> 城市/区县。只返回本次推荐需要的行政区信息，不持久化坐标。"""
        params = {
            "key": self.api_key,
            "location": f"{longitude:.6f},{latitude:.6f}",
            "extensions": "base",
            "radius": 1000,
            "output": "JSON",
        }
        try:
            data = requests.get(self.reverse_geocode_url, params=params, timeout=10).json()
        except (requests.RequestException, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        if data.get("status") != "1" or not data.get("regeocode"):
            return None
        regeocode = data["regeocode"]
        component = regeocode.get("addressComponent") or {}
        raw_city = component.get("city")
        if not isinstance(raw_city, str) or not raw_city.strip():
            raw_city = component.get("province")
        if not isinstance(raw_city, str) or not raw_city.strip():
            return None
        city = raw_city.strip()
        if city.endswith("市"):
            city = city[:-1]
        return {
            "city": city,
            "province": str(component.get("province") or "").strip(),
            "district": str(component.get("district") or "").strip(),
            "adcode": str(component.get("adcode") or "").strip(),
            "formatted_address": str(regeocode.get("formatted_address") or "").strip(),
        }

    # ---------- 2. 各交通方式：返回 (duration秒, distance米) ----------
    def get_driving_time(self, origin: str, destination: str):
        params = {"key": self.api_key, "origin": origin, "destination": destination, "strategy": 0}
        try:
            res = requests.get(self.driving_url, params=params, timeout=10).json()
            if res.get("status") == "1" and res.get("route"):
                p = res["route"]["paths"][0]
                return int(p["duration"]), int(p["distance"])
        except Exception:
            pass
        return None, None

    def get_walking_time(self, origin: str, destination: str):
        params = {"key": self.api_key, "origin": origin, "destination": destination}
        try:
            res = requests.get(self.walking_url, params=params, timeout=10).json()
            if res.get("status") == "1" and res.get("route"):
                p = res["route"]["paths"][0]
                return int(p["duration"]), int(p["distance"])
        except Exception:
            pass
        return None, None

    def get_bicycling_time(self, origin: str, destination: str):
        params = {"key": self.api_key, "origin": origin, "destination": destination}
        try:
            res = requests.get(self.bicycling_url, params=params, timeout=10).json()
            if "data" in res and res["data"].get("paths"):
                p = res["data"]["paths"][0]
                return int(p["duration"]), int(p["distance"])
        except Exception:
            pass
        return None, None

    def get_transit_time(self, origin: str, destination: str, city_code: str | None):
        params = {"key": self.api_key, "origin": origin, "destination": destination,
                  "city": city_code, "strategy": 0}
        try:
            res = requests.get(self.transit_url, params=params, timeout=10).json()
            if res.get("status") == "1" and res.get("route") and res["route"].get("transits"):
                t = res["route"]["transits"][0]
                return int(t["duration"]), int(t["distance"])
        except Exception:
            pass
        return None, None

    # ---------- 3. 格式化辅助 ----------
    @staticmethod
    def fmt_time(seconds):
        if seconds is None:
            return None
        m = round(seconds / 60)
        return f"{m}分钟" if m < 60 else f"{m // 60}小时{m % 60}分钟"

    @staticmethod
    def fmt_dist(meters):
        if meters is None:
            return None
        return f"{meters}米" if meters < 1000 else f"{meters / 1000:.1f}公里"

    @staticmethod
    def _is_lnglat(text: str) -> bool:
        """判断输入是否已是 '经度,纬度' 坐标串（前端定位会直接回填坐标）。"""
        parts = text.split(",")
        if len(parts) != 2:
            return False
        try:
            lng, lat = float(parts[0]), float(parts[1])
            return 73 <= lng <= 135 and 3 <= lat <= 54   # 中国经纬度范围
        except ValueError:
            return False

    def _resolve(self, text: str, city_hint: str | None = None):
        """地址或坐标串 -> (location, city, adcode)。坐标串跳过 geocode；
        city_hint 用于消解同名地歧义（如'朱家角'既在上海也在四川，真实产品由 POI 的 city 提供）。"""
        if self._is_lnglat(text):
            return text, None, None
        params = {"key": self.api_key, "address": text, "output": "JSON"}
        if city_hint:
            params["city"] = city_hint
        try:
            data = requests.get(self.geocode_url, params=params, timeout=10).json()
            if data.get("status") == "1" and data.get("geocodes"):
                g = data["geocodes"][0]
                c = g.get("city") or g.get("province")
                return g["location"], (c if isinstance(c, str) else None), g.get("adcode")
        except Exception as exc:
            print(f"geocode 异常 {text}: {exc}")
        return None, None, None

    # ---------- 4. 主入口：多方式对比，返回结构化结果 ----------
    def compare_routes(self, start_address: str, end_address: str,
                       start_city: str | None = None, end_city: str | None = None) -> dict:
        origin_loc, origin_city, origin_adcode = self._resolve(start_address, start_city)
        dest_loc, _, _ = self._resolve(end_address, end_city)
        if not origin_loc or not dest_loc:
            return {"ok": False, "error": "无法解析起点或终点地址，请检查名称。"}

        city_param = origin_adcode or origin_city
        raw = {
            "driving": self.get_driving_time(origin_loc, dest_loc),
            "transit": self.get_transit_time(origin_loc, dest_loc, city_param),
            "bicycling": self.get_bicycling_time(origin_loc, dest_loc),
            "walking": self.get_walking_time(origin_loc, dest_loc),
        }
        labels = {"driving": "驾车", "transit": "公交/地铁", "bicycling": "骑行", "walking": "步行"}
        icons = {"driving": "🚗", "transit": "🚌", "bicycling": "🚲", "walking": "🚶"}

        modes = []
        for key, (dur, dist) in raw.items():
            modes.append({
                "mode": key,
                "label": labels[key],
                "icon": icons[key],
                "duration_sec": dur,
                "distance_m": dist,
                "duration_text": self.fmt_time(dur) or "暂无数据",
                "distance_text": self.fmt_dist(dist) or "暂无数据",
                "available": dur is not None,
            })
        # 远距离/离岛提示：驾车明显超长时给出诚实提醒
        drive = raw["driving"][0]
        note = ""
        if drive and drive > 4 * 3600:
            note = "驾车耗时异常长，目的地可能为离岛或需轮渡；高德驾车不含船程，实际请查官方船班。"

        return {
            "ok": True,
            "start": {"address": start_address, "location": origin_loc, "city": origin_city},
            "end": {"address": end_address, "location": dest_loc},
            "modes": modes,
            "note": note,
        }
