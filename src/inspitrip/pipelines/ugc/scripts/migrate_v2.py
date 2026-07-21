#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from inspitrip.paths import PIPELINE_OUTPUT_DIR, PRIVATE_DATA_DIR, REPO_ROOT, SCHEMA_DIR

ROOT = REPO_ROOT
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from inspitrip.recommendation.v2_pipeline import build_v2_dataset, load_jsonl, write_jsonl


def _load_schema(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _merge_poi_sources(seed_rows: list[dict], tagged_rows: list[dict]) -> list[dict]:
    merged = {row["poi_id"]: dict(row) for row in tagged_rows if row.get("poi_id")}
    for row in seed_rows:
        if row.get("poi_id"):
            merged.setdefault(row["poi_id"], {}).update(row)
    return list(merged.values())


def main() -> int:
    parser = argparse.ArgumentParser(description="把 InspiTrip v1 POI/UGC 数据迁移为 v2 三层结构")
    parser.add_argument("--output-dir", type=Path, default=PRIVATE_DATA_DIR / "generated")
    parser.add_argument("--overrides", type=Path, default=PRIVATE_DATA_DIR / "entity_overrides.json")
    args = parser.parse_args()

    annotate_output = PIPELINE_OUTPUT_DIR / "ugc"
    dify = PRIVATE_DATA_DIR / "legacy" / "dify"
    schema_dir = SCHEMA_DIR
    seed = load_jsonl(annotate_output / "poi_seed.jsonl")
    tagged = load_jsonl(dify / "poi_structured_tagged.jsonl")
    poi_rows = _merge_poi_sources(seed, tagged)
    mention_rows = load_jsonl(annotate_output / "poi_mentions.jsonl")
    evidence_rows = load_jsonl(annotate_output / "ugc_evidence.jsonl")
    overrides = {}
    if args.overrides.exists():
        overrides = json.loads(args.overrides.read_text(encoding="utf-8"))

    result = build_v2_dataset(
        poi_rows=poi_rows,
        mention_rows=mention_rows,
        evidence_rows=evidence_rows,
        alias_map_path=PRIVATE_DATA_DIR / "alias_map.csv",
        taxonomy_path=schema_dir / "intent_taxonomy.json",
        schemas={
            "entity": _load_schema(schema_dir / "entity_schema.json"),
            "claim": _load_schema(schema_dir / "ugc_claim_schema.json"),
            "profile": _load_schema(schema_dir / "destination_profile_schema.json"),
        },
        overrides=overrides,
    )

    output = args.output_dir.resolve()
    write_jsonl(output / "entities.jsonl", result["entities"])
    write_jsonl(output / "ugc_claims.jsonl", result["claims"])
    write_jsonl(output / "destination_facts.jsonl", result["facts"])
    write_jsonl(output / "destination_profiles.jsonl", result["profiles"])
    write_jsonl(output / "travel_matrix.jsonl", [])
    print(
        f"v2 migration complete: entities={len(result['entities'])}, "
        f"claims={len(result['claims'])}, destinations={len(result['profiles'])}"
    )
    print(f"output: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
