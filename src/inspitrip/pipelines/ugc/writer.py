from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path
from statistics import median
from typing import Any, Iterable

from jsonschema import Draft7Validator, FormatChecker


JZH_PROVINCES = {"上海", "江苏", "浙江"}


def load_schema(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_record(record: dict[str, Any], schema: dict[str, Any], label: str) -> None:
    validator = Draft7Validator(schema, format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(record), key=lambda error: list(error.path))
    if not errors:
        return
    details = []
    for error in errors[:10]:
        path = ".".join(str(item) for item in error.absolute_path) or "$"
        details.append(f"{path}: {error.message}")
    raise ValueError(f"{label} 未通过 Schema：" + "; ".join(details))


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with temp_path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")))
            handle.write("\n")
    temp_path.replace(path)


def load_alias_map(path: Path | None) -> dict[str, dict[str, str]]:
    if not path or not path.exists():
        return {}
    aliases: dict[str, dict[str, str]] = {}
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            raw_name = (row.get("raw_name") or "").strip()
            canonical_name = (row.get("canonical_name") or "").strip()
            if raw_name and canonical_name:
                aliases[raw_name] = {
                    "canonical_name": canonical_name,
                    "city": (row.get("city") or "").strip(),
                    "province": (row.get("province") or "").strip(),
                }
    return aliases


def canonicalize(
    mention: dict[str, Any], aliases: dict[str, dict[str, str]]
) -> tuple[str, str, str]:
    raw_name = (mention.get("raw_place_name") or "").strip()
    proposed = (mention.get("canonical_name") or raw_name).strip()
    alias = aliases.get(raw_name) or aliases.get(proposed) or {}
    name = alias.get("canonical_name") or proposed or raw_name
    city = alias.get("city") or (mention.get("city") or "").strip()
    province = alias.get("province") or (mention.get("province") or "其他")
    return name, city, province


def stable_poi_id(name: str, city: str, province: str) -> str:
    key = "|".join([province.strip(), city.strip(), name.strip()])
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:10].upper()
    return f"POI_{digest}"


def stable_evidence_id(note_id: str, poi_id: str) -> str:
    digest = hashlib.sha1(f"{note_id}|{poi_id}".encode("utf-8")).hexdigest()[:12].upper()
    return f"EV_{digest}"


def normalize_budget(
    signals: list[dict[str, Any]],
    duration_days: int | None,
    *,
    trip_level: bool = False,
) -> tuple[int | None, str, str]:
    """Normalize one mention to RMB per person per trip.

    Trip-level values spanning several POIs are preserved in raw signals but are not
    assigned to a single POI, preventing a route budget from becoming each stop's budget.
    """
    if not signals or trip_level:
        return None, "none", "低"

    direct_person = [
        int(signal["amount"])
        for signal in signals
        if signal.get("basis") == "per_person_trip"
    ]
    if direct_person:
        return int(round(median(direct_person))), "direct", "高"

    converted_trip: list[float] = []
    for signal in signals:
        amount = int(signal.get("amount") or 0)
        basis = signal.get("basis")
        if basis == "per_person_day" and duration_days:
            converted_trip.append(amount * duration_days)
        elif basis == "per_group_trip":
            group_size = int(signal.get("group_size") or 2)
            converted_trip.append(amount / max(group_size, 1))
    if converted_trip:
        return int(round(median(converted_trip))), "direct", "中"

    parts: list[float] = []
    nights = max((duration_days or 1) - 1, 1)
    for signal in signals:
        amount = int(signal.get("amount") or 0)
        basis = signal.get("basis")
        if basis == "per_person_meal":
            parts.append(amount)
        elif basis == "per_room_night":
            room_size = int(signal.get("group_size") or 2)
            parts.append(amount / max(room_size, 1) * nights)
        elif basis == "single_item":
            parts.append(amount)
    if parts:
        return int(round(sum(parts))), "sum", "低"
    return None, "none", "低"


def build_mention_and_evidence_records(
    note: dict[str, Any],
    extraction: dict[str, Any],
    *,
    aliases: dict[str, dict[str, str]],
    evidence_schema: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    mentions: list[dict[str, Any]] = []
    evidence_rows: list[dict[str, Any]] = []
    prepared: list[tuple[dict[str, Any], str, str, str]] = []
    for mention in extraction.get("mentions", []):
        name, city, province = canonicalize(mention, aliases)
        prepared.append((mention, name, city, province))

    # A named business/sub-spot often omits its city while another mention in the
    # same note identifies the single trip city. Propagate only when unambiguous.
    context_cities = {city for _mention, _name, city, _province in prepared if city}
    context_city = next(iter(context_cities)) if len(context_cities) == 1 else ""

    for mention, name, city, province in prepared:
        city = city or context_city
        in_scope = (
            mention.get("place_status") == "specific"
            and province in JZH_PROVINCES
            and bool(name)
        )
        poi_id = stable_poi_id(name, city, province) if in_scope else ""
        trip_level = bool(mention.get("trip_level"))
        normalized, method, budget_confidence = normalize_budget(
            mention.get("budget_signals", []),
            mention.get("duration_days_observed"),
            trip_level=trip_level,
        )
        mention_row = {
            "note_id": note["note_id"],
            "poi_id": poi_id,
            "raw_place_name": mention.get("raw_place_name", ""),
            "canonical_name": name,
            "city": city,
            "province": province,
            "place_status": mention.get("place_status", "vague"),
            "in_scope": in_scope,
            "is_suspected_ad": bool(extraction.get("is_suspected_ad")),
            "ad_reason": extraction.get("ad_reason", ""),
            "author_hash": note.get("author_hash", ""),
            "publish_date": note.get("publish_date", ""),
            "collected_date": note.get("collected_date", ""),
            "mood": mention.get("mood", []),
            "vibe": mention.get("vibe", []),
            "activity": mention.get("activity", []),
            "key_quote": (mention.get("key_quote") or "")[:50],
            "entity_type_hint": mention.get("entity_type_hint", "unknown"),
            "claims": mention.get("claims", []),
            "trip_level": trip_level,
            "budget_signals": mention.get("budget_signals", []),
            "budget_normalized_per_person_trip": normalized,
            "budget_extract_method": method,
            "budget_confidence": budget_confidence,
            "duration_raw_quote": mention.get("duration_raw_quote", ""),
            "duration_days_observed": mention.get("duration_days_observed"),
            "duration_confidence": mention.get("duration_confidence", "低"),
        }
        mentions.append(mention_row)

        if not in_scope:
            continue
        evidence_id = stable_evidence_id(note["note_id"], poi_id)
        evidence_signals = []
        for signal in mention_row["budget_signals"]:
            clean_signal = dict(signal)
            if clean_signal.get("group_size") is None:
                clean_signal.pop("group_size", None)
            evidence_signals.append(clean_signal)
        evidence = {
            "evidence_id": evidence_id,
            "poi_id": poi_id,
            "note_id": note["note_id"],
            "source_platform": "小红书",
            "source_url": note.get("source_url", ""),
            "note_title": note.get("note_title", ""),
            "author_hash": note.get("author_hash", ""),
            "likes": int(note.get("likes") or 0),
            "collects": int(note.get("collects") or 0),
            "comments": int(note.get("comments") or 0),
            "publish_date": note.get("publish_date", ""),
            "is_suspected_ad": bool(extraction.get("is_suspected_ad")),
            "key_quote": mention_row["key_quote"],
            "mood": mention_row["mood"],
            "vibe": mention_row["vibe"],
            "activity": mention_row["activity"],
            "claims": mention_row["claims"],
            "budget_signals": evidence_signals,
            "budget_normalized_per_person_trip": normalized,
            "budget_extract_method": method,
            "budget_confidence": budget_confidence,
            "duration_raw_quote": mention_row["duration_raw_quote"],
            "duration_days_observed": mention_row["duration_days_observed"],
            "duration_confidence": mention_row["duration_confidence"],
            "collected_date": note.get("collected_date", ""),
        }
        validate_record(evidence, evidence_schema, evidence_id)
        mention_row["evidence_id"] = evidence_id
        evidence_rows.append(evidence)
    return mentions, evidence_rows


def _parse_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _freshness(rows: list[dict[str, Any]], today: date) -> str:
    dates = [parsed for row in rows if (parsed := _parse_date(row.get("publish_date", "")))]
    if not dates:
        return "待核实"
    latest = max(dates)
    if latest >= today - timedelta(days=365):
        return "新鲜"
    if latest >= today - timedelta(days=730):
        return "待核实"
    return "疑过期"


def _ordered_tags(rows: list[dict[str, Any]], field: str) -> list[str]:
    counts: Counter[str] = Counter()
    for row in rows:
        counts.update(row.get(field, []))
    return [tag for tag, _count in counts.most_common()]


def _tag_strength(rows: list[dict[str, Any]], field: str) -> dict[str, float]:
    """Legacy POI output also carries normalized tag strength for v2 migration."""
    authors = {row.get("author_hash") or row.get("note_id") for row in rows}
    denominator = max(len(authors), 1)
    per_tag: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        author = row.get("author_hash") or row.get("note_id")
        for tag in row.get(field, []):
            per_tag[tag].add(author)
    return {
        tag: round(min(len(tag_authors) / denominator, 1.0), 4)
        for tag, tag_authors in sorted(per_tag.items(), key=lambda item: (-len(item[1]), item[0]))
    }


def _duration_default(activity: list[str]) -> int:
    return 2 if {"act_sea", "act_stay", "act_hotspring"} & set(activity) else 1


def _independent_keys(rows: list[dict[str, Any]]) -> set[str]:
    keys = {row.get("author_hash", "") for row in rows if row.get("author_hash")}
    if any(not row.get("author_hash") for row in rows):
        keys.add("__unknown_author__")
    return keys


def _dedup_numeric_by_author(
    rows: list[dict[str, Any]], field: str
) -> list[int]:
    grouped: dict[str, list[int]] = defaultdict(list)
    for row in rows:
        value = row.get(field)
        if value is None:
            continue
        author_key = row.get("author_hash") or "__unknown_author__"
        grouped[author_key].append(int(value))
    return [int(round(median(values))) for values in grouped.values()]


def load_enrichment(path: Path | None) -> dict[str, dict[str, Any]]:
    if not path:
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return {
            str(item.get("poi_id") or item.get("name")): item
            for item in data
            if item.get("poi_id") or item.get("name")
        }
    if not isinstance(data, dict):
        raise ValueError("地图补全文件必须是 JSON object 或 array")
    return data


def aggregate_pois(
    mention_rows: list[dict[str, Any]],
    *,
    poi_schema: dict[str, Any],
    enrichment: dict[str, dict[str, Any]] | None = None,
    today: date | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    today = today or date.today()
    enrichment = enrichment or {}
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in mention_rows:
        if row.get("in_scope") and row.get("poi_id"):
            groups[row["poi_id"]].append(row)

    final_rows: list[dict[str, Any]] = []
    candidate_rows: list[dict[str, Any]] = []
    discarded_rows: list[dict[str, Any]] = []
    for poi_id, rows in sorted(groups.items()):
        valid = [row for row in rows if not row.get("is_suspected_ad")]
        if not valid:
            discarded_rows.append(
                {
                    "poi_id": poi_id,
                    "name": rows[0]["canonical_name"],
                    "reason": "所有来源均疑似软广，无独立可信佐证",
                }
            )
            continue

        independent_sources = len(_independent_keys(valid))
        freshness = _freshness(valid, today)
        if independent_sources >= 3 and freshness == "新鲜":
            confidence, evidence_level = "高", "强印证"
        elif 1 <= independent_sources <= 2 and freshness == "新鲜":
            confidence, evidence_level = "中", "弱印证"
        else:
            confidence, evidence_level = "低", "孤证/待核"

        mood = _ordered_tags(valid, "mood")
        vibe = _ordered_tags(valid, "vibe")
        activity = _ordered_tags(valid, "activity")
        mood_scores = _tag_strength(valid, "mood")
        vibe_scores = _tag_strength(valid, "vibe")
        activity_scores = _tag_strength(valid, "activity")

        budget_rows = [row for row in valid if not row.get("trip_level")]
        filterable_budget_rows = [
            row for row in budget_rows if row.get("budget_confidence") in {"高", "中"}
        ]
        estimate_budget_rows = [
            row for row in budget_rows if row.get("budget_confidence") == "低"
        ]
        budgets = _dedup_numeric_by_author(
            filterable_budget_rows, "budget_normalized_per_person_trip"
        )
        estimates = _dedup_numeric_by_author(
            estimate_budget_rows, "budget_normalized_per_person_trip"
        )
        duration_rows = [
            row
            for row in valid
            if not row.get("trip_level") and row.get("duration_days_observed") is not None
        ]
        durations = _dedup_numeric_by_author(duration_rows, "duration_days_observed")
        if durations:
            duration_counts = Counter(durations)
            duration_days = sorted(
                duration_counts, key=lambda value: (-duration_counts[value], value)
            )[0]
            duration_source = "证据"
        else:
            duration_days = _duration_default(activity)
            duration_source = "类型默认"

        quotes = []
        for row in valid:
            quote = row.get("key_quote", "")
            if quote and quote not in quotes:
                quotes.append(quote)
        description = "；".join(quotes)[:200]

        candidate: dict[str, Any] = {
            "poi_id": poi_id,
            "name": rows[0]["canonical_name"],
            "city": rows[0].get("city", ""),
            "province": rows[0]["province"],
            "duration_days": duration_days,
            "duration_source": duration_source,
            "mood": mood,
            "vibe": vibe,
            "activity": activity,
            "mood_scores": mood_scores,
            "vibe_scores": vibe_scores,
            "activity_scores": activity_scores,
            "description": description,
            "evidence_count": independent_sources,
            "independent_sources": independent_sources,
            "confidence": confidence,
            "evidence_level": evidence_level,
            "freshness_flag": freshness,
            "needs_verify_note": (
                ""
                if confidence == "高"
                else (
                    f"目前仅 {independent_sources} 个独立来源，信息有限，出行前建议核实。"
                    if confidence == "中"
                    else "信息较少或可能过期，出行前请核实营业、交通与价格。"
                )
            ),
            "collected_date": today.isoformat(),
        }
        if budgets:
            observed_budget_confidences = {
                row.get("budget_confidence", "低")
                for row in filterable_budget_rows
                if row.get("budget_normalized_per_person_trip") is not None
            }
            if len(budgets) >= 3 and observed_budget_confidences == {"高"}:
                poi_budget_confidence = "高"
            elif observed_budget_confidences & {"高", "中"}:
                poi_budget_confidence = "中"
            else:
                poi_budget_confidence = "低"
            candidate.update(
                {
                    "budget_per_capita": int(round(median(budgets))),
                    "budget_min": min(budgets),
                    "budget_max": max(budgets),
                    "budget_basis": "人均·全程（按笔记明确口径归一；往返大交通是否包含以原文为准）",
                    "budget_evidence_count": len(budgets),
                    "budget_confidence": poi_budget_confidence,
                    "budget_filterable": True,
                }
            )
        elif estimates:
            candidate.update(
                {
                    "budget_estimate_per_capita": int(round(median(estimates))),
                    "budget_confidence": "低",
                    "budget_filterable": False,
                }
            )

        supplement = enrichment.get(poi_id) or enrichment.get(candidate["name"]) or {}
        for field in (
            "reachable_from",
            "travel_time_min",
            "travel_time_source",
            "transport",
            "best_season",
        ):
            if field in supplement:
                candidate[field] = supplement[field]

        # 硬槽位分层退化：缺口只由 schema 的 required 派生。
        # travel_time_min / reachable_from 已从 required 移出（v1 不强制地图精度），
        # 有地图补全就填 UGC辅助/地图API，没有也不再挡候选进入 poi_seed。
        missing = []
        for field in poi_schema.get("required", []):
            if field not in candidate or candidate[field] in (None, "", []):
                missing.append(field)
        missing = sorted(set(missing))

        candidate_row = dict(candidate)
        candidate_row["missing_required_fields"] = missing
        candidate_rows.append(candidate_row)

        if not missing:
            validate_record(candidate, poi_schema, poi_id)
            final_rows.append(candidate)

    return final_rows, candidate_rows, discarded_rows
