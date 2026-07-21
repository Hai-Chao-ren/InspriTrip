from __future__ import annotations

import csv
import hashlib
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

from inspitrip.recommendation.v2_pipeline import (
    calculate_source_quality,
    infer_aspect,
    infer_conditions,
    infer_polarity,
)


ENHANCED_CLAIM_PREFIX = "CLM_RAW_"
PRIORITY_ASPECTS = ("crowd", "commercialization", "transport", "cost")
PRIORITY_POLARITIES = {"negative", "mixed", "neutral"}
ELIGIBLE_ENTITY_TYPES = {"destination", "experience"}

ASPECT_PATTERNS: dict[str, re.Pattern[str]] = {
    "crowd": re.compile(
        r"人少|人不多|清净|安静|游客(?:多|少|很|较|比较|密集|挤)|人多|拥挤|"
        r"人挤人|排队|人潮|客流|避开人群|错峰|限流"
    ),
    "commercialization": re.compile(
        r"商业化|原生态|过度开发|开发痕迹|网红|宰客|拉客|市井|商业街|店铺同质化"
    ),
    "transport": re.compile(
        r"交通|高铁|火车|动车|自驾|开车|停车|公交|公共交通|地铁|打车|出租车|"
        r"网约车|轮渡|坐船|船票|航班|接驳|换乘|码头(?!餐厅)|车程|路程|班车|班次|"
        r"堵车|绕路|上岛|登岛|晕船"
    ),
    "cost": re.compile(
        r"预算|人均|价格|价位|物价|费用|花费|性价比|票价|房费|住宿费|餐费|"
        r"收费|免费|便宜|划算|不值|宰客|(?:太|很|比较|有点|偏)贵|"
        r"\d+(?:\.\d+)?\s*(?:元|块|[rR](?![A-Za-z]))"
    ),
}

CONDITIONAL_EXPERIENCE_RE = re.compile(
    r"适合|建议|推荐|景色|风景|海水|看海|日出|日落|夕阳|拍照|出片|徒步|骑行|"
    r"开放|关闭|停业|人少|人多|游客|拥挤|排队|交通|高铁|自驾|公交|打车|"
    r"轮渡|坐船|船票|门票|费用|预算|凉快|炎热|冷|下雨|晴天|阴天|预约|抢票"
)

AD_FRAGMENT_RE = re.compile(
    r"商务合作|商业合作|品牌合作|推广合作|广告合作|报暗号|优惠券|领券|返现|"
    r"免费试住|免费体验|酒店邀约|民宿邀约|探店邀约|置换合作|私信(?:我|领取|咨询)|"
    r"点击(?:主页|链接)|联系客服|加(?:微|微信)|薯店|团购链接|评论区咨询|评论区扣|"
    r"不明处.{0,8}咨询"
)

UNINFORMATIVE_FRAGMENT_RE = re.compile(
    r"^(?:住宿|交通|住宿\s*[&和＋+]\s*交通|交通\s*[&和＋+]\s*住宿|"
    r"路线|行程|费用|预算|门票|注意事项|实用信息|tips?)\s*[：:]*$",
    re.IGNORECASE,
)
GUIDE_TITLE_RE = re.compile(r"(?:攻略|保姆级教程|游玩指南|出行指南|路线合集)\s*$")
INVALID_MONEY_RE = re.compile(r"\d+(?:\s*[-～~]\s*\d+)?\s*米/(?:人|间|位|车)")
TICKET_PRICE_RE = re.compile(
    r"(?:门票|船票|车票|套票)[^，。；\n]{0,10}(?:约|大概)?\d+(?:\.\d+)?\s*(?:💰|🎫)?"
)
TICKET_BOOKING_CONTEXT_RE = re.compile(r"提前|预定|预订|订票|预约|班次|航程|几点|日期")
NON_CROWD_GROUP_RE = re.compile(r"人多(?:可|就|建议|的话|可以)[^，。；！？\n]{0,12}(?:包车|拼车|组团)")
SERVICE_CONTEXT_RE = re.compile(
    r"餐厅|饭店|民宿|酒店|房间|住宿|服务|卫生|口感|味道|海鲜面|烧烤|咖啡|"
    r"鸡翅|面馆|小炒|海鲜店|海景房|客房"
)
DESTINATION_SCOPE_RE = re.compile(r"岛上|当地|全岛|整座|景区内|镇上|村里|整体|全程|整趟")
PLACE_NAME_RE = re.compile(
    r"[\u4e00-\u9fffA-Za-z0-9·]{1,18}(?:列岛|半岛|古镇|古村|渔村|沙滩|公园|"
    r"景区|码头|观景台|博物馆|美术馆|寺|岛|村|镇|县|市|山|湖|湾)"
)
GENERIC_PLACE_WORDS = {
    "上岛", "登岛", "离岛", "海岛", "小岛", "岛内", "全岛", "本岛", "这座岛",
    "码头", "景区", "公园", "沙滩", "观景台",
}
CONDITIONAL_ALLOWED_ASPECTS = {
    "mood_fit", "crowd", "commercialization", "scenery", "activity",
    "transport", "cost", "stay", "food", "solo", "photo", "weather_season", "other",
}

MAJOR_SPLIT_RE = re.compile(r"(?:[\r\n。！？!?；;]+|<图[^>]*>)")
CLAUSE_SPLIT_RE = re.compile(r"[，,]+")
LEADING_BULLET_RE = re.compile(r"^[\s•·●▪︎■□◆◇▶▷►▸➤✓✔✅❌⚠️📍💰🚗🚢🏨🍜🌟⭐*#—–-]+")


@dataclass(frozen=True)
class NoteTarget:
    entity_id: str
    destination_id: str
    entity_type: str
    terms: tuple[str, ...]
    mention: dict[str, Any]
    evidence: dict[str, Any]


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


def load_raw_notes(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _note_id_from_url(value: str) -> str:
    try:
        parts = [part for part in urlparse(value).path.split("/") if part]
    except ValueError:
        return ""
    if "item" in parts:
        index = parts.index("item")
        if index + 1 < len(parts):
            return parts[index + 1]
    return parts[-1] if parts else ""


def _entity_by_legacy_poi(entities: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for entity in entities:
        poi_ids = set(entity.get("legacy_poi_ids") or [])
        if entity.get("legacy_poi_id"):
            poi_ids.add(entity["legacy_poi_id"])
        for poi_id in poi_ids:
            if poi_id:
                result[str(poi_id)] = entity
    return result


def _target_terms(entity: dict[str, Any], mention: dict[str, Any]) -> tuple[str, ...]:
    terms = {
        str(entity.get("name") or "").strip(),
        str(mention.get("canonical_name") or "").strip(),
        str(mention.get("raw_place_name") or "").strip(),
        *(str(value).strip() for value in entity.get("aliases") or []),
    }
    return tuple(sorted((value for value in terms if len(value) >= 2), key=lambda value: (-len(value), value)))


def build_note_targets(
    raw_notes: list[dict[str, str]],
    mentions: list[dict[str, Any]],
    evidence_rows: list[dict[str, Any]],
    entities: list[dict[str, Any]],
) -> tuple[dict[int, list[NoteTarget]], Counter[str]]:
    evidence_by_id = {
        str(row.get("evidence_id") or ""): row
        for row in evidence_rows
        if row.get("evidence_id")
    }
    entity_by_poi = _entity_by_legacy_poi(entities)
    mentions_by_url: dict[str, list[dict[str, Any]]] = defaultdict(list)
    mentions_by_note: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for mention in mentions:
        evidence = evidence_by_id.get(str(mention.get("evidence_id") or ""), {})
        source_url = str(evidence.get("source_url") or "")
        if source_url:
            mentions_by_url[source_url].append(mention)
        note_id = str(mention.get("note_id") or "")
        if note_id:
            mentions_by_note[note_id].append(mention)

    stats: Counter[str] = Counter()
    targets_by_note: dict[int, list[NoteTarget]] = {}
    for index, raw_note in enumerate(raw_notes):
        source_url = str(raw_note.get("source_url") or "")
        note_id = _note_id_from_url(source_url)
        note_mentions = mentions_by_url.get(source_url) or mentions_by_note.get(note_id) or []
        targets: dict[str, NoteTarget] = {}
        for mention in note_mentions:
            evidence = evidence_by_id.get(str(mention.get("evidence_id") or ""))
            entity = entity_by_poi.get(str(mention.get("poi_id") or ""))
            if not evidence or not entity:
                stats["missing_evidence_or_entity"] += 1
                continue
            entity_type = str(entity.get("entity_type") or "")
            if entity_type not in ELIGIBLE_ENTITY_TYPES:
                stats["ineligible_entity_type"] += 1
                continue
            destination_id = (
                str(entity.get("entity_id") or "")
                if entity_type == "destination"
                else str(entity.get("parent_id") or "")
            )
            if not destination_id:
                stats["unlinked_experience"] += 1
                continue
            if (
                _truthy(raw_note.get("is_suspected_ad"))
                or _truthy(mention.get("is_suspected_ad"))
                or _truthy(evidence.get("is_suspected_ad"))
            ):
                stats["ad_target_skipped"] += 1
                continue
            target = NoteTarget(
                entity_id=str(entity.get("entity_id") or ""),
                destination_id=destination_id,
                entity_type=entity_type,
                terms=_target_terms(entity, mention),
                mention=mention,
                evidence=evidence,
            )
            targets[target.entity_id] = target
        targets_by_note[index] = sorted(targets.values(), key=lambda item: item.entity_id)
    return targets_by_note, stats


def _clean_fragment(value: str) -> str:
    fragment = LEADING_BULLET_RE.sub("", value.strip()).strip()
    if "[话题]" in fragment:
        fragment = fragment.split("[话题]", 1)[0].strip()
    if (
        "�" in fragment
        or INVALID_MONEY_RE.search(fragment)
        or UNINFORMATIVE_FRAGMENT_RE.fullmatch(fragment)
        or GUIDE_TITLE_RE.search(fragment)
    ):
        return ""
    return fragment[:240].strip() if len(fragment) > 240 else fragment


def split_note_fragment_contexts(raw_note: dict[str, str]) -> list[tuple[str, str]]:
    values = [str(raw_note.get("note_title") or ""), str(raw_note.get("content") or "")]
    fragments: list[tuple[str, str]] = []
    seen: set[str] = set()
    for value in values:
        for major in MAJOR_SPLIT_RE.split(value):
            for item in CLAUSE_SPLIT_RE.split(major):
                fragment = _clean_fragment(item)
                if len(fragment) < 5 or fragment in seen:
                    continue
                seen.add(fragment)
                fragments.append((fragment, major))
    return fragments


def split_note_fragments(raw_note: dict[str, str]) -> list[str]:
    return [fragment for fragment, _context in split_note_fragment_contexts(raw_note)]


def detect_aspects(text: str) -> list[str]:
    aspects = [aspect for aspect in PRIORITY_ASPECTS if ASPECT_PATTERNS[aspect].search(text)]
    if "crowd" in aspects and NON_CROWD_GROUP_RE.search(text):
        crowd_without_group_phrase = NON_CROWD_GROUP_RE.sub("", text)
        if not ASPECT_PATTERNS["crowd"].search(crowd_without_group_phrase):
            aspects.remove("crowd")
    if (
        "cost" not in aspects
        and TICKET_PRICE_RE.search(text)
        and not TICKET_BOOKING_CONTEXT_RE.search(text)
    ):
        aspects.append("cost")
    return aspects


def _select_targets(
    fragment: str,
    targets: list[NoteTarget],
    sentence_context: str = "",
) -> tuple[list[NoteTarget], str]:
    explicit = [target for target in targets if any(term in fragment for term in target.terms)]
    if len(explicit) == 1:
        return explicit, "explicit_entity"
    if len(explicit) > 1:
        experiences = [target for target in explicit if target.entity_type == "experience"]
        destination_ids = {target.destination_id for target in explicit}
        if len(experiences) == 1 and len(destination_ids) == 1:
            return experiences, "explicit_child_over_parent"
        return [], "ambiguous_explicit_entities"

    context_explicit = [
        target
        for target in targets
        if any(term in sentence_context for term in target.terms)
    ]
    if len(context_explicit) == 1:
        return context_explicit, "sentence_context_entity"
    if len(context_explicit) > 1:
        experiences = [target for target in context_explicit if target.entity_type == "experience"]
        destination_ids = {target.destination_id for target in context_explicit}
        if len(experiences) == 1 and len(destination_ids) == 1:
            return experiences, "sentence_context_child_over_parent"
        return [], "ambiguous_sentence_context"

    destinations = [target for target in targets if target.entity_type == "destination"]
    if len(destinations) == 1:
        return destinations, "single_destination_context"
    if not destinations and len(targets) == 1:
        return targets, "single_experience_context"
    return [], "ambiguous_note_context"


def _has_unmapped_explicit_place(fragment: str, targets: list[NoteTarget]) -> bool:
    if any(term in fragment for target in targets for term in target.terms):
        return False
    candidates = {
        match.group(0).strip()
        for match in PLACE_NAME_RE.finditer(fragment)
        if len(match.group(0).strip()) >= 2
        and match.group(0).strip() not in GENERIC_PLACE_WORDS
    }
    return bool(candidates)


def _stable_raw_claim_id(evidence_id: str, entity_id: str, aspect: str, quote: str) -> str:
    identity = f"raw_sentence_v1|{evidence_id}|{entity_id}|{aspect}|{quote}"
    digest = hashlib.sha1(identity.encode("utf-8")).hexdigest()[:12].upper()
    return f"{ENHANCED_CLAIM_PREFIX}{digest}"


def generate_enhanced_claims(
    *,
    raw_notes: list[dict[str, str]],
    mentions: list[dict[str, Any]],
    evidence_rows: list[dict[str, Any]],
    entities: list[dict[str, Any]],
    existing_claims: list[dict[str, Any]],
    today: date,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    targets_by_note, target_stats = build_note_targets(raw_notes, mentions, evidence_rows, entities)
    entity_by_id = {str(row.get("entity_id") or ""): row for row in entities}
    existing_keys = {
        (
            str(row.get("entity_id") or ""),
            str(row.get("aspect") or ""),
            re.sub(r"\s+", "", str(row.get("key_quote") or row.get("claim") or "")),
        )
        for row in existing_claims
        if not row.get("is_suspected_ad") and not str(row.get("claim_id") or "").startswith(ENHANCED_CLAIM_PREFIX)
    }
    rows_by_id: dict[str, dict[str, Any]] = {}
    stats: Counter[str] = Counter(target_stats)
    aspect_counts: Counter[str] = Counter()
    polarity_counts: Counter[str] = Counter()
    entity_type_counts: Counter[str] = Counter()
    assignment_counts: Counter[str] = Counter()

    for index, raw_note in enumerate(raw_notes):
        if _truthy(raw_note.get("is_suspected_ad")):
            stats["ad_note_skipped"] += 1
            continue
        targets = targets_by_note.get(index, [])
        if not targets:
            stats["note_without_eligible_target"] += 1
            continue
        for fragment, sentence_context in split_note_fragment_contexts(raw_note):
            stats["fragments_seen"] += 1
            if AD_FRAGMENT_RE.search(fragment):
                stats["ad_fragment_skipped"] += 1
                continue
            conditions = infer_conditions(fragment)
            aspects = detect_aspects(fragment)
            polarity = infer_polarity(fragment)
            if conditions and not aspects and CONDITIONAL_EXPERIENCE_RE.search(fragment):
                mention = targets[0].mention
                inferred_aspect = infer_aspect(
                        fragment,
                        list(mention.get("activity") or []),
                        list(mention.get("mood") or []),
                        list(mention.get("vibe") or []),
                    )
                if inferred_aspect in CONDITIONAL_ALLOWED_ASPECTS:
                    aspects = [inferred_aspect]
            if not aspects:
                stats["fragment_without_priority_signal"] += 1
                continue
            if polarity == "positive" and not conditions:
                stats["unconditional_positive_skipped"] += 1
                continue
            if _has_unmapped_explicit_place(fragment, targets):
                stats["unmapped_explicit_place"] += 1
                continue
            selected, assignment = _select_targets(fragment, targets, sentence_context)
            if not selected:
                stats[assignment] += 1
                continue
            if (
                assignment in {"single_destination_context", "sentence_context_entity"}
                and selected[0].entity_type == "destination"
                and SERVICE_CONTEXT_RE.search(fragment)
                and not DESTINATION_SCOPE_RE.search(fragment)
            ):
                stats["service_fragment_without_explicit_entity"] += 1
                continue
            assignment_counts[assignment] += 1
            for target in selected:
                if target.destination_id not in entity_by_id:
                    stats["destination_not_found"] += 1
                    continue
                evidence = dict(target.evidence)
                evidence["key_quote"] = fragment
                for aspect in aspects:
                    normalized_quote = re.sub(r"\s+", "", fragment)
                    key = (target.entity_id, aspect, normalized_quote)
                    if key in existing_keys:
                        stats["already_present"] += 1
                        continue
                    evidence_id = str(evidence.get("evidence_id") or "")
                    claim_id = _stable_raw_claim_id(evidence_id, target.entity_id, aspect, fragment)
                    row = {
                        "claim_id": claim_id,
                        "evidence_id": evidence_id,
                        "entity_id": target.entity_id,
                        "destination_id": target.destination_id,
                        "note_id": str(target.mention.get("note_id") or ""),
                        "aspect": aspect,
                        "polarity": polarity,
                        "claim": fragment,
                        "key_quote": fragment,
                        "mood": list(target.mention.get("mood") or []),
                        "vibe": list(target.mention.get("vibe") or []),
                        "activity": list(target.mention.get("activity") or []),
                        "conditions": conditions,
                        "author_hash": str(evidence.get("author_hash") or target.mention.get("author_hash") or ""),
                        "publish_date": str(evidence.get("publish_date") or ""),
                        "collected_date": str(evidence.get("collected_date") or today.isoformat()),
                        "source_quality": calculate_source_quality(evidence, today),
                        "is_suspected_ad": False,
                        "source_url": str(evidence.get("source_url") or raw_note.get("source_url") or ""),
                    }
                    rows_by_id[claim_id] = row
                    aspect_counts[aspect] += 1
                    polarity_counts[polarity] += 1
                    entity_type_counts[target.entity_type] += 1

    report = {
        "scan_counts": dict(sorted(stats.items())),
        "assignment_counts": dict(sorted(assignment_counts.items())),
        "generated_by_aspect": dict(sorted(aspect_counts.items())),
        "generated_by_polarity": dict(sorted(polarity_counts.items())),
        "generated_by_entity_type": dict(sorted(entity_type_counts.items())),
    }
    return sorted(rows_by_id.values(), key=lambda row: row["claim_id"]), report


def merge_claims(
    existing_claims: list[dict[str, Any]],
    generated_claims: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    old_enhanced = {
        str(row.get("claim_id") or ""): row
        for row in existing_claims
        if str(row.get("claim_id") or "").startswith(ENHANCED_CLAIM_PREFIX)
    }
    base = [
        row
        for row in existing_claims
        if not row.get("is_suspected_ad")
        and not str(row.get("claim_id") or "").startswith(ENHANCED_CLAIM_PREFIX)
    ]
    generated_by_id = {str(row["claim_id"]): row for row in generated_claims}
    added = sum(claim_id not in old_enhanced for claim_id in generated_by_id)
    updated = sum(
        claim_id in old_enhanced and old_enhanced[claim_id] != row
        for claim_id, row in generated_by_id.items()
    )
    unchanged = sum(
        claim_id in old_enhanced and old_enhanced[claim_id] == row
        for claim_id, row in generated_by_id.items()
    )
    removed_stale = sum(claim_id not in generated_by_id for claim_id in old_enhanced)
    removed_ads = sum(bool(row.get("is_suspected_ad")) for row in existing_claims)
    merged = base + sorted(generated_claims, key=lambda row: row["claim_id"])
    return merged, {
        "added": added,
        "updated": updated,
        "unchanged": unchanged,
        "removed_stale": removed_stale,
        "removed_ads": removed_ads,
        "base_claims": len(base),
        "enhanced_claims": len(generated_claims),
        "final_claims": len(merged),
    }


def coverage_summary(
    claims: list[dict[str, Any]],
    entities: list[dict[str, Any]],
) -> dict[str, Any]:
    entity_type_by_id = {
        str(row.get("entity_id") or ""): str(row.get("entity_type") or "")
        for row in entities
    }
    destinations = [row for row in entities if row.get("entity_type") == "destination"]
    rows_by_destination: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for claim in claims:
        destination_id = str(claim.get("destination_id") or "")
        if (
            destination_id
            and not claim.get("is_suspected_ad")
            and entity_type_by_id.get(str(claim.get("entity_id") or "")) in ELIGIBLE_ENTITY_TYPES
        ):
            rows_by_destination[destination_id].append(claim)

    def matching(destination_id: str, aspect: str | None = None) -> list[dict[str, Any]]:
        return [
            row
            for row in rows_by_destination.get(destination_id, [])
            if row.get("polarity") in PRIORITY_POLARITIES
            and (aspect is None or row.get("aspect") == aspect)
            and (aspect is not None or row.get("aspect") in PRIORITY_ASPECTS)
        ]

    by_aspect = {
        aspect: {
            "destinations": sum(bool(matching(str(row["entity_id"]), aspect)) for row in destinations),
            "claims": sum(
                1
                for claim in claims
                if not claim.get("is_suspected_ad")
                and claim.get("aspect") == aspect
                and claim.get("polarity") in PRIORITY_POLARITIES
                and entity_type_by_id.get(str(claim.get("entity_id") or "")) in ELIGIBLE_ENTITY_TYPES
            ),
        }
        for aspect in PRIORITY_ASPECTS
    }
    no_priority = [
        str(row.get("name") or row.get("entity_id") or "")
        for row in destinations
        if not matching(str(row["entity_id"]))
    ]
    no_conditions = [
        str(row.get("name") or row.get("entity_id") or "")
        for row in destinations
        if not any(claim.get("conditions") for claim in rows_by_destination.get(str(row["entity_id"]), []))
    ]
    return {
        "total_destinations": len(destinations),
        "priority_aspects": by_aspect,
        "priority_any": {
            "destinations": len(destinations) - len(no_priority),
            "claims": sum(len(matching(str(row["entity_id"]))) for row in destinations),
        },
        "negative_or_mixed": {
            "destinations": sum(
                any(claim.get("polarity") in {"negative", "mixed"} for claim in rows_by_destination.get(str(row["entity_id"]), []))
                for row in destinations
            ),
            "claims": sum(
                1
                for claim in claims
                if not claim.get("is_suspected_ad")
                and claim.get("polarity") in {"negative", "mixed"}
                and entity_type_by_id.get(str(claim.get("entity_id") or "")) in ELIGIBLE_ENTITY_TYPES
            ),
        },
        "conditions": {
            "destinations": len(destinations) - len(no_conditions),
            "claims": sum(
                1
                for claim in claims
                if not claim.get("is_suspected_ad")
                and claim.get("conditions")
                and entity_type_by_id.get(str(claim.get("entity_id") or "")) in ELIGIBLE_ENTITY_TYPES
            ),
        },
        "destinations_without_priority_claim": sorted(no_priority),
        "destinations_without_conditional_claim": sorted(no_conditions),
    }
