from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from jsonschema import validate

from inspitrip.paths import PRIVATE_DATA_DIR, REPO_ROOT, SCHEMA_DIR

ROOT = REPO_ROOT
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from inspitrip.recommendation.amap_place_enrichment import load_overrides, read_jsonl


DATA_DIR = PRIVATE_DATA_DIR / "generated"


def main() -> int:
    parser = argparse.ArgumentParser(description="离线验收 destination 地图补全快照")
    parser.add_argument("--entities", type=Path, default=DATA_DIR / "entities.jsonl")
    parser.add_argument(
        "--enrichment", type=Path, default=DATA_DIR / "destination_map_enrichment.jsonl"
    )
    parser.add_argument(
        "--overrides",
        type=Path,
        default=PRIVATE_DATA_DIR / "destination_map_overrides.json",
    )
    args = parser.parse_args()

    entity_schema = json.loads(
        (SCHEMA_DIR / "entity_schema.json").read_text(encoding="utf-8")
    )
    map_schema = json.loads(
        (SCHEMA_DIR / "destination_map_enrichment_schema.json").read_text(
            encoding="utf-8"
        )
    )
    entities = read_jsonl(args.entities)
    destinations = [row for row in entities if row.get("entity_type") == "destination"]
    records = read_jsonl(args.enrichment)
    overrides = load_overrides(args.overrides)
    errors: list[str] = []

    for row in entities:
        validate(row, entity_schema)
    for row in records:
        validate(row, map_schema)

    destination_ids = {row["entity_id"] for row in destinations}
    record_ids = [row["destination_id"] for row in records]
    if len(destinations) != 60:
        errors.append(f"destination 数应为 60，实际 {len(destinations)}")
    if len(record_ids) != len(set(record_ids)):
        errors.append("地图补全快照存在重复 destination_id")
    if set(record_ids) != destination_ids:
        errors.append("地图补全快照没有一一覆盖全部 destination")
    if set(overrides) != destination_ids:
        errors.append("人工库存审核没有一一覆盖全部 destination")

    for row in records:
        status = row["geocode_status"]
        policy = row["binding_policy"]
        has_coordinates = row.get("coordinates") is not None
        has_poi = bool(row.get("map_poi_id"))
        if status in {"matched", "manual_override"} and (not has_coordinates or not has_poi):
            errors.append(f"{row['destination_id']} 高置信匹配缺少坐标或 POI ID")
        if status == "matched" and policy == "auto":
            reasons = set(row.get("match_reasons") or [])
            if not ({"name_exact", "name_contains"} & reasons):
                errors.append(f"{row['destination_id']} 自动绑定缺少高置信名称匹配")
            if not ({"city_match", "district_match"} & reasons) or "province_match" not in reasons:
                errors.append(f"{row['destination_id']} 自动绑定缺少行政区匹配")
            if "poi_type_match" not in reasons:
                errors.append(f"{row['destination_id']} 自动绑定缺少目的地级 POI 类型匹配")
        if policy == "region" and has_poi:
            errors.append(f"{row['destination_id']} 区域型目的地错误绑定了单一 POI")
        if policy == "region" and status == "region_geocoded":
            if row.get("source") != "amap_geocode_v3":
                errors.append(f"{row['destination_id']} 区域型目的地没有使用行政地理编码")
            if "region_policy" not in set(row.get("match_reasons") or []):
                errors.append(f"{row['destination_id']} 区域型目的地缺少 region_policy 证据")
        if status == "review_required" and (has_coordinates or has_poi):
            errors.append(f"{row['destination_id']} 待审核结果不应写入正式坐标或 POI ID")
        if status not in {"pending", "excluded"} and not row.get("checked_at"):
            errors.append(f"{row['destination_id']} 已尝试结果缺少 checked_at")
        if status not in {"pending"} and row.get("source") == "unknown":
            errors.append(f"{row['destination_id']} 已尝试结果缺少 source")

    report = {
        "destination_count": len(destinations),
        "record_count": len(records),
        "override_count": len(overrides),
        "status_counts": dict(sorted(Counter(row["geocode_status"] for row in records).items())),
        "coordinate_count": sum(row.get("coordinates") is not None for row in records),
        "map_poi_count": sum(bool(row.get("map_poi_id")) for row in records),
        "validation_errors": errors,
    }
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
