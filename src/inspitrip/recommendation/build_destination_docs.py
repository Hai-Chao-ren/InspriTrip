from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from inspitrip.paths import DEMO_DATA_DIR, PIPELINE_OUTPUT_DIR

from .v2_pipeline import load_jsonl, write_jsonl


PLACEHOLDER_VALUES = {
    "体验感待更多 UGC 补充",
    "体验感待核实",
    "氛围待核实",
    "待更多 UGC 补充",
    "待核实",
    "暂无高置信限制信息",
    "当前证据不足",
    "0 条正向体验证据，0 条限制或争议信息",
}


def _clean_text(value: Any) -> str:
    text = str(value or "").strip()
    if text in PLACEHOLDER_VALUES:
        return ""
    return text.removeprefix("体验感待更多 UGC 补充；").strip()


def _clean_items(values: Any) -> list[str]:
    return [text for value in (values or []) if (text := _clean_text(value))]


def render_destination_document(profile: dict[str, Any]) -> str:
    header = [f"地点：{profile['name']}"]
    aliases = _clean_items(profile.get("aliases"))
    if aliases:
        header.append(f"别名：{'、'.join(aliases)}")
    region = f"{profile.get('province', '')}{profile.get('city', '')}".strip()
    if region:
        header.append(f"区域：{region}")

    sections: list[str] = ["\n".join(header)]
    values = [
        ("核心感觉", _clean_text(profile.get("core_feeling"))),
        ("氛围特征", _clean_text(profile.get("atmosphere"))),
        ("适合场景", "；".join(_clean_items(profile.get("suitable_scenes")))),
        ("主要活动", "、".join(_clean_items(profile.get("activities")))),
        ("不适合与限制", "；".join(_clean_items(profile.get("limitations")))),
    ]
    sections.extend(f"{label}：\n{value}" for label, value in values if value)
    return "\n\n".join(sections)


def build_metadata(profile: dict[str, Any]) -> dict[str, Any]:
    facts = dict(profile.get("metadata") or {})
    return {
        "destination_id": profile["destination_id"],
        "entity_type": "destination",
        "province": profile.get("province", ""),
        "city": profile.get("city", ""),
        "category": profile.get("category", ""),
        "duration_min": facts.get("duration_min"),
        "duration_max": facts.get("duration_max"),
        "budget_typical": facts.get("budget_typical"),
        "budget_confidence": facts.get("budget_confidence") or "",
        "budget_filterable": "true" if facts.get("budget_filterable") else "false",
        "confidence_level": (
            "高" if profile.get("evidence_quality", 0) >= 0.75
            else "中" if profile.get("evidence_quality", 0) >= 0.45
            else "低"
        ),
        "source_count": profile.get("source_count", 0),
        "positive_evidence_count": profile.get("positive_evidence_count", 0),
        "limitation_evidence_count": profile.get("limitation_evidence_count", 0),
        "evidence_quality": profile.get("evidence_quality", 0),
        "freshness_score": profile.get("freshness_score", 0),
        "private_discovery_value": profile.get("private_discovery_value", 0),
        "operational_status": profile.get("status", "unknown"),
    }


def build_documents(profiles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "destination_id": profile["destination_id"],
            "name": profile["name"],
            "text": render_destination_document(profile),
            "metadata": build_metadata(profile),
        }
        for profile in profiles
        if profile.get("status") == "active"
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 Dify v2 目的地画像文档")
    parser.add_argument(
        "--profiles",
        type=Path,
        default=DEMO_DATA_DIR / "destination_profiles.jsonl",
    )
    parser.add_argument(
        "--output-dir", type=Path, default=PIPELINE_OUTPUT_DIR / "dify"
    )
    args = parser.parse_args()
    documents = build_documents(load_jsonl(args.profiles))
    write_jsonl(args.output_dir / "destination_documents.jsonl", documents)
    markdown = "\n\n---\n\n".join(row["text"] for row in documents)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "destination_profiles.md").write_text(markdown + "\n", encoding="utf-8")
    print(f"destination documents={len(documents)} -> {args.output_dir.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
