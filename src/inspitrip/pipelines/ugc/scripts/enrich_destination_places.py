from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from inspitrip.paths import PRIVATE_DATA_DIR, REPO_ROOT

ROOT = REPO_ROOT
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from inspitrip.recommendation.amap_place_enrichment import (
    AmapPlaceClient,
    apply_enrichment_to_entities,
    build_enrichment_records,
    build_failure_records,
    load_amap_key,
    load_overrides,
    read_jsonl,
    status_counts,
    utc_now,
    write_json_atomic,
    write_jsonl_atomic,
)


DATA_DIR = PRIVATE_DATA_DIR / "generated"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="使用高德 Place/POI 搜索补全 60 个 destination；低置信结果只进入人工审核"
    )
    parser.add_argument("--entities", type=Path, default=DATA_DIR / "entities.jsonl")
    parser.add_argument(
        "--overrides",
        type=Path,
        default=PRIVATE_DATA_DIR / "destination_map_overrides.json",
    )
    parser.add_argument(
        "--output", type=Path, default=DATA_DIR / "destination_map_enrichment.jsonl"
    )
    parser.add_argument(
        "--failures", type=Path, default=DATA_DIR / "destination_map_failures.jsonl"
    )
    parser.add_argument(
        "--cache-dir", type=Path, default=DATA_DIR / "amap_place_cache"
    )
    parser.add_argument(
        "--report", type=Path, default=DATA_DIR / "destination_map_run_report.json"
    )
    parser.add_argument("--limit", type=int, help="本次最多处理的待补全 destination 数")
    parser.add_argument("--qps", type=float, default=2.0)
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument("--offline", action="store_true", help="只使用人工覆盖和已有缓存")
    parser.add_argument("--refresh", action="store_true", help="忽略已有终态重新计算")
    parser.add_argument("--no-resume", action="store_true", help="不复用已有输出终态")
    parser.add_argument("--no-apply", action="store_true", help="不合并回 entities.jsonl")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    entities = read_jsonl(args.entities)
    destinations = [row for row in entities if row.get("entity_type") == "destination"]
    if not destinations:
        raise SystemExit("entities.jsonl 中没有 destination")
    overrides = load_overrides(args.overrides)
    existing = read_jsonl(args.output)
    api_key = None if args.offline else load_amap_key()
    client = AmapPlaceClient(
        api_key,
        args.cache_dir,
        qps=args.qps,
        max_retries=args.max_retries,
        offline=args.offline,
    )
    records = build_enrichment_records(
        entities,
        overrides,
        client,
        existing=existing,
        limit=args.limit,
        resume=not args.no_resume,
        refresh=args.refresh,
    )
    write_jsonl_atomic(args.output, records)
    write_jsonl_atomic(args.failures, build_failure_records(records))
    if not args.no_apply:
        write_jsonl_atomic(args.entities, apply_enrichment_to_entities(entities, records))

    failures = build_failure_records(records)
    summary = {
        "last_run_at": utc_now(),
        "destination_count": len(destinations),
        "override_count": sum(1 for row in destinations if row["entity_id"] in overrides),
        "status_counts": status_counts(records),
        "failure_count": len(failures),
        "high_confidence_poi_count": sum(
            1 for row in records if row.get("geocode_status") in {"matched", "manual_override"} and row.get("map_poi_id")
        ),
        "region_geocoded_count": sum(1 for row in records if row.get("geocode_status") == "region_geocoded"),
        "review_required_count": sum(1 for row in records if row.get("geocode_status") == "review_required"),
        "api_configuration_error": any(
            "INVALID_USER_KEY" in str(row.get("failure_reason") or "") for row in failures
        ),
        "applied_to_entities": not args.no_apply,
        "offline": args.offline,
        "limit": args.limit,
    }
    write_json_atomic(args.report, summary)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
