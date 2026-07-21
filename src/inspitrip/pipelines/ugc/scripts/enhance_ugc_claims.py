#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from inspitrip.paths import PIPELINE_OUTPUT_DIR, PRIVATE_DATA_DIR, REPO_ROOT, SCHEMA_DIR

ROOT = REPO_ROOT
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from inspitrip.recommendation.ugc_claim_enhancement import (
    coverage_summary,
    generate_enhanced_claims,
    load_raw_notes,
    merge_claims,
)
from inspitrip.recommendation.v2_pipeline import (
    _validate_rows,
    build_profiles,
    load_jsonl,
    load_taxonomy_names,
    write_jsonl,
)


def _load_schema(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _merge_poi_sources(seed_rows: list[dict], tagged_rows: list[dict]) -> list[dict]:
    merged = {row["poi_id"]: dict(row) for row in tagged_rows if row.get("poi_id")}
    for row in seed_rows:
        if row.get("poi_id"):
            merged.setdefault(row["poi_id"], {}).update(row)
    return list(merged.values())


def main() -> int:
    parser = argparse.ArgumentParser(
        description="从原始 UGC 原文幂等补充目的地/体验级负面、条件和重点 aspect Claim"
    )
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    parser.add_argument("--dry-run", action="store_true", help="只审计，不写 Claim、画像或报告")
    parser.add_argument("--sample-limit", type=int, default=0, help="打印不含 URL 的增强 Claim 抽检样本")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=PRIVATE_DATA_DIR / "generated",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=PRIVATE_DATA_DIR / "generated" / "ugc_claim_enhancement_report.json",
    )
    args = parser.parse_args()

    data_dir = args.data_dir.resolve()
    annotate_output = PIPELINE_OUTPUT_DIR / "ugc"
    schema_dir = SCHEMA_DIR
    entities = load_jsonl(data_dir / "entities.jsonl")
    existing_claims = load_jsonl(data_dir / "ugc_claims.jsonl")
    facts = load_jsonl(data_dir / "destination_facts.jsonl")
    mentions = load_jsonl(annotate_output / "poi_mentions.jsonl")
    evidence_rows = load_jsonl(annotate_output / "ugc_evidence.jsonl")
    raw_notes = load_raw_notes(PRIVATE_DATA_DIR / "notes.csv")

    generated, generation_report = generate_enhanced_claims(
        raw_notes=raw_notes,
        mentions=mentions,
        evidence_rows=evidence_rows,
        entities=entities,
        existing_claims=existing_claims,
        today=args.as_of,
    )
    merged_claims, merge_report = merge_claims(existing_claims, generated)
    before_coverage = coverage_summary(existing_claims, entities)
    after_coverage = coverage_summary(merged_claims, entities)

    seed = load_jsonl(annotate_output / "poi_seed.jsonl")
    tagged = load_jsonl(PRIVATE_DATA_DIR / "legacy" / "poi_structured_tagged.jsonl")
    poi_rows = _merge_poi_sources(seed, tagged)
    poi_by_id = {str(row.get("poi_id") or ""): row for row in poi_rows}
    profiles = build_profiles(
        entities,
        merged_claims,
        poi_by_id,
        facts,
        load_taxonomy_names(schema_dir / "intent_taxonomy.json"),
        today=args.as_of,
    )

    _validate_rows(merged_claims, _load_schema(schema_dir / "ugc_claim_schema.json"), "claims")
    _validate_rows(profiles, _load_schema(schema_dir / "destination_profile_schema.json"), "profiles")

    report = {
        "version": "raw_sentence_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "as_of": args.as_of.isoformat(),
        "dry_run": args.dry_run,
        "inputs": {
            "raw_notes": len(raw_notes),
            "mentions": len(mentions),
            "evidence": len(evidence_rows),
            "entities": len(entities),
            "existing_claims": len(existing_claims),
        },
        "generation": generation_report,
        "merge": merge_report,
        "coverage_before": before_coverage,
        "coverage_after": after_coverage,
    }

    if not args.dry_run:
        write_jsonl(data_dir / "ugc_claims.jsonl", merged_claims)
        write_jsonl(data_dir / "destination_profiles.jsonl", profiles)
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    before = before_coverage["priority_any"]
    after = after_coverage["priority_any"]
    print(
        "UGC Claim enhancement "
        f"{'dry-run' if args.dry_run else 'complete'}: "
        f"generated={len(generated)}, final={len(merged_claims)}, "
        f"added={merge_report['added']}, updated={merge_report['updated']}, "
        f"removed_ads={merge_report['removed_ads']}"
    )
    print(
        "priority coverage: "
        f"destinations {before['destinations']}->{after['destinations']}/"
        f"{after_coverage['total_destinations']}, claims {before['claims']}->{after['claims']}"
    )
    print(
        "conditional coverage: "
        f"destinations {before_coverage['conditions']['destinations']}->"
        f"{after_coverage['conditions']['destinations']}/"
        f"{after_coverage['total_destinations']}, claims "
        f"{before_coverage['conditions']['claims']}->{after_coverage['conditions']['claims']}"
    )
    if args.sample_limit > 0:
        entity_names = {
            str(row.get("entity_id") or ""): str(row.get("name") or "")
            for row in entities
        }
        sample_rows = sorted(
            generated,
            key=lambda row: (
                {"negative": 0, "mixed": 1, "neutral": 2, "positive": 3}.get(row["polarity"], 4),
                row["aspect"],
                row["claim_id"],
            ),
        )
        for row in sample_rows[: args.sample_limit]:
            print(
                json.dumps(
                    {
                        "entity": entity_names.get(row["entity_id"], row["entity_id"]),
                        "aspect": row["aspect"],
                        "polarity": row["polarity"],
                        "conditions": row["conditions"],
                        "claim": row["claim"],
                    },
                    ensure_ascii=False,
                )
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
