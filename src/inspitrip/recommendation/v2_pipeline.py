from __future__ import annotations

import csv
import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterable

from jsonschema import Draft7Validator, FormatChecker


ENTITY_PREFIX = {
    "destination": "DEST",
    "experience": "EXP",
    "service": "SVC",
    "transport_node": "TRN",
}

SERVICE_CATEGORIES = {"cafe", "food", "stay"}
SERVICE_RE = re.compile(r"(酒店|民宿|客栈|度假村|餐厅|饭店|排档|咖啡|书店|茶馆|小吃店)")
TRANSPORT_RE = re.compile(r"(码头|客运站|火车站|高铁站|汽车站|机场|停车场|换乘中心)")
EXPERIENCE_RE = re.compile(
    r"(沙滩|观景台|灯塔|绝壁|索道|栈道|古道|步道|公路|打卡点|日出点|"
    r"博物馆|美术馆|纪念馆|寺|庙|公园|乐园|牧场|大坝|桥|洞|瀑布|营地|无人村)$"
)
DESTINATION_RE = re.compile(
    r"(岛|列岛|古镇|古村|渔村|村|镇|县|市|区|半岛|湿地|竹海|草原|景区)$"
)
KNOWN_DESTINATIONS = {
    "嵊泗", "嵊泗岛", "嵊泗本岛", "东极岛", "枸杞岛", "花鸟岛", "嵊山岛",
    "东福山岛", "庙子湖", "庙子湖岛", "青浜岛", "衢山岛", "岱山岛",
    "朱家尖", "普陀山", "连岛", "连云港", "温岭", "雁荡山", "云台山",
    "海上云台山", "莫干山", "安吉", "临海", "舟山", "舟山本岛"
}

PROTECTED_POSITIVE_PATTERNS = (
    re.compile(
        r"(?:没有|没那么|没怎么|并不|不算|不太|不怎么|无需|不用)"
        r"[^，。；！？\n]{0,8}(?:商业化|拥挤|人挤人|排队|人潮)"
    ),
    re.compile(r"(?:避开|远离)[^，。；！？\n]{0,8}(?:商业化|拥挤|人挤人|排队|人潮)"),
    re.compile(r"(?:不|没)(?:算|太|怎么|那么|很)?贵"),
    re.compile(r"(?:不|没)(?:算|太|怎么|那么|很)?(?:堵|堵车|折腾|不便|麻烦)"),
    re.compile(r"(?:人|游客)(?:并)?不多"),
)

NON_CROWD_GROUP_RE = re.compile(r"人多(?:可|就|建议|的话|可以)[^，。；！？\n]{0,12}(?:包车|拼车|组团)")

NEGATED_POSITIVE_RE = re.compile(
    r"(?:不|没)(?:太|怎么|是很|那么|很)?"
    r"(?:推荐|值得|安静|治愈|放松|漂亮|好看|舒服|壮观|方便|好吃|适合|喜欢)"
)

NEGATIVE_PATTERNS = tuple(
    re.compile(pattern)
    for pattern in (
        r"不(?:太|怎么|很)?推荐",
        r"不会再去|不会去第二次|踩雷|避雷|难吃|宰客|将就吃",
        r"拥挤|人挤人|排队|商业化",
        r"人多|游客多|太虐|折腾|不便|不方便|麻烦|堵车|限流|售罄|售完|抢不到",
        r"(?:太|很|比较|有点|偏)贵",
        r"物价(?:偏|较|稍|有点)?高|价格(?:偏|较|稍|有点)?高|消费(?:偏|较|稍|有点)?高|"
        r"没有价格表|没必要|没有必要|拍不出|浑浊",
        r"(?:^|[，,：:；;])贵(?:$|[，,。；;])",
        r"危险|关闭|停业|脏|失望|不值|不好",
        r"味道普通|味道一般",
        r"(?:体验|味道|环境|服务|卫生|交通|住宿|性价比)"
        r"(?:很|太|比较|有点|相当|非常)?差",
    )
)

POSITIVE_PATTERNS = tuple(
    re.compile(pattern)
    for pattern in (
        r"推荐|惊喜|值得|安静|治愈|放松|漂亮|好看|舒服",
        r"人少|小众|原生态|壮观|震撼|方便|好吃|适合|喜欢",
        r"超美|很美|更蓝|划算|新鲜|惬意|轻松",
    )
)

PROFILE_ENTITY_TYPES = {"destination", "experience"}
LIMITATION_ASPECTS = {
    "crowd", "commercialization", "transport", "cost", "safety", "weather_season"
}
ITINERARY_HEADING_RE = re.compile(
    r"^(?:(?:DAY|D)\s*\d+\s*[【\[].*[】\]]|第[一二三四五六七八九十]+天\s*[：:]?.*)$",
    re.IGNORECASE,
)
ACTION_ONLY_RE = re.compile(r"^(?:登岛|抵达|到达|前往|返程|出发|入住|途经|经过)[^，。；！？]{0,12}$")
ROUTE_FRAGMENT_RE = re.compile(r"(?:→|->|—>|⇒|⇢)")
TIME_ITINERARY_RE = re.compile(r"^\d{1,2}[:：]\d{2}\s*[^，。；！？\n]{0,40}$")
POSITIVE_SUMMARY_MARKERS = (
    "适合", "人少", "安静", "治愈", "放空", "发呆", "舒服", "轻松", "惬意",
    "浪漫", "小众", "原生态", "商业化", "人挤人", "人潮", "度假", "慢生活",
    "氛围", "超美", "很美", "更蓝", "风景", "景色", "日出", "日落", "拍照",
    "出片", "值得", "震撼", "清净", "悠闲", "凉爽", "自由",
)
LIMITATION_SUMMARY_MARKERS = (
    "拥挤", "人挤人", "排队", "商业化", "折腾", "不便", "很贵", "太贵", "危险",
    "关闭", "停业", "天气", "下雨", "台风", "暴晒", "晕船", "交通", "预算",
    "门票", "节假日", "堵车", "限流", "预约", "慎入", "不推荐", "不好", "失望",
    "宰客", "不值", "人多", "游客多", "不方便", "麻烦", "售罄", "抢不到",
)

ASPECT_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("transport", ("交通", "高铁", "自驾", "轮渡", "船票", "公交", "接驳", "打车", "开车")),
    ("cost", ("预算", "人均", "价格", "门票", "花费", "费用", "贵", "便宜", "免费")),
    ("crowd", ("人少", "人不多", "游客多", "拥挤", "排队", "人挤人", "避开人群", "节假日")),
    ("commercialization", ("商业化", "原生态", "开发", "网红", "宰客", "市井")),
    ("safety", ("危险", "涨潮", "落石", "夜路", "封路", "安全", "湿滑")),
    ("weather_season", ("天气", "下雨", "雨天", "晴天", "阴天", "多云", "夏天", "冬天", "春天", "秋天", "季节")),
    ("stay", ("民宿", "酒店", "住宿", "房间", "海景房")),
    ("food", ("美食", "海鲜", "小吃", "餐厅", "好吃", "难吃")),
    ("photo", ("拍照", "出片", "机位", "摄影")),
    ("solo", ("一个人", "独处", "单人", "solo")),
    ("scenery", ("风景", "看海", "日出", "日落", "夕阳", "山景", "海景", "景色")),
    ("activity", ("徒步", "骑行", "赶海", "露营", "泡温泉", "看展")),
    ("mood_fit", ("放空", "治愈", "松弛", "浪漫", "怀旧", "发呆", "自由")),
]


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")))
            handle.write("\n")
    temp.replace(path)


def load_aliases(path: Path | None) -> dict[str, list[str]]:
    aliases: dict[str, list[str]] = defaultdict(list)
    if not path or not path.exists():
        return {}
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            raw = (row.get("raw_name") or "").strip()
            canonical = (row.get("canonical_name") or "").strip()
            if raw and canonical and raw != canonical and raw not in aliases[canonical]:
                aliases[canonical].append(raw)
    return dict(aliases)


def load_alias_rules(path: Path | None) -> dict[str, dict[str, str]]:
    if not path or not path.exists():
        return {}
    rules: dict[str, dict[str, str]] = {}
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            raw = (row.get("raw_name") or "").strip()
            canonical = (row.get("canonical_name") or "").strip()
            if raw and canonical:
                rules[raw] = {
                    "canonical_name": canonical,
                    "city": (row.get("city") or "").strip().removesuffix("市"),
                    "province": (row.get("province") or "").strip(),
                }
    return rules


def load_taxonomy_names(path: Path) -> dict[str, str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {
        leaf["leaf_id"]: leaf["name"]
        for dimension in payload["dimensions"]
        for leaf in dimension["leaves"]
    }


def _stable_entity_id(identity_key: str, entity_type: str) -> str:
    digest = hashlib.sha1(identity_key.encode("utf-8")).hexdigest()[:10].upper()
    return f"{ENTITY_PREFIX[entity_type]}_{digest}"


def _stable_claim_id(evidence_id: str, index: int, quote: str) -> str:
    digest = hashlib.sha1(f"{evidence_id}|{index}|{quote}".encode("utf-8")).hexdigest()[:12].upper()
    return f"CLM_{digest}"


def classify_entity(row: dict[str, Any], overrides: dict[str, Any] | None = None) -> str:
    overrides = overrides or {}
    poi_id = str(row.get("poi_id") or row.get("legacy_poi_id") or "")
    name = str(row.get("name") or row.get("canonical_name") or "").strip()
    override = overrides.get(poi_id) or overrides.get(name) or {}
    if override.get("entity_type") in ENTITY_PREFIX:
        return override["entity_type"]
    category = str(row.get("category") or "").lower()
    if category in SERVICE_CATEGORIES or SERVICE_RE.search(name):
        return "service"
    if TRANSPORT_RE.search(name):
        return "transport_node"
    if EXPERIENCE_RE.search(name):
        return "experience"
    if name and (name in KNOWN_DESTINATIONS or name == row.get("city") or DESTINATION_RE.search(name)):
        return "destination"
    return "experience"


def build_entities(
    poi_rows: list[dict[str, Any]],
    mention_rows: list[dict[str, Any]],
    *,
    aliases: dict[str, list[str]] | None = None,
    alias_rules: dict[str, dict[str, str]] | None = None,
    overrides: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    aliases = aliases or {}
    alias_rules = alias_rules or {}
    overrides = overrides or {}
    entities: list[dict[str, Any]] = []
    entity_id_by_poi: dict[str, str] = {}
    entity_type_by_id: dict[str, str] = {}
    grouped_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in poi_rows:
        poi_id = str(row.get("poi_id") or "")
        if not poi_id:
            continue
        original_name = str(row.get("name") or "").strip()
        alias_rule = alias_rules.get(original_name, {})
        canonical_name = alias_rule.get("canonical_name") or original_name
        normalized_row = dict(row)
        normalized_row["name"] = canonical_name
        normalized_row["city"] = (
            alias_rule.get("city") or str(row.get("city") or "").removesuffix("市")
        )
        normalized_row["province"] = alias_rule.get("province") or row.get("province")
        entity_type = classify_entity(normalized_row, overrides)
        identity_key = "|".join(
            [
                str(normalized_row.get("province") or ""),
                str(normalized_row.get("city") or ""),
                canonical_name,
                entity_type,
            ]
        )
        entity_id = _stable_entity_id(identity_key, entity_type)
        entity_id_by_poi[poi_id] = entity_id
        entity_type_by_id[entity_id] = entity_type
        normalized_row["_original_name"] = original_name
        grouped_rows[entity_id].append(normalized_row)

    # Only unambiguous single-destination notes create automatic parent links.
    note_entities: dict[str, set[str]] = defaultdict(set)
    for mention in mention_rows:
        poi_id = str(mention.get("poi_id") or "")
        if poi_id in entity_id_by_poi:
            note_entities[str(mention.get("note_id") or "")].add(entity_id_by_poi[poi_id])
    parent_votes: dict[str, Counter[str]] = defaultdict(Counter)
    for entity_ids in note_entities.values():
        destinations = [entity_id for entity_id in entity_ids if entity_type_by_id.get(entity_id) == "destination"]
        if len(destinations) != 1:
            continue
        parent = destinations[0]
        for child in entity_ids:
            if child != parent and entity_type_by_id.get(child) != "destination":
                parent_votes[child][parent] += 1

    for entity_id, rows in grouped_rows.items():
        rows.sort(key=lambda row: int(row.get("independent_sources") or 0), reverse=True)
        row = rows[0]
        poi_ids = sorted(str(item.get("poi_id") or "") for item in rows if item.get("poi_id"))
        poi_id = poi_ids[0]
        entity_type = entity_type_by_id[entity_id]
        name = str(row.get("name") or "").strip()
        override = overrides.get(poi_id) or overrides.get(name) or {}
        parent_id: str | None = None
        explicit_parent = override.get("parent_legacy_poi_id")
        if explicit_parent in entity_id_by_poi:
            parent_id = entity_id_by_poi[explicit_parent]
        elif parent_votes.get(entity_id):
            parent_id, _votes = parent_votes[entity_id].most_common(1)[0]
        entity_aliases = set(aliases.get(name, []))
        entity_aliases.update(
            item.get("_original_name") for item in rows if item.get("_original_name") != name
        )
        entity = {
            "entity_id": entity_id,
            "legacy_poi_id": poi_id,
            "legacy_poi_ids": poi_ids,
            "entity_type": entity_type,
            "parent_id": parent_id,
            "name": name,
            "aliases": sorted(value for value in entity_aliases if value),
            "city": str(row.get("city") or ""),
            "province": str(row.get("province") or ""),
            "category": str(row.get("category") or ""),
            "longitude": row.get("longitude"),
            "latitude": row.get("latitude"),
            "map_poi_id": row.get("map_poi_id"),
            "status": str(row.get("status") or "active"),
        }
        entities.append(entity)
    return sorted(entities, key=lambda row: row["entity_id"]), entity_id_by_poi


def _parse_date(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value or "")
    except ValueError:
        return None


def calculate_source_quality(evidence: dict[str, Any], today: date) -> float:
    if evidence.get("is_suspected_ad"):
        return 0.1
    quality = 0.55
    published = _parse_date(str(evidence.get("publish_date") or ""))
    if published:
        age = max((today - published).days, 0)
        if age <= 365:
            quality += 0.20
        elif age <= 730:
            quality += 0.10
    if evidence.get("key_quote"):
        quality += 0.10
    engagement = (
        int(evidence.get("likes") or 0)
        + 2 * int(evidence.get("collects") or 0)
        + 3 * int(evidence.get("comments") or 0)
    )
    quality += min(math.log10(engagement + 1) / 30.0, 0.10)
    return round(min(quality, 1.0), 4)


def infer_polarity(text: str) -> str:
    normalized = re.sub(r"\s+", "", text or "")
    protected_positive = any(pattern.search(normalized) for pattern in PROTECTED_POSITIVE_PATTERNS)
    negative_text = normalized
    for pattern in PROTECTED_POSITIVE_PATTERNS:
        negative_text = pattern.sub(" ", negative_text)
    negative_text = NON_CROWD_GROUP_RE.sub(" ", negative_text)
    positive_text = NEGATED_POSITIVE_RE.sub(" ", normalized)
    negative = any(pattern.search(negative_text) for pattern in NEGATIVE_PATTERNS)
    positive = protected_positive or any(pattern.search(positive_text) for pattern in POSITIVE_PATTERNS)
    if positive and negative:
        return "mixed"
    if negative:
        return "negative"
    if positive:
        return "positive"
    return "neutral"


def resolve_polarity(text: str, declared: str | None = None) -> str:
    inferred = infer_polarity(text)
    if inferred != "neutral":
        return inferred
    if declared in {"positive", "negative", "mixed", "neutral"}:
        return declared
    return "neutral"


def infer_aspect(text: str, activity: list[str], mood: list[str], vibe: list[str]) -> str:
    for aspect, words in ASPECT_KEYWORDS:
        if any(word in text for word in words):
            return aspect
    if activity:
        return "activity"
    if mood:
        return "mood_fit"
    if vibe:
        return "scenery"
    return "other"


def infer_conditions(text: str) -> dict[str, Any]:
    conditions: dict[str, Any] = {}
    normalized = re.sub(r"\s+", "", text or "")
    if "工作日" in normalized or "周一至周五" in normalized:
        conditions["weekday"] = True
    if "周末" in normalized or "双休日" in normalized:
        conditions["weekend"] = True
    if any(marker in normalized for marker in ("节假日", "假期", "小长假", "黄金周")):
        conditions["holiday"] = True
    if "一个人" in normalized or "单人" in normalized or "solo" in normalized.lower():
        conditions["companion"] = "solo"
    elif any(marker in normalized for marker in ("情侣", "两个人", "两人", "对象")):
        conditions["companion"] = "couple"
    elif any(marker in normalized for marker in ("亲子", "带娃", "小朋友", "孩子")):
        conditions["companion"] = "family_with_children"
    elif any(marker in normalized for marker in ("老人", "长辈", "父母")):
        conditions["companion"] = "with_seniors"
    seasons = {
        "spring": ("春天", "春季", "春日"),
        "summer": ("夏天", "夏季", "盛夏"),
        "autumn": ("秋天", "秋季", "秋日"),
        "winter": ("冬天", "冬季", "寒冬"),
    }
    for season, markers in seasons.items():
        if any(marker in normalized for marker in markers):
            conditions["season"] = season
            break
    if "晴" in normalized:
        conditions["weather"] = "sunny"
    elif "雨" in normalized:
        conditions["weather"] = "rainy"
    elif any(marker in normalized for marker in ("阴天", "多云")):
        conditions["weather"] = "cloudy"
    time_markers = (
        ("sunrise", ("日出",)),
        ("sunset", ("日落", "夕阳")),
        ("early_morning", ("清晨", "一早", "早起")),
        ("morning", ("早上", "上午")),
        ("noon", ("中午",)),
        ("afternoon", ("下午",)),
        ("evening", ("傍晚", "晚上")),
        ("night", ("深夜", "夜里", "夜晚")),
    )
    for value, markers in time_markers:
        if any(marker in normalized for marker in markers):
            conditions["time_of_day"] = value
            break
    if "淡季" in normalized or "错峰" in normalized:
        conditions["travel_period"] = "off_peak"
    elif "旺季" in normalized or "高峰期" in normalized:
        conditions["travel_period"] = "peak"
    if "提前" in normalized and any(marker in normalized for marker in ("预约", "买票", "订票", "抢票")):
        conditions["advance_booking"] = True
    if "自驾" in normalized or "开车" in normalized:
        conditions["transport_mode"] = "driving"
    elif any(marker in normalized for marker in ("公交", "公共交通", "高铁", "地铁")):
        conditions["transport_mode"] = "public_transport"
    return conditions


def build_claims(
    evidence_rows: list[dict[str, Any]],
    mention_rows: list[dict[str, Any]],
    entities: list[dict[str, Any]],
    entity_id_by_poi: dict[str, str],
    *,
    today: date,
) -> list[dict[str, Any]]:
    mention_by_evidence = {
        str(row.get("evidence_id") or ""): row
        for row in mention_rows
        if row.get("evidence_id")
    }
    entity_by_id = {row["entity_id"]: row for row in entities}
    claims: list[dict[str, Any]] = []
    for evidence in evidence_rows:
        if evidence.get("is_suspected_ad"):
            continue
        evidence_id = str(evidence.get("evidence_id") or "")
        poi_id = str(evidence.get("poi_id") or "")
        entity_id = entity_id_by_poi.get(poi_id)
        if not entity_id:
            continue
        entity = entity_by_id[entity_id]
        destination_id = entity_id if entity["entity_type"] == "destination" else entity.get("parent_id")
        mention = mention_by_evidence.get(evidence_id, {})
        extracted_claims = mention.get("claims") or evidence.get("claims") or []
        if not extracted_claims:
            quote = str(evidence.get("key_quote") or mention.get("key_quote") or "").strip()
            if not quote:
                continue
            fallback_mood = list(mention.get("mood") or [])
            fallback_vibe = list(mention.get("vibe") or [])
            fallback_activity = list(mention.get("activity") or [])
            fallback_polarity = infer_polarity(quote)
            if fallback_polarity == "neutral" and (
                fallback_mood or fallback_vibe or fallback_activity
            ):
                fallback_polarity = "positive"
            extracted_claims = [
                {
                    "aspect": infer_aspect(
                        quote,
                        list(mention.get("activity") or []),
                        list(mention.get("mood") or []),
                        list(mention.get("vibe") or []),
                    ),
                    "polarity": fallback_polarity,
                    "claim": quote,
                    "key_quote": quote,
                    "conditions": infer_conditions(quote),
                    "mood": fallback_mood,
                    "vibe": fallback_vibe,
                    "activity": fallback_activity,
                }
            ]
        for index, extracted in enumerate(extracted_claims):
            quote = str(extracted.get("key_quote") or extracted.get("claim") or "").strip()[:120]
            claim_text = str(extracted.get("claim") or quote).strip()[:240]
            if not quote or not claim_text:
                continue
            claims.append(
                {
                    "claim_id": _stable_claim_id(evidence_id, index, quote),
                    "evidence_id": evidence_id,
                    "entity_id": entity_id,
                    "destination_id": destination_id,
                    "note_id": str(mention.get("note_id") or evidence.get("note_id") or ""),
                    "aspect": str(extracted.get("aspect") or "other"),
                    "polarity": resolve_polarity(
                        claim_text,
                        str(extracted.get("polarity") or "") or None,
                    ),
                    "claim": claim_text,
                    "key_quote": quote,
                    "mood": list(extracted.get("mood") or mention.get("mood") or []),
                    "vibe": list(extracted.get("vibe") or mention.get("vibe") or []),
                    "activity": list(extracted.get("activity") or mention.get("activity") or []),
                    "conditions": dict(extracted.get("conditions") or infer_conditions(claim_text)),
                    "author_hash": str(evidence.get("author_hash") or mention.get("author_hash") or ""),
                    "publish_date": str(evidence.get("publish_date") or ""),
                    "collected_date": str(evidence.get("collected_date") or today.isoformat()),
                    "source_quality": calculate_source_quality(evidence, today),
                    "is_suspected_ad": bool(evidence.get("is_suspected_ad")),
                    "source_url": str(evidence.get("source_url") or ""),
                }
            )
    return claims


def _polarity_factor(polarity: str) -> float:
    return {"positive": 1.0, "mixed": 0.45, "neutral": 0.25, "negative": -0.75}.get(polarity, 0.0)


def _tag_scores(
    claims: list[dict[str, Any]], field: str, fallback_tags: list[str]
) -> dict[str, float]:
    per_tag_author: dict[str, dict[str, float]] = defaultdict(dict)
    total_author_weight: dict[str, float] = defaultdict(float)
    for claim in claims:
        author = claim.get("author_hash") or claim["evidence_id"]
        base = float(claim.get("source_quality") or 0) * _polarity_factor(claim.get("polarity", "neutral"))
        for tag in claim.get(field, []):
            current = per_tag_author[tag].get(author)
            if current is None or abs(base) > abs(current):
                per_tag_author[tag][author] = base
            total_author_weight[author] = max(total_author_weight[author], float(claim.get("source_quality") or 0))
    denominator = sum(total_author_weight.values()) or 1.0
    scores = {
        tag: round(max(0.0, min(1.0, sum(author_scores.values()) / denominator)), 4)
        for tag, author_scores in per_tag_author.items()
    }
    for tag in fallback_tags:
        scores.setdefault(tag, 0.5)
    return dict(sorted(scores.items(), key=lambda item: (-item[1], item[0])))


def _freshness_score(claims: list[dict[str, Any]], today: date) -> float:
    dates = [_parse_date(str(row.get("publish_date") or "")) for row in claims]
    dates = [value for value in dates if value]
    if not dates:
        return 0.25
    age = max((today - max(dates)).days, 0)
    if age <= 180:
        return 1.0
    if age <= 365:
        return 0.85
    if age <= 730:
        return 0.55
    return 0.25


def _private_discovery_value(source_count: int) -> float:
    if source_count <= 0:
        return 0.2
    if source_count <= 2:
        return 0.9
    if source_count <= 5:
        return 0.75
    if source_count <= 10:
        return 0.55
    return 0.35


def _is_informative_claim(claim: dict[str, Any]) -> bool:
    text = re.sub(r"\s+", "", str(claim.get("claim") or ""))
    if len(text) < 6:
        return False
    if (
        ITINERARY_HEADING_RE.fullmatch(text)
        or ACTION_ONLY_RE.fullmatch(text)
        or ROUTE_FRAGMENT_RE.search(text)
        or TIME_ITINERARY_RE.fullmatch(text)
    ):
        return False
    return True


def _is_positive_summary_worthy(claim: dict[str, Any]) -> bool:
    text = str(claim.get("claim") or "")
    return _is_informative_claim(claim) and any(marker in text for marker in POSITIVE_SUMMARY_MARKERS)


def _is_limitation_summary_worthy(claim: dict[str, Any]) -> bool:
    text = str(claim.get("claim") or "")
    return _is_informative_claim(claim) and any(marker in text for marker in LIMITATION_SUMMARY_MARKERS)


def _summary_claim_score(claim: dict[str, Any]) -> tuple[float, int, int, int]:
    text = str(claim.get("claim") or "")
    tag_support = len(claim.get("mood") or []) + len(claim.get("vibe") or [])
    descriptive = sum(
        marker in text
        for marker in (
            "安静", "人少", "治愈", "放空", "舒服", "浪漫", "日出", "日落",
            "风景", "海", "山", "原生态", "小众", "震撼", "适合",
        )
    )
    return (
        float(claim.get("source_quality") or 0),
        tag_support,
        descriptive,
        min(len(text), 120),
    )


def build_facts(
    poi_by_id: dict[str, dict[str, Any]], entities: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []
    for entity in entities:
        if entity["entity_type"] != "destination":
            continue
        candidates = [poi_by_id.get(poi_id, {}) for poi_id in entity.get("legacy_poi_ids", [])]
        candidates = [row for row in candidates if row]
        poi = max(
            candidates or [{}],
            key=lambda row: (
                row.get("budget_confidence") in {"高", "中"},
                int(row.get("independent_sources") or 0),
            ),
        )
        budget_confidence = poi.get("budget_confidence")
        budget_typical = poi.get("budget_per_capita")
        transport = list(poi.get("transport") or [])
        duration = poi.get("duration_days")
        facts.append(
            {
                "destination_id": entity["entity_id"],
                "duration_min": duration,
                "duration_max": duration,
                "duration_source": poi.get("duration_source"),
                "budget_min": poi.get("budget_min"),
                "budget_typical": budget_typical,
                "budget_max": poi.get("budget_max"),
                "budget_basis": poi.get("budget_basis"),
                "budget_confidence": budget_confidence,
                "budget_filterable": bool(
                    budget_typical is not None and budget_confidence in {"高", "中"}
                ),
                "requires_ferry": "轮渡" in transport,
                "best_season": poi.get("best_season"),
                "operational_status": poi.get("status") or "unknown",
                "reachable_from": list(poi.get("reachable_from") or []),
                "travel_time_min": poi.get("travel_time_min"),
                "travel_time_source": poi.get("travel_time_source"),
                "transport": transport,
            }
        )
    return facts


def build_profiles(
    entities: list[dict[str, Any]],
    claims: list[dict[str, Any]],
    poi_by_id: dict[str, dict[str, Any]],
    facts: list[dict[str, Any]],
    taxonomy_names: dict[str, str],
    *,
    today: date,
) -> list[dict[str, Any]]:
    entity_type_by_id = {
        entity["entity_id"]: entity["entity_type"]
        for entity in entities
    }
    claims_by_destination: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for claim in claims:
        if claim.get("destination_id"):
            claims_by_destination[claim["destination_id"]].append(claim)
    fact_by_destination = {row["destination_id"]: row for row in facts}
    profiles: list[dict[str, Any]] = []
    for entity in entities:
        if entity["entity_type"] != "destination":
            continue
        destination_id = entity["entity_id"]
        relevant = [
            claim
            for claim in claims_by_destination.get(destination_id, [])
            if entity_type_by_id.get(claim.get("entity_id")) in PROFILE_ENTITY_TYPES
        ]
        candidate_pois = [poi_by_id.get(poi_id, {}) for poi_id in entity.get("legacy_poi_ids", [])]
        candidate_pois = [row for row in candidate_pois if row]
        poi = max(
            candidate_pois or [{}],
            key=lambda row: int(row.get("independent_sources") or 0),
        )
        mood_scores = _tag_scores(relevant, "mood", list(poi.get("mood") or []))
        vibe_scores = _tag_scores(relevant, "vibe", list(poi.get("vibe") or []))
        activity_scores = _tag_scores(relevant, "activity", list(poi.get("activity") or []))
        top_moods = [taxonomy_names.get(tag, tag) for tag, score in mood_scores.items() if score >= 0.45][:3]
        top_vibes = [taxonomy_names.get(tag, tag) for tag, score in vibe_scores.items() if score >= 0.45][:3]
        top_activities = [taxonomy_names.get(tag, tag) for tag, score in activity_scores.items() if score >= 0.45][:4]
        positive_rows = sorted(
            (
                row
                for row in relevant
                if row["polarity"] == "positive" and _is_positive_summary_worthy(row)
            ),
            key=_summary_claim_score,
            reverse=True,
        )
        limitation_rows = sorted(
            (
                row
                for row in relevant
                if row["polarity"] in {"negative", "mixed"}
                and row.get("aspect") in LIMITATION_ASPECTS
                and _is_limitation_summary_worthy(row)
            ),
            key=_summary_claim_score,
            reverse=True,
        )
        positive = list(dict.fromkeys(row["claim"] for row in positive_rows))
        limitations = list(dict.fromkeys(row["claim"] for row in limitation_rows))
        source_keys = {
            row.get("author_hash") or row["evidence_id"]
            for row in relevant
            if not row.get("is_suspected_ad")
        }
        source_count = len(source_keys) or int(poi.get("independent_sources") or 0)
        evidence_quality = round(
            min(
                1.0,
                (sum(float(row.get("source_quality") or 0) for row in relevant) / max(len(relevant), 1))
                * min(1.0, 0.55 + source_count * 0.1),
            ),
            4,
        )
        core_feeling = "、".join(top_moods) or "体验感待更多 UGC 补充"
        if positive:
            core_feeling += f"；代表性体验：{positive[0]}"
        profile = {
            "destination_id": destination_id,
            "name": entity["name"],
            "aliases": entity["aliases"],
            "city": entity["city"],
            "province": entity["province"],
            "category": entity.get("category") or "destination",
            "status": entity["status"],
            "mood_scores": mood_scores,
            "vibe_scores": vibe_scores,
            "activity_scores": activity_scores,
            "core_feeling": core_feeling,
            "atmosphere": "、".join(top_vibes) or "氛围待核实",
            "suitable_scenes": positive[:4],
            "activities": top_activities,
            "limitations": limitations[:3],
            "positive_evidence_count": len(positive),
            "limitation_evidence_count": len(limitations),
            "evidence_quality": evidence_quality,
            "freshness_score": _freshness_score(relevant, today),
            "private_discovery_value": _private_discovery_value(source_count),
            "source_count": source_count,
            "metadata": fact_by_destination.get(destination_id, {}),
        }
        profiles.append(profile)
    return sorted(profiles, key=lambda row: row["destination_id"])


def _validate_rows(rows: Iterable[dict[str, Any]], schema: dict[str, Any], label: str) -> None:
    validator = Draft7Validator(schema, format_checker=FormatChecker())
    for index, row in enumerate(rows, 1):
        errors = sorted(validator.iter_errors(row), key=lambda error: list(error.path))
        if errors:
            error = errors[0]
            path = ".".join(str(item) for item in error.absolute_path) or "$"
            raise ValueError(f"{label}[{index}] {path}: {error.message}")


def build_v2_dataset(
    *,
    poi_rows: list[dict[str, Any]],
    mention_rows: list[dict[str, Any]],
    evidence_rows: list[dict[str, Any]],
    alias_map_path: Path,
    taxonomy_path: Path,
    schemas: dict[str, dict[str, Any]],
    overrides: dict[str, Any] | None = None,
    today: date | None = None,
) -> dict[str, list[dict[str, Any]]]:
    today = today or date.today()
    aliases = load_aliases(alias_map_path)
    alias_rules = load_alias_rules(alias_map_path)
    entities, entity_id_by_poi = build_entities(
        poi_rows,
        mention_rows,
        aliases=aliases,
        alias_rules=alias_rules,
        overrides=overrides,
    )
    poi_by_id = {str(row.get("poi_id") or ""): row for row in poi_rows}
    claims = build_claims(
        evidence_rows,
        mention_rows,
        entities,
        entity_id_by_poi,
        today=today,
    )
    facts = build_facts(poi_by_id, entities)
    profiles = build_profiles(
        entities,
        claims,
        poi_by_id,
        facts,
        load_taxonomy_names(taxonomy_path),
        today=today,
    )
    _validate_rows(entities, schemas["entity"], "entities")
    _validate_rows(claims, schemas["claim"], "claims")
    _validate_rows(profiles, schemas["profile"], "profiles")
    return {
        "entities": entities,
        "claims": claims,
        "facts": facts,
        "profiles": profiles,
    }
