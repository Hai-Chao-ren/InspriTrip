from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

import psycopg
from dotenv import dotenv_values

from inspitrip.paths import PRIVATE_DATA_DIR


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PRIVATE_DATA_DIR / "generated"

REQUIRED_MAP_COLUMNS = {
    "longitude",
    "latitude",
    "map_poi_id",
    "standard_province",
    "standard_city",
    "standard_district",
    "adcode",
    "address",
    "telephone",
    "business_area",
    "opening_hours",
    "map_operational_status",
    "map_match_confidence",
    "map_match_level",
    "geocode_status",
    "map_review_status",
    "map_checked_at",
    "map_source",
    "map_payload",
}

REQUIRED_TRAVEL_COLUMNS = {
    "travel_minutes",
    "distance_m",
    "requires_ferry",
    "contains_ferry",
    "failure_reason",
    "partial_failure_reasons",
    "raw_status",
    "route_estimate",
    "origin_name",
    "origin_type",
    "door_to_door_min",
    "door_to_door_typical",
    "door_to_door_max",
    "rail_segment_min",
    "rail_segment_typical",
    "rail_segment_max",
    "access_egress_min",
    "access_egress_typical",
    "access_egress_max",
    "railway_segments",
    "sample_dates",
    "sample_times",
    "planned_sample_count",
    "route_sample_count",
    "ferry_detection_sources",
    "travel_payload",
    "checked_at",
}


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path.name} 第 {line_number} 行不是完整 JSON") from exc
    return rows


def scalar(cursor: psycopg.Cursor, query: str, params: tuple[Any, ...] | None = None) -> int:
    cursor.execute(query, params)
    return int(cursor.fetchone()[0])


def column_names(cursor: psycopg.Cursor, table_name: str) -> set[str]:
    cursor.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema=current_schema() AND table_name=%s",
        (table_name,),
    )
    return {str(row[0]) for row in cursor.fetchall()}


def optional_scalar(
    cursor: psycopg.Cursor,
    available_columns: set[str],
    required_columns: set[str],
    query: str,
) -> int | None:
    if not required_columns.issubset(available_columns):
        return None
    return scalar(cursor, query)


def interval_invalid_sql(prefix: str) -> str:
    minimum = f"{prefix}_min"
    typical = f"{prefix}_typical"
    maximum = f"{prefix}_max"
    return (
        f"(num_nonnulls({minimum},{typical},{maximum}) NOT IN (0,3) "
        f"OR ({minimum} IS NOT NULL AND NOT ({minimum} <= {typical} AND {typical} <= {maximum})))"
    )


def report_is_ok(report: dict[str, Any]) -> bool:
    return bool(
        report["entities"] == 342
        and report["facts"] == 60
        and report["claims"] == report["claim_snapshot_rows"]
        and report["profiles"] == 60
        and report["destinations"] == report["map_snapshot_rows"] == 60
        and report["travel"] == report["travel_snapshot_rows"]
        and report["travel"] > 0
        and report["travel_missing_snapshot_keys"] == 0
        and report["travel_extra_database_keys"] == 0
        and report["travel_payload_rows"] == report["travel"]
        and report["travel_payload_mismatches"] == 0
        and report["travel_invalid_outcomes"] == 0
        and report["travel_invalid_intervals"] == 0
        and report["travel_invalid_samples"] == 0
        and report["travel_invalid_ferry"] == 0
        and report["travel_railway_rows"] == report["snapshot_travel_railway_rows"]
        and report["travel_partial_failure_rows"]
        == report["snapshot_travel_partial_failure_rows"]
        and report["travel_requires_ferry"] == report["snapshot_travel_requires_ferry"]
        and report["travel_contains_ferry"] == report["snapshot_travel_contains_ferry"]
        and report["map_payload_rows"] == report["map_snapshot_rows"]
        and report["map_status_counts"] == report["snapshot_map_status_counts"]
        and report["map_invalid_rows"] == 0
        and report["map_invalid_auto_bindings"] == 0
        and report["map_api_failed"] == 0
        and report["map_pending"] == 0
        and not report["missing_map_columns"]
        and not report["missing_travel_columns"]
    )


def main() -> int:
    database_url = str(dotenv_values(ROOT / ".env").get("DATABASE_URL") or "").strip()
    if not database_url:
        print(json.dumps({"ok": False, "reason": "DATABASE_URL missing"}))
        return 2

    try:
        travel_snapshot = load_jsonl(DATA_DIR / "travel_matrix.jsonl")
        map_snapshot = load_jsonl(DATA_DIR / "destination_map_enrichment.jsonl")
        claim_snapshot = load_jsonl(DATA_DIR / "ugc_claims.jsonl")
    except ValueError as exc:
        print(json.dumps({"ok": False, "reason": str(exc)}, ensure_ascii=False))
        return 2
    snapshot_travel_keys = {
        (row["destination_id"], row["origin_city"], row["transport_mode"])
        for row in travel_snapshot
    }

    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            entity_columns = column_names(cursor, "recommendation_entities")
            travel_columns = column_names(cursor, "travel_matrix")
            report: dict[str, Any] = {
                "entities": scalar(cursor, "SELECT count(*) FROM recommendation_entities"),
                "destinations": scalar(
                    cursor,
                    "SELECT count(*) FROM recommendation_entities WHERE entity_type='destination'",
                ),
                "facts": scalar(cursor, "SELECT count(*) FROM destination_facts"),
                "claims": scalar(cursor, "SELECT count(*) FROM ugc_evidence_claims"),
                "profiles": scalar(cursor, "SELECT count(*) FROM destination_profiles"),
                "travel": scalar(cursor, "SELECT count(*) FROM travel_matrix"),
                "travel_snapshot_rows": len(travel_snapshot),
                "map_snapshot_rows": len(map_snapshot),
                "claim_snapshot_rows": len(claim_snapshot),
                "missing_map_columns": sorted(REQUIRED_MAP_COLUMNS - entity_columns),
                "missing_travel_columns": sorted(REQUIRED_TRAVEL_COLUMNS - travel_columns),
                "travel_with_minutes": scalar(
                    cursor, "SELECT count(*) FROM travel_matrix WHERE travel_minutes IS NOT NULL"
                ),
                "travel_failures": scalar(
                    cursor,
                    "SELECT count(*) FROM travel_matrix "
                    "WHERE travel_minutes IS NULL AND failure_reason <> ''",
                ),
                "travel_payload_rows": scalar(
                    cursor, "SELECT count(*) FROM travel_matrix WHERE travel_payload <> '{}'::jsonb"
                ),
                "travel_payload_mismatches": scalar(
                    cursor,
                    "SELECT count(*) FROM travel_matrix WHERE "
                    "travel_payload->>'destination_id' IS DISTINCT FROM destination_id "
                    "OR travel_payload->>'origin_city' IS DISTINCT FROM origin_city "
                    "OR travel_payload->>'transport_mode' IS DISTINCT FROM transport_mode",
                ),
                "travel_invalid_outcomes": scalar(
                    cursor,
                    "SELECT count(*) FROM travel_matrix WHERE "
                    "(travel_minutes IS NULL AND failure_reason = '') "
                    "OR (travel_minutes IS NOT NULL AND failure_reason <> '') "
                    "OR (travel_minutes IS NOT NULL AND door_to_door_typical IS DISTINCT FROM travel_minutes)",
                ),
                "travel_invalid_intervals": scalar(
                    cursor,
                    "SELECT count(*) FROM travel_matrix WHERE "
                    + " OR ".join(
                        interval_invalid_sql(prefix)
                        for prefix in ("door_to_door", "rail_segment", "access_egress")
                    ),
                ),
                "travel_invalid_samples": scalar(
                    cursor,
                    "SELECT count(*) FROM travel_matrix WHERE planned_sample_count < 0 "
                    "OR route_sample_count < 0 OR route_sample_count > planned_sample_count",
                ),
                "travel_invalid_ferry": scalar(
                    cursor,
                    "SELECT count(*) FROM travel_matrix WHERE contains_ferry AND NOT requires_ferry",
                ),
                "travel_railway_rows": scalar(
                    cursor,
                    "SELECT count(*) FROM travel_matrix WHERE jsonb_array_length(railway_segments) > 0",
                ),
                "travel_partial_failure_rows": optional_scalar(
                    cursor,
                    travel_columns,
                    {"partial_failure_reasons"},
                    "SELECT count(*) FROM travel_matrix WHERE cardinality(partial_failure_reasons) > 0",
                ),
                "travel_requires_ferry": scalar(
                    cursor, "SELECT count(*) FROM travel_matrix WHERE requires_ferry"
                ),
                "travel_contains_ferry": scalar(
                    cursor, "SELECT count(*) FROM travel_matrix WHERE contains_ferry"
                ),
                "map_payload_rows": optional_scalar(
                    cursor,
                    entity_columns,
                    {"map_payload"},
                    "SELECT count(*) FROM recommendation_entities "
                    "WHERE entity_type='destination' AND map_payload <> '{}'::jsonb",
                ),
                "map_matched": scalar(
                    cursor,
                    "SELECT count(*) FROM recommendation_entities "
                    "WHERE entity_type='destination' AND geocode_status "
                    "IN ('matched','region_geocoded','manual_override')",
                ),
                "map_api_failed": scalar(
                    cursor,
                    "SELECT count(*) FROM recommendation_entities "
                    "WHERE entity_type='destination' AND geocode_status='api_failed'",
                ),
                "map_pending": scalar(
                    cursor,
                    "SELECT count(*) FROM recommendation_entities "
                    "WHERE entity_type='destination' AND geocode_status='pending'",
                ),
                "map_invalid_rows": scalar(
                    cursor,
                    "SELECT count(*) FROM recommendation_entities WHERE entity_type='destination' AND ("
                    "(geocode_status IN ('matched','manual_override') "
                    "AND (longitude IS NULL OR latitude IS NULL OR map_poi_id IS NULL)) "
                    "OR (geocode_status='region_geocoded' "
                    "AND (longitude IS NULL OR latitude IS NULL OR map_poi_id IS NOT NULL)) "
                    "OR (geocode_status='review_required' "
                    "AND (longitude IS NOT NULL OR latitude IS NOT NULL OR map_poi_id IS NOT NULL)))",
                ),
                "map_invalid_auto_bindings": optional_scalar(
                    cursor,
                    entity_columns,
                    {"map_payload"},
                    "SELECT count(*) FROM recommendation_entities "
                    "WHERE entity_type='destination' AND geocode_status='matched' "
                    "AND map_payload->>'binding_policy'='auto' AND ("
                    "NOT (map_payload->'match_reasons' ?| ARRAY['name_exact','name_contains']) "
                    "OR NOT (map_payload->'match_reasons' ?| ARRAY['city_match','district_match']) "
                    "OR NOT (map_payload->'match_reasons' ? 'province_match') "
                    "OR NOT (map_payload->'match_reasons' ? 'poi_type_match'))",
                ),
            }
            cursor.execute(
                "SELECT destination_id, origin_city, transport_mode FROM travel_matrix"
            )
            database_travel_keys = {tuple(row) for row in cursor.fetchall()}
            report["travel_missing_snapshot_keys"] = len(
                snapshot_travel_keys - database_travel_keys
            )
            report["travel_extra_database_keys"] = len(
                database_travel_keys - snapshot_travel_keys
            )
            cursor.execute(
                "SELECT origin_city, transport_mode, count(*) "
                "FROM travel_matrix GROUP BY origin_city, transport_mode "
                "ORDER BY origin_city, transport_mode"
            )
            report["origin_mode_counts"] = [list(row) for row in cursor.fetchall()]
            cursor.execute(
                "SELECT geocode_status, count(*) FROM recommendation_entities "
                "WHERE entity_type='destination' GROUP BY geocode_status ORDER BY geocode_status"
            )
            report["map_status_counts"] = {str(status): int(count) for status, count in cursor.fetchall()}

    report["snapshot_travel_status_counts"] = dict(
        sorted(
            Counter(
                "success" if row.get("travel_minutes") is not None else "failure"
                for row in travel_snapshot
            ).items()
        )
    )
    report["snapshot_travel_railway_rows"] = sum(
        bool(row.get("railway_segments")) for row in travel_snapshot
    )
    report["snapshot_travel_partial_failure_rows"] = sum(
        bool(row.get("partial_failure_reasons")) for row in travel_snapshot
    )
    report["snapshot_travel_requires_ferry"] = sum(
        bool(row.get("requires_ferry")) for row in travel_snapshot
    )
    report["snapshot_travel_contains_ferry"] = sum(
        bool(row.get("contains_ferry")) for row in travel_snapshot
    )
    report["snapshot_map_status_counts"] = dict(
        sorted(Counter(str(row.get("geocode_status") or "pending") for row in map_snapshot).items())
    )
    report["ok"] = report_is_ok(report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
