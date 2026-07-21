from __future__ import annotations

import argparse
import json
from pathlib import Path

import psycopg
from dotenv import dotenv_values

from inspitrip.paths import DEFAULT_ENV_PATH, PRIVATE_DATA_DIR


ROOT = Path(__file__).resolve().parents[4]
ENV_VALUES = dotenv_values(DEFAULT_ENV_PATH)


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def validate_claim_snapshot(claims: list[dict]) -> list[str]:
    if not claims:
        raise ValueError("拒绝同步空 Claim 快照")
    claim_ids = [str(row.get("claim_id") or "").strip() for row in claims]
    if any(not claim_id for claim_id in claim_ids):
        raise ValueError("Claim 快照存在空 claim_id")
    if len(claim_ids) != len(set(claim_ids)):
        raise ValueError("Claim 快照存在重复 claim_id")
    return claim_ids


def text_or_default(value, default: str = "") -> str:
    return default if value is None else str(value)


def upsert_claim_snapshot(
    cursor,
    claims: list[dict],
    claim_ids: list[str] | None = None,
) -> int:
    claim_ids = claim_ids if claim_ids is not None else validate_claim_snapshot(claims)
    if not claim_ids:
        raise ValueError("拒绝同步空 Claim 快照")
    for row in claims:
        cursor.execute(
            """
            INSERT INTO ugc_evidence_claims (
                claim_id, evidence_id, entity_id, destination_id, note_id,
                aspect, polarity, claim, key_quote, mood, vibe, activity,
                conditions, author_hash, publish_date, collected_date,
                source_quality, is_suspected_ad, source_url
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,NULLIF(%s,'')::date,NULLIF(%s,'')::date,%s,%s,%s)
            ON CONFLICT (claim_id) DO UPDATE SET
                evidence_id=EXCLUDED.evidence_id,
                entity_id=EXCLUDED.entity_id,
                destination_id=EXCLUDED.destination_id,
                note_id=EXCLUDED.note_id,
                aspect=EXCLUDED.aspect,
                polarity=EXCLUDED.polarity,
                claim=EXCLUDED.claim,
                key_quote=EXCLUDED.key_quote,
                mood=EXCLUDED.mood,
                vibe=EXCLUDED.vibe,
                activity=EXCLUDED.activity,
                conditions=EXCLUDED.conditions,
                author_hash=EXCLUDED.author_hash,
                publish_date=EXCLUDED.publish_date,
                collected_date=EXCLUDED.collected_date,
                source_quality=EXCLUDED.source_quality,
                is_suspected_ad=EXCLUDED.is_suspected_ad,
                source_url=EXCLUDED.source_url
            """,
            (
                row["claim_id"], row["evidence_id"], row["entity_id"],
                row.get("destination_id"), row["note_id"], row["aspect"],
                row["polarity"], row["claim"], row["key_quote"], row["mood"],
                row["vibe"], row["activity"], json.dumps(row["conditions"], ensure_ascii=False),
                row["author_hash"], row["publish_date"], row["collected_date"],
                row["source_quality"], row["is_suspected_ad"], row["source_url"],
            ),
        )
    cursor.execute(
        "DELETE FROM ugc_evidence_claims WHERE NOT (claim_id = ANY(%s::text[]))",
        (claim_ids,),
    )
    return max(int(cursor.rowcount or 0), 0)


def main() -> int:
    parser = argparse.ArgumentParser(description="把 v2 JSONL 快照幂等写入 PostgreSQL")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=PRIVATE_DATA_DIR / "generated",
    )
    parser.add_argument(
        "--ddl",
        type=Path,
        default=Path(__file__).with_name("001_recommendation_v2.sql"),
    )
    args = parser.parse_args()
    database_url = str(ENV_VALUES.get("DATABASE_URL") or "").strip()
    if not database_url:
        raise SystemExit("请在 .env 配置 DATABASE_URL")

    entities = load_jsonl(args.data_dir / "entities.jsonl")
    map_enrichment = load_jsonl(args.data_dir / "destination_map_enrichment.jsonl")
    facts = load_jsonl(args.data_dir / "destination_facts.jsonl")
    travel = load_jsonl(args.data_dir / "travel_matrix.jsonl")
    claims = load_jsonl(args.data_dir / "ugc_claims.jsonl")
    profiles = load_jsonl(args.data_dir / "destination_profiles.jsonl")
    map_by_destination = {
        str(row.get("destination_id")): row
        for row in map_enrichment
        if row.get("destination_id")
    }
    if len(map_by_destination) != len(map_enrichment):
        raise SystemExit("地图补全快照存在空或重复 destination_id")
    destination_ids = {
        str(row["entity_id"])
        for row in entities
        if row.get("entity_type") == "destination"
    }
    if set(map_by_destination) != destination_ids:
        raise SystemExit("地图补全快照未一一覆盖全部 destination")
    try:
        claim_ids = validate_claim_snapshot(claims)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    deleted_stale_claims = 0
    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(args.ddl.read_text(encoding="utf-8"))
            for row in entities:
                map_payload = map_by_destination.get(row["entity_id"], {})
                cursor.execute(
                    """
                    INSERT INTO recommendation_entities (
                        entity_id, legacy_poi_id, entity_type, parent_id, name,
                        aliases, city, province, category, longitude, latitude,
                        map_poi_id, standard_province, standard_city,
                        standard_district, adcode, address, telephone,
                        business_area, opening_hours, map_operational_status,
                        map_match_confidence, map_match_level, geocode_status,
                        map_review_status, map_checked_at, map_source,
                        map_payload, status
                    ) VALUES (
                        %s,%s,%s,NULL,
                        %s,%s,%s,%s,%s,%s,%s,%s,
                        %s,%s,%s,%s,%s,%s,%s,%s,
                        %s,%s,%s,%s,%s,
                        NULLIF(%s,'')::timestamptz,%s,%s::jsonb,%s
                    )
                    ON CONFLICT (entity_id) DO UPDATE SET
                        legacy_poi_id=EXCLUDED.legacy_poi_id,
                        entity_type=EXCLUDED.entity_type,
                        name=EXCLUDED.name,
                        aliases=EXCLUDED.aliases,
                        city=EXCLUDED.city,
                        province=EXCLUDED.province,
                        category=EXCLUDED.category,
                        longitude=EXCLUDED.longitude,
                        latitude=EXCLUDED.latitude,
                        map_poi_id=EXCLUDED.map_poi_id,
                        standard_province=EXCLUDED.standard_province,
                        standard_city=EXCLUDED.standard_city,
                        standard_district=EXCLUDED.standard_district,
                        adcode=EXCLUDED.adcode,
                        address=EXCLUDED.address,
                        telephone=EXCLUDED.telephone,
                        business_area=EXCLUDED.business_area,
                        opening_hours=EXCLUDED.opening_hours,
                        map_operational_status=EXCLUDED.map_operational_status,
                        map_match_confidence=EXCLUDED.map_match_confidence,
                        map_match_level=EXCLUDED.map_match_level,
                        geocode_status=EXCLUDED.geocode_status,
                        map_review_status=EXCLUDED.map_review_status,
                        map_checked_at=EXCLUDED.map_checked_at,
                        map_source=EXCLUDED.map_source,
                        map_payload=EXCLUDED.map_payload,
                        status=EXCLUDED.status,
                        updated_at=now()
                    """,
                    (
                        row["entity_id"], row["legacy_poi_id"], row["entity_type"],
                        row["name"], row["aliases"], row["city"], row["province"],
                        row.get("category", ""), row.get("longitude"), row.get("latitude"),
                        row.get("map_poi_id"), row.get("standard_province", ""),
                        row.get("standard_city", ""), row.get("standard_district", ""),
                        row.get("adcode", ""), row.get("address", ""),
                        row.get("telephone", ""), row.get("business_area", ""),
                        row.get("opening_hours", ""), row.get("operational_status", "unknown"),
                        row.get("map_match_confidence"), row.get("map_match_level", "unknown"),
                        row.get("geocode_status", "pending"),
                        row.get("map_review_status", "review_required"),
                        row.get("map_checked_at") or "", row.get("map_source", ""),
                        json.dumps(map_payload, ensure_ascii=False),
                        row.get("status", "unknown"),
                    ),
                )
            for row in entities:
                if row.get("parent_id"):
                    cursor.execute(
                        "UPDATE recommendation_entities SET parent_id=%s WHERE entity_id=%s",
                        (row["parent_id"], row["entity_id"]),
                    )
            for row in facts:
                payload = dict(row)
                cursor.execute(
                    """
                    INSERT INTO destination_facts (
                        destination_id, duration_min, duration_max, duration_source,
                        budget_min, budget_typical, budget_max, budget_basis,
                        budget_confidence, budget_filterable, requires_ferry,
                        best_season, operational_status, fact_payload
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
                    ON CONFLICT (destination_id) DO UPDATE SET
                        duration_min=EXCLUDED.duration_min,
                        duration_max=EXCLUDED.duration_max,
                        duration_source=EXCLUDED.duration_source,
                        budget_min=EXCLUDED.budget_min,
                        budget_typical=EXCLUDED.budget_typical,
                        budget_max=EXCLUDED.budget_max,
                        budget_basis=EXCLUDED.budget_basis,
                        budget_confidence=EXCLUDED.budget_confidence,
                        budget_filterable=EXCLUDED.budget_filterable,
                        requires_ferry=EXCLUDED.requires_ferry,
                        best_season=EXCLUDED.best_season,
                        operational_status=EXCLUDED.operational_status,
                        fact_payload=EXCLUDED.fact_payload,
                        updated_at=now()
                    """,
                    (
                        row["destination_id"], row.get("duration_min"), row.get("duration_max"),
                        row.get("duration_source"), row.get("budget_min"), row.get("budget_typical"),
                        row.get("budget_max"), row.get("budget_basis"), row.get("budget_confidence"),
                        row.get("budget_filterable", False), row.get("requires_ferry", False),
                        row.get("best_season"), row.get("operational_status", "unknown"),
                        json.dumps(payload, ensure_ascii=False),
                    ),
                )
            for row in travel:
                cursor.execute(
                    """
                    INSERT INTO travel_matrix (
                        destination_id, origin_city, transport_mode, travel_minutes,
                        distance_m, source, confidence, requires_ferry,
                        contains_ferry, note, failure_reason,
                        partial_failure_reasons, raw_status,
                        route_estimate, origin_name, origin_type,
                        door_to_door_min, door_to_door_typical, door_to_door_max,
                        rail_segment_min, rail_segment_typical, rail_segment_max,
                        access_egress_min, access_egress_typical, access_egress_max,
                        railway_segments, sample_dates, sample_times,
                        planned_sample_count, route_sample_count,
                        ferry_detection_sources, travel_payload, checked_at
                    ) VALUES (
                        %s,%s,%s,%s,%s,%s,%s,%s,
                        %s,%s,%s,%s,%s::jsonb,%s,%s,%s,
                        %s,%s,%s,%s,%s,%s,%s,%s,%s,
                        %s::jsonb,%s,%s,%s,%s,%s,%s::jsonb,
                        COALESCE(NULLIF(%s,'')::timestamptz,now())
                    )
                    ON CONFLICT (destination_id, origin_city, transport_mode) DO UPDATE SET
                        travel_minutes=EXCLUDED.travel_minutes,
                        distance_m=EXCLUDED.distance_m,
                        source=EXCLUDED.source,
                        confidence=EXCLUDED.confidence,
                        requires_ferry=EXCLUDED.requires_ferry,
                        contains_ferry=EXCLUDED.contains_ferry,
                        note=EXCLUDED.note,
                        failure_reason=EXCLUDED.failure_reason,
                        partial_failure_reasons=EXCLUDED.partial_failure_reasons,
                        raw_status=EXCLUDED.raw_status,
                        route_estimate=EXCLUDED.route_estimate,
                        origin_name=EXCLUDED.origin_name,
                        origin_type=EXCLUDED.origin_type,
                        door_to_door_min=EXCLUDED.door_to_door_min,
                        door_to_door_typical=EXCLUDED.door_to_door_typical,
                        door_to_door_max=EXCLUDED.door_to_door_max,
                        rail_segment_min=EXCLUDED.rail_segment_min,
                        rail_segment_typical=EXCLUDED.rail_segment_typical,
                        rail_segment_max=EXCLUDED.rail_segment_max,
                        access_egress_min=EXCLUDED.access_egress_min,
                        access_egress_typical=EXCLUDED.access_egress_typical,
                        access_egress_max=EXCLUDED.access_egress_max,
                        railway_segments=EXCLUDED.railway_segments,
                        sample_dates=EXCLUDED.sample_dates,
                        sample_times=EXCLUDED.sample_times,
                        planned_sample_count=EXCLUDED.planned_sample_count,
                        route_sample_count=EXCLUDED.route_sample_count,
                        ferry_detection_sources=EXCLUDED.ferry_detection_sources,
                        travel_payload=EXCLUDED.travel_payload,
                        checked_at=EXCLUDED.checked_at
                    """,
                    (
                        row["destination_id"], row["origin_city"], row["transport_mode"],
                        row.get("travel_minutes"), row.get("distance_m"),
                        text_or_default(row.get("source")),
                        text_or_default(row.get("confidence"), "低"),
                        row.get("requires_ferry", False), row.get("contains_ferry", False),
                        text_or_default(row.get("note")),
                        text_or_default(row.get("failure_reason")),
                        row.get("partial_failure_reasons") or [],
                        json.dumps(row.get("raw_status") or {}, ensure_ascii=False),
                        row.get("route_estimate", True), row.get("origin_name", ""),
                        row.get("origin_type", ""), row.get("door_to_door_min"),
                        row.get("door_to_door_typical"), row.get("door_to_door_max"),
                        row.get("rail_segment_min"), row.get("rail_segment_typical"),
                        row.get("rail_segment_max"), row.get("access_egress_min"),
                        row.get("access_egress_typical"), row.get("access_egress_max"),
                        json.dumps(row.get("railway_segments") or [], ensure_ascii=False),
                        row.get("sample_dates") or [], row.get("sample_times") or [],
                        row.get("planned_sample_count", 0), row.get("route_sample_count", 0),
                        row.get("ferry_detection_sources") or [],
                        json.dumps(row, ensure_ascii=False), row.get("checked_at") or "",
                    ),
                )
            deleted_stale_claims = upsert_claim_snapshot(cursor, claims, claim_ids)
            for row in profiles:
                cursor.execute(
                    """
                    INSERT INTO destination_profiles (
                        destination_id, mood_scores, vibe_scores, activity_scores,
                        core_feeling, atmosphere, suitable_scenes, activities,
                        limitations, positive_evidence_count, limitation_evidence_count,
                        evidence_quality, freshness_score, private_discovery_value, source_count
                    ) VALUES (%s,%s::jsonb,%s::jsonb,%s::jsonb,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (destination_id) DO UPDATE SET
                        mood_scores=EXCLUDED.mood_scores,
                        vibe_scores=EXCLUDED.vibe_scores,
                        activity_scores=EXCLUDED.activity_scores,
                        core_feeling=EXCLUDED.core_feeling,
                        atmosphere=EXCLUDED.atmosphere,
                        suitable_scenes=EXCLUDED.suitable_scenes,
                        activities=EXCLUDED.activities,
                        limitations=EXCLUDED.limitations,
                        positive_evidence_count=EXCLUDED.positive_evidence_count,
                        limitation_evidence_count=EXCLUDED.limitation_evidence_count,
                        evidence_quality=EXCLUDED.evidence_quality,
                        freshness_score=EXCLUDED.freshness_score,
                        private_discovery_value=EXCLUDED.private_discovery_value,
                        source_count=EXCLUDED.source_count,
                        profile_version='2.1.0',
                        updated_at=now()
                    """,
                    (
                        row["destination_id"], json.dumps(row["mood_scores"], ensure_ascii=False),
                        json.dumps(row["vibe_scores"], ensure_ascii=False),
                        json.dumps(row["activity_scores"], ensure_ascii=False), row["core_feeling"],
                        row["atmosphere"], json.dumps(row["suitable_scenes"], ensure_ascii=False),
                        json.dumps(row["activities"], ensure_ascii=False),
                        json.dumps(row["limitations"], ensure_ascii=False),
                        row["positive_evidence_count"], row["limitation_evidence_count"],
                        row["evidence_quality"], row["freshness_score"],
                        row["private_discovery_value"], row["source_count"],
                    ),
                )
        connection.commit()
    print(
        f"loaded entities={len(entities)}, map={len(map_enrichment)}, "
        f"facts={len(facts)}, travel={len(travel)}, "
        f"claims={len(claims)}, profiles={len(profiles)}, "
        f"deleted_stale_claims={deleted_stale_claims}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
