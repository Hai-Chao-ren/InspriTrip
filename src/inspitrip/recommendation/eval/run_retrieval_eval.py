from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from inspitrip.paths import DEMO_DATA_DIR


DEFAULT_GOLD = Path(__file__).resolve().parent / "retrieval_gold.jsonl"
DEFAULT_ENTITIES = DEMO_DATA_DIR / "entities.jsonl"


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: {exc}") from exc
    return rows


def _candidate_ids(row: dict[str, Any]) -> list[str]:
    direct = row.get("retrieved_destination_ids")
    if isinstance(direct, list):
        values = direct
    else:
        values = []
        for item in row.get("items") or row.get("retrieval_items") or []:
            metadata = item.get("metadata") or {}
            nested = metadata.get("doc_metadata") if isinstance(metadata.get("doc_metadata"), dict) else {}
            destination_id = (
                item.get("destination_id")
                or metadata.get("destination_id")
                or metadata.get("entity_id")
                or nested.get("destination_id")
                or nested.get("entity_id")
            )
            if destination_id:
                values.append(destination_id)
    result = []
    for value in values:
        value = str(value)
        if value and value not in result:
            result.append(value)
    return result


def _active_ids(path: Path) -> set[str]:
    return {
        str(row["entity_id"])
        for row in load_jsonl(path)
        if row.get("entity_type") == "destination" and row.get("status") == "active"
    }


def _ratio(numerator: int | float, denominator: int | float) -> float | None:
    return round(float(numerator) / float(denominator), 6) if denominator else None


def evaluate(
    gold_rows: list[dict[str, Any]],
    predictions: dict[str, list[str]],
    *,
    active_ids: set[str],
    cutoffs: tuple[int, ...] = (20, 30, 60),
) -> dict[str, Any]:
    totals: Counter[str] = Counter()
    bucket_totals: Counter[tuple[str, int]] = Counter()
    bucket_recall_sum: Counter[tuple[str, int]] = Counter()
    invalid_predictions: set[str] = set()
    inventory_seen: dict[int, set[str]] = {cutoff: set() for cutoff in cutoffs}
    reciprocal_rank_sum = 0.0
    evaluated = 0
    missing = []

    for gold in gold_rows:
        case_id = str(gold["id"])
        if case_id not in predictions:
            missing.append(case_id)
            continue
        evaluated += 1
        retrieved = predictions[case_id]
        invalid_predictions.update(set(retrieved) - active_ids)
        relevant = {str(value) for value in gold.get("relevant_destination_ids") or []}
        bucket = str(gold.get("bucket") or "unknown")
        ranks = [index for index, destination_id in enumerate(retrieved, 1) if destination_id in relevant]
        if ranks:
            reciprocal_rank_sum += 1.0 / min(ranks)
        for cutoff in cutoffs:
            top = set(retrieved[:cutoff])
            inventory_seen[cutoff].update(top & active_ids)
            hits = len(relevant & top)
            recall = hits / len(relevant) if relevant else 1.0
            totals[f"recall_sum_{cutoff}"] += recall
            totals[f"hit_{cutoff}"] += int(hits > 0)
            bucket_totals[(bucket, cutoff)] += 1
            bucket_recall_sum[(bucket, cutoff)] += recall

    return {
        "gold_count": len(gold_rows),
        "evaluated_count": evaluated,
        "missing_prediction_count": len(missing),
        "recall": {str(cutoff): _ratio(totals[f"recall_sum_{cutoff}"], evaluated) for cutoff in cutoffs},
        "hit_rate": {str(cutoff): _ratio(totals[f"hit_{cutoff}"], evaluated) for cutoff in cutoffs},
        "mrr": _ratio(reciprocal_rank_sum, evaluated),
        "active_inventory_coverage": {str(cutoff): _ratio(len(inventory_seen[cutoff]), len(active_ids)) for cutoff in cutoffs},
        "bucket_recall": {
            bucket: {
                str(cutoff): _ratio(bucket_recall_sum[(bucket, cutoff)], bucket_totals[(bucket, cutoff)])
                for cutoff in cutoffs
            }
            for bucket in sorted({key[0] for key in bucket_totals})
        },
        "invalid_destination_ids": sorted(invalid_predictions),
        "missing_prediction_ids": missing,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Compute Recall@20/30/60 from exported Dify retrieval results.")
    parser.add_argument("--gold", type=Path, default=DEFAULT_GOLD)
    parser.add_argument("--predictions", type=Path, required=True, help="JSONL rows with id and retrieved_destination_ids/items.")
    parser.add_argument("--entities", type=Path, default=DEFAULT_ENTITIES)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--cutoffs", default="20,30,60", help="Comma-separated positive rank cutoffs.")
    parser.add_argument("--require-all", action="store_true")
    args = parser.parse_args()
    gold_rows = load_jsonl(args.gold)
    predictions = {str(row["id"]): _candidate_ids(row) for row in load_jsonl(args.predictions)}
    cutoffs = tuple(dict.fromkeys(int(value) for value in args.cutoffs.split(",") if int(value) > 0))
    if not cutoffs:
        raise SystemExit("--cutoffs must contain at least one positive integer")
    report = evaluate(gold_rows, predictions, active_ids=_active_ids(args.entities), cutoffs=cutoffs)
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    if report["invalid_destination_ids"]:
        return 3
    if args.require_all and report["missing_prediction_count"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
