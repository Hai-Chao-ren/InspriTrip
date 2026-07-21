from __future__ import annotations

import json
import re
from functools import lru_cache
from typing import Any, Iterable

from jsonschema import Draft7Validator

from inspitrip.paths import DEMO_DATA_DIR, SCHEMA_DIR


QUERY_PLAN_SCHEMA_PATH = SCHEMA_DIR / "query_plan_schema.json"

SCOPES = {"in_domain", "out_of_region", "not_travel", "not_supported_yet"}
TASK_TYPES = {"destination_discovery", "experience_lookup", "chitchat", "unsupported"}
TRANSPORT_MODES = ("高铁", "自驾", "大巴", "轮渡", "地铁", "公共交通")

MOOD_TERMS = {
    "mood_unwind": ("独处", "一个人", "独自", "发呆", "安静待着", "安静", "清静", "躲开人群", "不喧闹"),
    "mood_heal": ("治愈", "放松", "松弛", "躺平", "回血", "解压"),
    "mood_romantic": ("浪漫", "约会", "情侣", "对象", "氛围感"),
    "mood_excited": ("刺激", "冒险", "挑战", "热血"),
    "mood_nostalgic": ("怀旧", "年代感", "旧时光", "复古"),
    "mood_social": ("热闹", "朋友", "欢聚", "一群人", "人多一点"),
    "mood_inspired": ("文艺", "灵感", "审美", "充电"),
    "mood_freedom": ("随性", "自由", "说走就走", "不做攻略", "随便走走"),
}
VIBE_TERMS = {
    "vibe_niche": ("小众", "冷门", "人少", "避开人潮", "没那么网红"),
    "vibe_unspoiled": ("不商业化", "原生态", "没开发", "未开发", "质朴", "没那么商业化"),
    "vibe_artsy": ("文艺", "设计感", "有调性", "小资", "审美在线"),
    "vibe_nature": ("自然", "山野", "海边", "看海", "森林", "湖"),
    "vibe_local": ("烟火气", "市井", "本地生活", "老街小巷"),
    "vibe_cozy": ("精致", "惬意", "舒服", "品质感"),
    "vibe_ancient": ("古镇", "古村", "历史", "古朴"),
    "vibe_urban": ("都市", "潮流", "时髦", "商圈", "城市街区", "有活力的城市"),
}
ACTIVITY_TERMS = {
    "act_sea": ("看海", "赶海", "海岛", "海边", "沙滩", "踏浪"),
    "act_hike": ("徒步", "爬山", "登山", "登高"),
    "act_camp": ("露营", "野餐", "星空", "扎营"),
    "act_town": ("古镇", "古村", "老街", "水乡"),
    "act_cafe": ("咖啡", "书店", "下午茶"),
    "act_art": ("看展", "博物馆", "美术馆", "艺术展"),
    "act_ride": ("骑行", "骑车", "环湖", "自行车"),
    "act_stay": ("民宿", "酒店", "住一晚", "住得", "住宿"),
    "act_hotspring": ("温泉", "泡汤", "汤池"),
    "act_food": ("美食", "探店", "小吃", "觅食", "好吃的"),
}
ALLOWED_TAG_IDS = {
    "mood": frozenset(MOOD_TERMS),
    "vibe": frozenset(VIBE_TERMS),
    "activity": frozenset(ACTIVITY_TERMS),
}

OUT_OF_REGION_TERMS = (
    "云南", "西藏", "新疆", "四川", "成都", "重庆", "北京", "天津", "东北",
    "海南", "三亚", "厦门", "福建", "广东", "广州", "深圳", "广西", "桂林",
    "青海", "甘肃", "内蒙古", "日本", "韩国", "泰国", "欧洲", "美国", "出国", "国外",
    "北极", "南极", "香港", "澳门", "台湾",
)
IN_REGION_TERMS = ("上海", "江苏", "浙江", "杭州", "苏州", "南京", "宁波", "温州", "湖州", "嘉兴", "绍兴", "舟山", "无锡", "常州", "镇江", "扬州", "南通", "连云港")
CHITCHAT_PATTERNS = (
    r"^(?:(?:你好|您好|嗨|hi|hello|在吗|谢谢|你是谁|你能做什么)(?:呀|啊|呢)?[！!。,.，？?\s]*)+$",
    r"(?:写代码|翻译|算一下|讲个笑话|股票|编程|数学题)",
    r"^今天心情怎么样[？?。\s]*$",
)
SERVICE_TERMS = (
    "订酒店", "订民宿", "找酒店", "找民宿", "推荐饭店", "推荐餐厅", "哪里好吃",
    "订餐", "找一家", "订一家", "替我订", "帮我订",
)
SIGHTSEEING_TERMS = tuple(
    dict.fromkeys(
        term
        for tag_id, terms in ACTIVITY_TERMS.items()
        if tag_id not in {"act_cafe", "act_stay", "act_food"}
        for term in terms
    )
) + ("景点", "观光", "风景", "周末去哪", "目的地", "旅行", "旅游")
LOOKUP_TERMS = ("怎么玩", "玩法", "攻略", "行程", "路线", "哪些景点", "有什么", "值得去", "怎么安排", "附近", "做什么")

UNSUPPORTED_TRANSACTION_TERMS = ("买门票", "景区门票", "订票", "替我订票", "完整轮渡班次")

NEGATIVE_PREFIX = re.compile(
    r"(?:不要|不想|不愿|不能|别|拒绝|避免|避开|不考虑|排除|不用|不再|不)"
    r"(?:\s*(?:安排|找|住|泡|去|逛|看|做|吃|爬|登|要))?"
    r"(?:[^，,。；;！!?但不过]*)$"
)
DOUBLE_NEGATIVE_PREFIX = re.compile(
    r"(?:不是不能|不是不想|并非不要|并不是不|不代表不|不是不|不想错过|不能没有|不能不)"
    r"(?:[^，,。；;！!?但不过]*)$"
)
WEAK_PREFIX = re.compile(
    r"(?:顺便|也可以|可以|可选|有的话|如果能|最好能|不强求|都行|加分|优先)"
    r"(?:\s*(?:去|想|喝|逛|泡|看|骑|住|吃|走))?[^，,。；;！!?但不过]*$"
)
WEAK_SUFFIX = re.compile(
    r"^(?:店)?\s*(?:(?:能到|可达)?最好|可以有|可以顺便|优先|不是必须|不是硬要求|不强求|可有可无|有就更好|更好|加分|也行|都行)"
)

CN_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
CN_UNITS = {"十": 10, "百": 100, "千": 1000, "万": 10000}
NUMBER_TOKEN = r"(?:\d+(?:\.\d+)?|[零〇一二两三四五六七八九十百千万]+)"


def empty_query_plan() -> dict[str, Any]:
    return {
        "scope": "in_domain",
        "task_type": "destination_discovery",
        "target_destination": None,
        "hard_constraints": {
            "origin": None,
            "days_max": None,
            "budget_max": None,
            "travel_time_max": None,
            "transport_modes": [],
            "must_have_activities": [],
        },
        "exclusions": [],
        "semantic_query": "",
        "soft_preferences": {"mood": [], "vibe": [], "activity": []},
        "evidence_aspects": [],
    }


def _dedupe(values: Iterable[Any]) -> list[Any]:
    result: list[Any] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result


def _weighted_tags(
    value: Any,
    *,
    dimension: str,
    minimum: float = 0.0,
) -> list[dict[str, Any]]:
    by_id: dict[str, float] = {}
    allowed = ALLOWED_TAG_IDS[dimension]
    for item in value or []:
        if isinstance(item, str):
            tag_id, confidence = item, 0.7
        elif isinstance(item, dict):
            tag_id = str(item.get("id") or "")
            try:
                confidence = float(item.get("confidence"))
            except (TypeError, ValueError):
                confidence = 0.0
        else:
            continue
        if tag_id in allowed and confidence >= minimum:
            by_id[tag_id] = max(by_id.get(tag_id, 0.0), min(max(confidence, 0.0), 1.0))
    return [
        {"id": tag_id, "confidence": round(confidence, 4)}
        for tag_id, confidence in sorted(by_id.items(), key=lambda item: item[1], reverse=True)[:2]
    ]


def _optional_int(value: Any, *, minimum: int = 0) -> int | None:
    if value in (None, ""):
        return None
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return None
    return number if number >= minimum else None


def _schema_validator(schema_path: Path | None = None) -> Draft7Validator:
    path = schema_path or QUERY_PLAN_SCHEMA_PATH
    return Draft7Validator(json.loads(path.read_text(encoding="utf-8")))


def _raise_first_schema_error(plan: dict[str, Any], schema_path: Path | None = None) -> None:
    errors = sorted(_schema_validator(schema_path).iter_errors(plan), key=lambda error: list(error.absolute_path))
    if errors:
        error = errors[0]
        path = ".".join(str(item) for item in error.absolute_path) or "$"
        raise ValueError(f"query plan {path}: {error.message}")


def normalize_query_plan(
    payload: dict[str, Any],
    *,
    raw_query: str = "",
    schema_path: Path | None = None,
    minimum_soft_tag_confidence: float = 0.55,
) -> dict[str, Any]:
    """Normalize a trusted/legacy plan, then enforce the single V2 schema.

    Untrusted LLM strings must enter through :func:`parse_query_plan_output`,
    which validates before normalization and falls back to the rule parser.
    """
    if not isinstance(payload, dict):
        raise ValueError("query plan payload must be an object")
    hard = dict(payload.get("hard_constraints") or {})
    if not hard:
        hard = {
            "origin": payload.get("origin"),
            "days_max": payload.get("days") or payload.get("days_max"),
            "budget_max": payload.get("budget") or payload.get("budget_max"),
            "travel_time_max": payload.get("travel_time_max"),
            "transport_modes": payload.get("transport_modes") or [],
            "must_have_activities": payload.get("must_have_activities") or [],
        }
    soft = dict(payload.get("soft_preferences") or {})
    if not soft:
        soft = {
            "mood": payload.get("mood") or [],
            "vibe": payload.get("vibe") or [],
            "activity": payload.get("activity") or [],
        }
    scope = str(payload.get("scope") or "in_domain")
    task_type = str(payload.get("task_type") or "destination_discovery")
    normalized = {
        "scope": scope,
        "task_type": task_type,
        "target_destination": str(payload.get("target_destination") or "").strip() or None,
        "hard_constraints": {
            "origin": str(hard.get("origin") or "").strip() or None,
            "days_max": _optional_int(hard.get("days_max"), minimum=1),
            "budget_max": _optional_int(hard.get("budget_max")),
            "travel_time_max": _optional_int(hard.get("travel_time_max")),
            "transport_modes": _dedupe(
                mode for mode in hard.get("transport_modes") or [] if mode in TRANSPORT_MODES
            ),
            "must_have_activities": _dedupe(
                activity for activity in hard.get("must_have_activities") or [] if activity in ALLOWED_TAG_IDS["activity"]
            ),
        },
        "exclusions": _dedupe(
            str(value).strip() for value in payload.get("exclusions") or [] if str(value).strip()
        ),
        "semantic_query": str(payload.get("semantic_query") or payload.get("query_rewrite") or raw_query).strip(),
        "soft_preferences": {
            dimension: _weighted_tags(
                soft.get(dimension),
                dimension=dimension,
                minimum=minimum_soft_tag_confidence,
            )
            for dimension in ("mood", "vibe", "activity")
        },
        "evidence_aspects": _dedupe(
            value
            for value in payload.get("evidence_aspects") or []
            if value in {
                "mood_fit", "crowd", "commercialization", "scenery", "activity",
                "transport", "cost", "stay", "food", "solo", "photo", "safety",
                "weather_season", "other",
            }
        ),
    }
    if scope not in SCOPES:
        raise ValueError(f"unknown query scope: {scope}")
    if task_type not in TASK_TYPES:
        raise ValueError(f"unknown query task_type: {task_type}")
    if scope != "in_domain":
        cleared = empty_query_plan()
        normalized.update(
            {
                "task_type": "chitchat" if scope == "not_travel" else "unsupported",
                "target_destination": None,
                "hard_constraints": cleared["hard_constraints"],
                "exclusions": [],
                "semantic_query": "",
                "soft_preferences": cleared["soft_preferences"],
                "evidence_aspects": [],
            }
        )
    elif task_type not in {"destination_discovery", "experience_lookup"}:
        normalized["task_type"] = "destination_discovery"
    if normalized["scope"] == "in_domain":
        if normalized["task_type"] == "destination_discovery":
            normalized["target_destination"] = None
        elif normalized["task_type"] == "experience_lookup" and not normalized["target_destination"]:
            normalized["task_type"] = "destination_discovery"
    _raise_first_schema_error(normalized, schema_path)
    return normalized


def _cn_number(value: str) -> float | None:
    token = value.strip()
    if not token:
        return None
    try:
        return float(token)
    except ValueError:
        pass
    if "点" in token:
        integer, fraction = token.split("点", 1)
        base = _cn_number(integer) or 0
        digits = "".join(str(CN_DIGITS[char]) for char in fraction if char in CN_DIGITS)
        return base + (float(f"0.{digits}") if digits else 0)
    total = 0
    section = 0
    number = 0
    last_unit = 1
    contains_zero = "零" in token or "〇" in token
    for char in token:
        if char in CN_DIGITS:
            number = CN_DIGITS[char]
            continue
        unit = CN_UNITS.get(char)
        if unit is None:
            return None
        if unit == 10000:
            section = (section + number) * unit
            total += section
            section = 0
            number = 0
        else:
            section += (number or 1) * unit
            number = 0
        last_unit = unit
    if number and last_unit >= 100 and not contains_zero and token[-1] in CN_DIGITS:
        number *= last_unit // 10
    return float(total + section + number)


def _number(value: str | None) -> float | None:
    return _cn_number(value or "")


def _upper_bound_match(text: str, *, unit_pattern: str, prefix_pattern: str = "") -> float | None:
    prefix = rf"(?:{prefix_pattern})\s*" if prefix_pattern else ""
    range_match = re.search(
        rf"{prefix}({NUMBER_TOKEN})\s*(?:到|至|[-~—～])\s*({NUMBER_TOKEN})\s*(?:{unit_pattern})",
        text,
        re.IGNORECASE,
    )
    if range_match:
        values = [_number(range_match.group(1)), _number(range_match.group(2))]
        valid = [value for value in values if value is not None]
        return max(valid) if valid else None
    single = re.search(rf"{prefix}({NUMBER_TOKEN})\s*(?:{unit_pattern})", text, re.IGNORECASE)
    return _number(single.group(1)) if single else None


def _extract_budget(text: str) -> int | None:
    if re.search(r"(?:人均|预算|元|块|[kK]).{0,12}(?:不是硬上限|不是硬要求|不作硬上限|不算硬上限)", text):
        return None
    prefix_pattern = (
        r"人均(?:预算)?(?:不要超过|不超过|最多)?|"
        r"预算(?:改成|调整为|调到|补充到|加到|增加到|是|为)?|"
        r"最多(?:花)?|(?:不要|不能|别)?超过"
    )
    kilo = re.search(
        rf"(?:{prefix_pattern})\s*(\d+(?:\.\d+)?)\s*[kK](?![A-Za-z])",
        text,
    )
    if kilo:
        return int(float(kilo.group(1)) * 1000)
    range_without_unit = re.search(
        rf"(?:{prefix_pattern})\s*({NUMBER_TOKEN})\s*"
        rf"(?:到|至|[-~—～])\s*({NUMBER_TOKEN})(?!\s*(?:天|小时|分钟))",
        text,
    )
    if range_without_unit:
        values = [_number(range_without_unit.group(1)), _number(range_without_unit.group(2))]
        valid = [value for value in values if value is not None]
        if valid:
            return int(max(valid))
    value = _upper_bound_match(
        text,
        unit_pattern=r"元|块|rmb|人民币",
        prefix_pattern=prefix_pattern,
    )
    if value is None:
        match = re.search(rf"(?:{prefix_pattern})\s*({NUMBER_TOKEN})(?:\s*(?:以内|以下|内|左右))", text)
        value = _number(match.group(1)) if match else None
    if value is None:
        match = re.search(rf"({NUMBER_TOKEN})\s*(?:元|块)(?:\s*(?:以内|以下|内))", text)
        value = _number(match.group(1)) if match else None
    if value is None:
        match = re.search(rf"({NUMBER_TOKEN})\s*(?:元|块|rmb|人民币)", text, re.IGNORECASE)
        value = _number(match.group(1)) if match else None
    if value is None:
        match = re.search(
            rf"(?:{prefix_pattern})\s*({NUMBER_TOKEN})(?!\s*(?:个)?\s*(?:半)?\s*(?:天|小时|分钟))",
            text,
        )
        value = _number(match.group(1)) if match else None
    return int(value) if value is not None and value >= 0 else None


def _extract_days(text: str) -> int | None:
    value = _upper_bound_match(text, unit_pattern=r"天")
    if value is not None:
        return max(1, int(value))
    if "两天一夜" in text or "周末" in text:
        return 2
    if any(term in text for term in ("当天回", "当日回", "当天往返", "当日往返", "一日往返")):
        return 1
    return None


def _extract_travel_minutes(text: str) -> int | None:
    range_hours = re.search(rf"({NUMBER_TOKEN})\s*(?:到|至|[-~—～])\s*({NUMBER_TOKEN})\s*(?:个)?小时", text)
    if range_hours:
        values = [_number(range_hours.group(1)), _number(range_hours.group(2))]
        valid = [value for value in values if value is not None]
        return int(max(valid) * 60) if valid else None
    half_before = re.search(rf"({NUMBER_TOKEN})个半小时", text)
    if half_before:
        value = _number(half_before.group(1))
        return int((value + 0.5) * 60) if value is not None else None
    half_after = re.search(rf"({NUMBER_TOKEN})\s*(?:个)?小时半", text)
    if half_after:
        value = _number(half_after.group(1))
        return int((value + 0.5) * 60) if value is not None else None
    hours = re.search(rf"({NUMBER_TOKEN})\s*(?:个)?(?:小时|h\b)", text, re.IGNORECASE)
    if hours:
        value = _number(hours.group(1))
        return int(value * 60) if value is not None else None
    minutes = re.search(rf"({NUMBER_TOKEN})\s*分钟", text)
    if minutes:
        value = _number(minutes.group(1))
        return int(value) if value is not None else None
    return None


def _clause_context(text: str, start: int, end: int) -> tuple[str, str]:
    left = 0
    for marker in ("，", ",", "。", "；", ";", "！", "!", "？", "?", "但", "不过", "然而"):
        position = text.rfind(marker, 0, start)
        if position >= 0:
            left = max(left, position + len(marker))
    right = len(text)
    for marker in ("，", ",", "。", "；", ";", "！", "!", "？", "?", "但", "不过", "然而"):
        position = text.find(marker, end)
        if position >= 0:
            right = min(right, position)
    return text[left:start], text[end:right]


def _term_modality(text: str, start: int, end: int) -> str:
    prefix, suffix = _clause_context(text, start, end)
    context = prefix[-20:]
    if re.search(r"(?:没有|没|并未)(?:说|表示|要求).{0,6}(?:不要|不想|别)[^，,。；;]*$", context):
        return "ignored"
    double = DOUBLE_NEGATIVE_PREFIX.search(context)
    if double:
        marker = double.group(0)
        return "hard" if any(value in marker for value in ("不想错过", "不能没有", "不能不")) else "soft"
    if re.search(r"如果[^，,。；;]*(?:就)?(?:不要|不|别)[^，,。；;]*$", context):
        return "soft"
    if re.match(r"^\s*(?:去掉|取消|不要了|不安排了|不去了)", suffix):
        return "excluded"
    if WEAK_SUFFIX.search(suffix):
        return "soft"
    negative = NEGATIVE_PREFIX.search(context)
    positive_matches = list(
        re.finditer(
            r"(?:必须|一定要|只想|主要想|本人想|反而想|就是要|专门|(?<!不)想|(?<!不)要)[^，,。；;]*$",
            context,
        )
    )
    if negative and (not positive_matches or positive_matches[-1].start() < negative.start()):
        return "excluded"
    if WEAK_PREFIX.search(context):
        return "soft"
    return "hard"


def _term_is_negated(text: str, start: int, end: int | None = None) -> bool:
    end = start if end is None else end
    modality = _term_modality(text, start, end)
    return modality in {"excluded", "ignored"}


def _activity_modalities(text: str) -> tuple[list[str], list[dict[str, Any]], list[str]]:
    occurrences: list[tuple[int, int, str, str]] = []
    for tag_id, terms in ACTIVITY_TERMS.items():
        for term in terms:
            for match in re.finditer(re.escape(term), text):
                occurrences.append((match.start(), match.end(), tag_id, term))
    occurrences.sort()
    states: dict[str, tuple[int, str]] = {}
    processed: set[tuple[int, str]] = set()
    for start, end, tag_id, _term in occurrences:
        key = (start, tag_id)
        if key in processed:
            continue
        processed.add(key)
        states[tag_id] = (start, _term_modality(text, start, end))
    ordered = sorted(states.items(), key=lambda item: item[1][0])
    hard = [tag_id for tag_id, (_position, mode) in ordered if mode == "hard"]
    soft = [{"id": tag_id, "confidence": 0.68} for tag_id, (_position, mode) in ordered if mode == "soft"]
    excluded = [tag_id for tag_id, (_position, mode) in ordered if mode == "excluded"]
    return hard, soft, excluded


def _find_weighted(text: str, mapping: dict[str, tuple[str, ...]], *, dimension: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for tag_id, terms in mapping.items():
        matches = []
        for term in terms:
            for match in re.finditer(re.escape(term), text):
                if not _term_is_negated(text, match.start(), match.end()):
                    matches.append(term)
        if matches:
            result.append({"id": tag_id, "confidence": min(0.7 + 0.08 * len(set(matches)), 0.95)})
    return _weighted_tags(result, dimension=dimension)


@lru_cache(maxsize=1)
def _known_destinations() -> tuple[tuple[str, tuple[str, ...]], ...]:
    path = DEMO_DATA_DIR / "destination_profiles.jsonl"
    rows: list[tuple[str, tuple[str, ...]]] = []
    if path.exists():
        for physical_line in path.read_text(encoding="utf-8").split("\n"):
            line = physical_line.rstrip("\r")
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            name = str(item.get("name") or "").strip()
            aliases = tuple(str(value).strip() for value in item.get("aliases") or [] if str(value).strip())
            if name:
                rows.append((name, aliases))
    rows.sort(key=lambda item: max([len(item[0]), *(len(alias) for alias in item[1])]), reverse=True)
    return tuple(rows)


def _target_destination(text: str) -> str | None:
    for name, aliases in _known_destinations():
        if name in text or any(alias in text for alias in aliases):
            return name
    for place in IN_REGION_TERMS:
        if place in text:
            return place
    return None


def _route(text: str) -> tuple[str, str, str | None]:
    stripped = text.strip()
    if not stripped:
        return "not_travel", "chitchat", None
    if any(term in stripped for term in OUT_OF_REGION_TERMS):
        return "out_of_region", "unsupported", None
    if any(re.search(pattern, stripped, re.IGNORECASE) for pattern in CHITCHAT_PATTERNS):
        return "not_travel", "chitchat", None
    if any(term in stripped for term in UNSUPPORTED_TRANSACTION_TERMS):
        return "not_supported_yet", "unsupported", None
    target = _target_destination(stripped)
    if target and any(term in stripped for term in LOOKUP_TERMS):
        return "in_domain", "experience_lookup", target
    has_service = any(term in stripped for term in SERVICE_TERMS)
    has_sightseeing = any(term in stripped for term in SIGHTSEEING_TERMS)
    if has_service and not has_sightseeing:
        return "not_supported_yet", "unsupported", None
    return "in_domain", "destination_discovery", None


def _extract_origin(text: str) -> str | None:
    match = re.search(r"([\u4e00-\u9fff]{2,12})(?:市)?(?:出发|出发去|出发到)", text)
    if match:
        value = match.group(1)
        value = re.sub(r"^(?:我从|从)", "", value)
        value = re.sub(r"^(?:出发地)?(?:改成|换成|调整为)", "", value)
        for place in sorted(IN_REGION_TERMS, key=len, reverse=True):
            if place in value:
                return place
        return value[-6:]
    for place in IN_REGION_TERMS:
        if re.search(rf"{re.escape(place)}(?:出发|附近|周边)", text):
            return place
    return None


def _extract_transport_modes(text: str) -> tuple[list[str], list[str]]:
    positive: list[str] = []
    excluded: list[str] = []
    mentioned_modes = [mode for mode in TRANSPORT_MODES if mode in text]
    coordinated_options = len(mentioned_modes) >= 2 and bool(
        re.search(r"可以[^，,。；;]*(?:也可以|都可以|均可)", text)
        or re.search(r"(?:和|或)[^，,。；;]*(?:都可以|均可)", text)
    )
    for mode in TRANSPORT_MODES:
        for match in re.finditer(re.escape(mode), text):
            modality = _term_modality(text, match.start(), match.end())
            if modality in {"excluded", "ignored"}:
                excluded.append(mode)
            elif modality == "hard" or coordinated_options:
                positive.append(mode)
    return _dedupe(positive), _dedupe(excluded)


def _crowd_preference_cancelled(text: str) -> bool:
    return bool(
        re.search(r"(?:不要求|不用|不必|不再要求).{0,8}(?:人少|避开人潮|不拥挤|小众)", text)
        or re.search(r"(?:人少|拥挤|人潮|人挤人|小众).{0,8}(?:无所谓|不是硬伤|都行)", text)
    )


def _commercialization_preference_cancelled(text: str) -> bool:
    return bool(
        re.search(r"(?:不要求|不用|不必|不再要求).{0,8}(?:不商业化|商业化|原生态)", text)
        or re.search(r"(?:商业化|商业程度|原生态).{0,8}(?:无所谓|不是硬伤|都行)", text)
    )


def _extract_exclusions(text: str, activity_exclusions: list[str], transport_exclusions: list[str]) -> list[str]:
    exclusions: list[str] = [*activity_exclusions, *transport_exclusions]
    crowd_cancelled = _crowd_preference_cancelled(text)
    commercialization_cancelled = _commercialization_preference_cancelled(text)
    if not crowd_cancelled and (
        re.search(r"(?:不要|不想|避开|拒绝|别|远离|没那么|不太)\s*(?:去)?\s*(?:网红|打卡)", text)
        or re.search(r"(?:不要|不想|避开|拒绝|别|远离)\s*(?:去)?\s*(?:热门)?打卡", text)
        or "小众" in text
    ):
        exclusions.append("网红")
    if not crowd_cancelled and (
        any(term in text for term in ("避开人群", "避开人潮", "不要人多", "不想人多", "不拥挤", "人少", "清静", "人挤人"))
        or re.search(r"(?:不要|不想|避开|拒绝|别|远离)\s*(?:去)?\s*(?:人多|拥挤|人挤人)", text)
        or re.search(r"人多.{0,6}(?:就算了|不要了|不去了)", text)
        or re.search(r"别.{0,3}太多(?:人|游客)", text)
    ):
        exclusions.append("拥挤")
    if not commercialization_cancelled and (
        any(term in text for term in ("不商业化", "没那么商业化", "不要商业化", "别太商业", "原生态", "没开发", "未开发"))
        or re.search(r"(?:不要|不想|避开|拒绝|别|远离)\s*(?:去)?\s*(?:商业化|商业)", text)
        or re.search(r"(?:不要|不想|别)\s*太\s*(?:商业化|商业)", text)
    ):
        exclusions.append("商业化")
    return _dedupe(exclusions)


def _extract_evidence_aspects(text: str, plan: dict[str, Any]) -> list[str]:
    hard = plan["hard_constraints"]
    aspects: list[str] = []
    if plan["soft_preferences"]["mood"]:
        aspects.append("mood_fit")
    if not _crowd_preference_cancelled(text) and any(term in text for term in ("安静", "清静", "人少", "人多", "拥挤", "人群", "人潮", "网红", "小众", "冷门", "热门")):
        aspects.append("crowd")
    if not _commercialization_preference_cancelled(text) and any(term in text for term in ("商业化", "商业", "原生态", "开发")):
        aspects.append("commercialization")
    if any(term in text for term in ("海", "山", "景色", "风景", "湖", "森林")):
        aspects.append("scenery")
    if hard["must_have_activities"] or plan["soft_preferences"]["activity"]:
        aspects.append("activity")
    if hard["budget_max"] is not None:
        aspects.append("cost")
    if (
        hard["origin"]
        or hard["transport_modes"]
        or hard["travel_time_max"] is not None
        or any(term in text for term in ("交通方便", "交通便利", "方便到达", "好到达"))
    ):
        aspects.append("transport")
    if any(term in text for term in ("一个人", "独处", "独自")):
        aspects.append("solo")
    if any(term in text for term in ("拍照", "出片", "摄影")):
        aspects.append("photo")
    if any(term in text for term in ("季节", "几月", "天气", "下雨", "晴天")):
        aspects.append("weather_season")
    if any(term in text for term in ("住宿", "民宿", "酒店", "住一晚", "住得")):
        aspects.append("stay")
    if any(term in text for term in ("美食", "吃", "小吃", "餐厅", "探店")):
        aspects.append("food")
    return _dedupe(aspects)


def _semantic_query(text: str) -> str:
    """Conservative fallback rewrite: remove explicit negatives, add nothing."""
    result = text.strip()
    negative_terms = [term for terms in ACTIVITY_TERMS.values() for term in terms]
    negative_terms += ["网红", "打卡", "人多", "拥挤", "商业化", "自驾", "高铁", "轮渡", "地铁", "大巴", "公共交通"]
    for term in sorted(set(negative_terms), key=len, reverse=True):
        result = re.sub(rf"(?:不要|不想|不愿|别|拒绝|避免|避开|不考虑|排除)\s*(?:去)?\s*{re.escape(term)}", "", result)
    result = re.sub(r"[，,；;。]{2,}", "，", result).strip(" ，,；;。")
    return result


def _explicit_and_clear_slots(text: str, plan: dict[str, Any]) -> tuple[list[str], list[str], list[str]]:
    hard = plan["hard_constraints"]
    explicit: list[str] = []
    clear: list[str] = []
    replace: list[str] = []
    if hard["origin"] is not None:
        explicit.append("hard_constraints.origin")
    if hard["days_max"] is not None:
        explicit.append("hard_constraints.days_max")
    if hard["budget_max"] is not None:
        explicit.append("hard_constraints.budget_max")
    if hard["travel_time_max"] is not None:
        explicit.append("hard_constraints.travel_time_max")
    if hard["transport_modes"]:
        explicit.append("hard_constraints.transport_modes")
    if hard["must_have_activities"]:
        explicit.append("hard_constraints.must_have_activities")
    for dimension in ("mood", "vibe", "activity"):
        if plan["soft_preferences"][dimension]:
            explicit.append(f"soft_preferences.{dimension}")
    if plan["exclusions"]:
        explicit.append("exclusions")
    if plan["target_destination"]:
        explicit.extend(["scope", "task_type", "target_destination"])
    if any(term in text for term in OUT_OF_REGION_TERMS) or plan["scope"] != "in_domain":
        explicit.extend(["scope", "task_type"])
    if any(term in text for term in ("预算不限", "预算不设限", "不限制预算", "预算无所谓")):
        clear.append("hard_constraints.budget_max")
    if any(term in text for term in ("天数不限", "时间不限", "几天都行")):
        clear.append("hard_constraints.days_max")
    if any(term in text for term in ("交通不限", "方式不限", "怎么去都行")):
        clear.append("hard_constraints.transport_modes")
    if any(term in text for term in ("出发地改了", "出发地不限")):
        clear.append("hard_constraints.origin")
    if "改成" in text or "调整为" in text or "换成" in text:
        replace.extend(
            slot for slot in (
                "hard_constraints.transport_modes",
                "hard_constraints.must_have_activities",
                "soft_preferences.mood",
                "soft_preferences.vibe",
                "soft_preferences.activity",
            )
            if slot in explicit
        )
    if re.search(r"(?:还是|改为|改想|现在|现在更)想", text) and "soft_preferences.mood" in explicit:
        replace.append("soft_preferences.mood")
    semantic_signal = bool(
        plan["target_destination"]
        or any(plan["soft_preferences"].values())
        or hard["must_have_activities"]
        or plan["exclusions"]
    )
    if semantic_signal:
        explicit.extend(["semantic_query", "evidence_aspects"])
    elif any(hard[key] not in (None, [], "") for key in hard):
        explicit.append("evidence_aspects")
    return _dedupe(explicit), _dedupe(clear), _dedupe(replace)


def _activity_update_removals(text: str) -> list[str]:
    removed: list[str] = []
    for tag_id, terms in ACTIVITY_TERMS.items():
        for term in terms:
            for match in re.finditer(re.escape(term), text):
                _prefix, suffix = _clause_context(text, match.start(), match.end())
                if re.match(r"^\s*(?:不是必须|不是硬要求|不再是必须|可有可无)", suffix):
                    removed.append(tag_id)
    return _dedupe(removed)


def _cancelled_preference_operations(text: str) -> dict[str, list[str]]:
    remove_exclusions: list[str] = []
    remove_soft_tag_ids: list[str] = []
    remove_evidence_aspects: list[str] = []
    if _crowd_preference_cancelled(text):
        remove_exclusions.extend(["拥挤", "网红"])
        remove_soft_tag_ids.append("vibe_niche")
        remove_evidence_aspects.append("crowd")
    if _commercialization_preference_cancelled(text):
        remove_exclusions.append("商业化")
        remove_soft_tag_ids.append("vibe_unspoiled")
        remove_evidence_aspects.append("commercialization")
    return {
        "remove_exclusions": _dedupe(remove_exclusions),
        "remove_soft_tag_ids": _dedupe(remove_soft_tag_ids),
        "remove_evidence_aspects": _dedupe(remove_evidence_aspects),
    }


def _apply_form_values(plan: dict[str, Any], form_values: dict[str, Any] | None, *, protected_slots: Iterable[str] = ()) -> dict[str, Any]:
    if not form_values:
        return plan
    result = json.loads(json.dumps(plan, ensure_ascii=False))
    hard = result["hard_constraints"]
    protected = set(protected_slots)
    mapping = {
        "origin": ("hard_constraints.origin", lambda value: str(value).strip() or None),
        "days_max": ("hard_constraints.days_max", lambda value: _optional_int(value, minimum=1)),
        "days": ("hard_constraints.days_max", lambda value: _optional_int(value, minimum=1)),
        "budget_max": ("hard_constraints.budget_max", _optional_int),
        "budget": ("hard_constraints.budget_max", _optional_int),
        "travel_time_max": ("hard_constraints.travel_time_max", _optional_int),
    }
    for key, (slot, converter) in mapping.items():
        if key in form_values and form_values[key] not in (None, "") and slot not in protected:
            hard[slot.rsplit(".", 1)[1]] = converter(form_values[key])
    if "transport_modes" in form_values and "hard_constraints.transport_modes" not in protected:
        value = form_values["transport_modes"]
        if isinstance(value, str):
            value = re.split(r"[,，、/\s]+", value)
        hard["transport_modes"] = _dedupe(mode for mode in value or [] if mode in TRANSPORT_MODES)
    return result


def build_rule_query_plan(raw_query: str, *, form_values: dict[str, Any] | None = None) -> dict[str, Any]:
    """Conservative deterministic Query Plan used for fallback and tests."""
    text = str(raw_query or "").strip()
    scope, task_type, target = _route(text)
    if scope != "in_domain":
        plan = empty_query_plan()
        plan.update({"scope": scope, "task_type": task_type})
        return normalize_query_plan(plan, raw_query=text)

    hard_activities, soft_activities, activity_exclusions = _activity_modalities(text)
    transport_modes, transport_exclusions = _extract_transport_modes(text)
    plan = empty_query_plan()
    plan.update(
        {
            "scope": scope,
            "task_type": task_type,
            "target_destination": target,
            "hard_constraints": {
                "origin": _extract_origin(text),
                "days_max": _extract_days(text),
                "budget_max": _extract_budget(text),
                "travel_time_max": _extract_travel_minutes(text),
                "transport_modes": transport_modes,
                "must_have_activities": hard_activities,
            },
            "exclusions": _extract_exclusions(text, activity_exclusions, transport_exclusions),
            "semantic_query": _semantic_query(text),
            "soft_preferences": {
                "mood": _find_weighted(text, MOOD_TERMS, dimension="mood"),
                "vibe": _find_weighted(text, VIBE_TERMS, dimension="vibe"),
                "activity": soft_activities,
            },
        }
    )
    plan["evidence_aspects"] = _extract_evidence_aspects(text, plan)
    explicit, clear, _replace = _explicit_and_clear_slots(text, plan)
    plan = _apply_form_values(plan, form_values, protected_slots=[*explicit, *clear])
    return normalize_query_plan(plan, raw_query=text)


def build_rule_query_delta(raw_query: str, *, form_values: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return a merge-safe turn delta.

    A complete Query Plan cannot distinguish an unmentioned null from an
    explicit reset. The envelope records those operations separately and is
    intentionally not part of the Query Plan schema.
    """
    plan = build_rule_query_plan(raw_query)
    explicit, clear, replace = _explicit_and_clear_slots(str(raw_query or ""), plan)
    activity_removals = _activity_update_removals(str(raw_query or ""))
    operations = _cancelled_preference_operations(str(raw_query or ""))
    if "act_town" in activity_removals:
        operations["remove_soft_tag_ids"] = _dedupe(
            [*operations["remove_soft_tag_ids"], "vibe_ancient"]
        )
    return {
        "query_plan": _apply_form_values(plan, form_values, protected_slots=[*explicit, *clear]),
        "explicit_slots": explicit,
        "clear_slots": clear,
        "replace_slots": replace,
        "remove_activity_ids": activity_removals,
        **operations,
        "raw_query": str(raw_query or "").strip(),
    }


def _extract_json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if value is None:
        raise ValueError("empty planner output")
    text = str(value).strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.IGNORECASE | re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError("planner output must be a JSON object")
    return payload


def _rewrite_adds_guarded_fact(raw_query: str, semantic_query: str) -> bool:
    """Detect high-risk additions while allowing harmless synonym expansion."""
    raw = raw_query.lower()
    rewrite = semantic_query.lower()
    guarded_terms = [
        *TRANSPORT_MODES,
        *(term for terms in ACTIVITY_TERMS.values() for term in terms),
        *(term for terms in VIBE_TERMS.values() for term in terms),
        *OUT_OF_REGION_TERMS,
        *IN_REGION_TERMS,
        *(name for name, _aliases in _known_destinations()),
    ]
    if any(term.lower() in rewrite and term.lower() not in raw for term in guarded_terms):
        return True
    raw_numbers = set(re.findall(r"\d+(?:\.\d+)?", raw))
    rewrite_numbers = set(re.findall(r"\d+(?:\.\d+)?", rewrite))
    return not rewrite_numbers.issubset(raw_numbers)


def _correct_planner_plan(plan: dict[str, Any], raw_query: str) -> tuple[dict[str, Any], list[str], list[str]]:
    """Apply deterministic safety corrections without inventing new slots."""
    corrected = json.loads(json.dumps(plan, ensure_ascii=False))
    rule = build_rule_query_plan(raw_query)
    explicit, clear, _replace = _explicit_and_clear_slots(raw_query, rule)

    if rule["scope"] != "in_domain" or rule["task_type"] == "experience_lookup":
        corrected["scope"] = rule["scope"]
        corrected["task_type"] = rule["task_type"]
        corrected["target_destination"] = rule["target_destination"]

    rule_hard = rule["hard_constraints"]
    corrected["hard_constraints"] = json.loads(json.dumps(rule_hard, ensure_ascii=False))

    corrected["soft_preferences"]["activity"] = rule["soft_preferences"]["activity"]
    for dimension in ("mood", "vibe"):
        if rule["soft_preferences"][dimension]:
            corrected["soft_preferences"][dimension] = rule["soft_preferences"][dimension]
        elif not any(
            term in raw_query
            for terms in (MOOD_TERMS if dimension == "mood" else VIBE_TERMS).values()
            for term in terms
        ):
            corrected["soft_preferences"][dimension] = []
    corrected["exclusions"] = _dedupe([*corrected["exclusions"], *rule["exclusions"]])
    negative_activities = set(corrected["exclusions"]) & ALLOWED_TAG_IDS["activity"]
    corrected["hard_constraints"]["must_have_activities"] = [
        value for value in corrected["hard_constraints"]["must_have_activities"] if value not in negative_activities
    ]
    corrected["soft_preferences"]["activity"] = [
        item for item in corrected["soft_preferences"]["activity"] if item.get("id") not in negative_activities
    ]
    corrected["evidence_aspects"] = _dedupe([*corrected["evidence_aspects"], *rule["evidence_aspects"]])
    if _rewrite_adds_guarded_fact(raw_query, str(corrected.get("semantic_query") or "")):
        corrected["semantic_query"] = rule["semantic_query"]
    return normalize_query_plan(corrected, raw_query=raw_query), explicit, clear


def parse_query_plan_output(
    planner_output: Any,
    *,
    raw_query: str,
    form_values: dict[str, Any] | None = None,
    schema_path: Path | None = None,
) -> dict[str, Any]:
    """Validate untrusted planner output; any illegal output uses rule fallback."""
    try:
        payload = _extract_json_object(planner_output)
        _raise_first_schema_error(payload, schema_path)
        normalized = normalize_query_plan(payload, raw_query=raw_query, schema_path=schema_path)
        normalized, explicit, clear = _correct_planner_plan(normalized, raw_query)
        normalized = _apply_form_values(normalized, form_values, protected_slots=[*explicit, *clear])
        return normalize_query_plan(normalized, raw_query=raw_query, schema_path=schema_path)
    except (TypeError, ValueError, json.JSONDecodeError):
        return build_rule_query_plan(raw_query, form_values=form_values)


def should_enter_retrieval(plan: dict[str, Any]) -> bool:
    return (
        plan.get("scope") == "in_domain"
        and plan.get("task_type") in {"destination_discovery", "experience_lookup"}
    )
