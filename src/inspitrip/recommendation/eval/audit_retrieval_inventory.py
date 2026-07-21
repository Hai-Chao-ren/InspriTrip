from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import yaml

from inspitrip.paths import DEMO_DATA_DIR, PIPELINE_OUTPUT_DIR, REPO_ROOT


ROOT = Path(__file__).resolve().parents[3]


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit active destination inventory against Dify retrieval inputs.")
    parser.add_argument("--entities", type=Path, default=DEMO_DATA_DIR / "entities.jsonl")
    parser.add_argument("--documents", type=Path, default=PIPELINE_OUTPUT_DIR / "dify" / "destination_documents.jsonl")
    parser.add_argument("--workflow", type=Path, default=REPO_ROOT / "workflows" / "dify" / "InspiTrip.template.yml")
    parser.add_argument("--retrieval-gold", type=Path, default=Path(__file__).resolve().parent / "retrieval_gold.jsonl")
    args = parser.parse_args()

    active_ids = {
        str(row["entity_id"])
        for row in load_jsonl(args.entities)
        if row.get("entity_type") == "destination" and row.get("status") == "active"
    }
    documents = load_jsonl(args.documents)
    document_ids = [str(row.get("destination_id") or (row.get("metadata") or {}).get("destination_id") or "") for row in documents]
    workflow = yaml.safe_load(args.workflow.read_text(encoding="utf-8"))
    retrieval_nodes = [
        node for node in workflow["workflow"]["graph"]["nodes"]
        if node.get("data", {}).get("type") == "knowledge-retrieval"
    ]
    top_k_values = [int((node["data"].get("multiple_retrieval_config") or {}).get("top_k") or 0) for node in retrieval_nodes]
    gold = load_jsonl(args.retrieval_gold)
    gold_relevant = {str(value) for row in gold for value in row.get("relevant_destination_ids") or []}
    current_top_k = max(top_k_values, default=0)
    report = {
        "ok": (
            len(active_ids) == 60
            and len(documents) == 60
            and len(set(document_ids)) == 60
            and set(document_ids) == active_ids
            and gold_relevant == active_ids
        ),
        "active_destination_count": len(active_ids),
        "document_count": len(documents),
        "unique_document_destination_count": len(set(document_ids)),
        "document_missing_active_count": len(active_ids - set(document_ids)),
        "document_unknown_count": len(set(document_ids) - active_ids),
        "retrieval_node_count": len(retrieval_nodes),
        "configured_top_k": top_k_values,
        "configured_inventory_ceiling": min(current_top_k, len(active_ids)),
        "configured_inventory_gap": max(len(active_ids) - current_top_k, 0),
        "recommended_top_k": len(active_ids),
        "inventory_supplement_required_if_top_k_unchanged": current_top_k < len(active_ids),
        "retrieval_gold_count": len(gold),
        "retrieval_gold_destination_coverage": len(gold_relevant),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
