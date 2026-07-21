from __future__ import annotations

import argparse
import importlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DEFAULT_GOLD = Path(__file__).resolve().parent / "query_gold.jsonl"


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: {exc}") from exc
    return rows


def load_callable(spec: str | None) -> Callable[..., Any] | None:
    if not spec:
        return None
    module_name, separator, attribute = spec.partition(":")
    if not separator:
        raise ValueError("callable must use module:function syntax")
    return getattr(importlib.import_module(module_name), attribute)


def _tag_ids(value: Any) -> list[str]:
    result = []
    for item in value or []:
        if isinstance(item, str):
            result.append(item)
        elif isinstance(item, dict) and item.get("id"):
            result.append(str(item["id"]))
    return result


def _set(value: Any) -> set[str]:
    return {str(item) for item in (value or [])}


def _safe_ratio(numerator: int | float, denominator: int | float) -> float | None:
    if not denominator:
        return None
    return round(float(numerator) / float(denominator), 6)


def _macro_f1(expected: list[str], predicted: list[str]) -> float | None:
    labels = sorted(set(expected) | set(predicted))
    if not labels:
        return None
    scores = []
    for label in labels:
        tp = sum(1 for gold, pred in zip(expected, predicted) if gold == label and pred == label)
        fp = sum(1 for gold, pred in zip(expected, predicted) if gold != label and pred == label)
        fn = sum(1 for gold, pred in zip(expected, predicted) if gold == label and pred != label)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        scores.append(2 * precision * recall / (precision + recall) if precision + recall else 0.0)
    return round(sum(scores) / len(scores), 6)


def _predict_from_parser(
    row: dict[str, Any],
    parser: Callable[[str], dict[str, Any]],
    envelope_parser: Callable[[str], dict[str, Any]] | None,
    merge: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]] | None,
) -> tuple[dict[str, Any] | None, str | None]:
    turns = row.get("turns") or []
    if len(turns) == 1:
        return parser(str(turns[0].get("query") or "")), None
    if not envelope_parser or not merge:
        return None, "multi_turn_requires_envelope_parser_and_merge"
    state: dict[str, Any] | None = None
    for turn in turns:
        envelope = envelope_parser(str(turn.get("query") or ""))
        if state is None:
            state = dict(envelope.get("query_plan") or envelope)
        else:
            state = merge(state, envelope)
            if "query_plan" in state:
                state = dict(state["query_plan"])
    return state, None


def _compare_case(expected: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    def add(field: str, gold: Any, pred: Any, *, mode: str = "equal") -> None:
        if mode == "set":
            passed = _set(gold) == _set(pred)
        elif mode == "contains_set":
            passed = _set(gold) <= _set(pred)
        else:
            passed = gold == pred
        checks.append({"field": field, "passed": passed, "expected": gold, "predicted": pred})

    for field in ("scope", "task_type", "target_destination"):
        if field in expected:
            add(field, expected[field], plan.get(field))

    expected_hard = expected.get("hard_constraints") or {}
    predicted_hard = plan.get("hard_constraints") or {}
    for field, gold in expected_hard.items():
        mode = "set" if field in {"transport_modes", "must_have_activities"} else "equal"
        add(f"hard_constraints.{field}", gold, predicted_hard.get(field), mode=mode)

    if "exclusions" in expected:
        add("exclusions", expected["exclusions"], plan.get("exclusions") or [], mode="set")

    expected_soft = expected.get("soft_preferences") or {}
    predicted_soft = plan.get("soft_preferences") or {}
    for field, gold in expected_soft.items():
        add(f"soft_preferences.{field}", gold, _tag_ids(predicted_soft.get(field)), mode="set")

    if "evidence_aspects" in expected:
        add(
            "evidence_aspects",
            expected["evidence_aspects"],
            plan.get("evidence_aspects") or [],
            mode="contains_set",
        )

    semantic = str(plan.get("semantic_query") or "")
    for token in expected.get("semantic_must_include") or []:
        checks.append(
            {
                "field": "semantic_query.contains",
                "passed": token in semantic,
                "expected": token,
                "predicted": semantic,
            }
        )
    for token in expected.get("semantic_must_not_include") or []:
        checks.append(
            {
                "field": "semantic_query.excludes",
                "passed": token not in semantic,
                "expected": token,
                "predicted": semantic,
            }
        )
    return {"passed": all(check["passed"] for check in checks), "checks": checks}


def evaluate(
    gold_rows: list[dict[str, Any]],
    predictions: dict[str, dict[str, Any]],
    *,
    skipped: dict[str, str] | None = None,
) -> dict[str, Any]:
    skipped = skipped or {}
    case_results = []
    field_totals: Counter[str] = Counter()
    field_passed: Counter[str] = Counter()
    bucket_totals: Counter[str] = Counter()
    bucket_passed: Counter[str] = Counter()
    scope_expected: list[str] = []
    scope_predicted: list[str] = []
    task_expected: list[str] = []
    task_predicted: list[str] = []
    neg_tp = neg_fp = neg_fn = 0
    explicit_constraint_total = explicit_constraint_missed = 0
    added_hard_cases = 0
    added_hard_case_ids: list[str] = []
    evaluated_hard_cases = 0

    for row in gold_rows:
        case_id = str(row["id"])
        if case_id not in predictions:
            case_results.append({"id": case_id, "bucket": row["bucket"], "status": "skipped", "reason": skipped.get(case_id, "missing_prediction")})
            continue
        plan = predictions[case_id]
        result = _compare_case(row["expected"], plan)
        result.update(
            {
                "id": case_id,
                "bucket": row["bucket"],
                "queries": [str(turn.get("query") or "") for turn in row.get("turns") or []],
                "status": "evaluated",
            }
        )
        case_results.append(result)
        bucket_totals[row["bucket"]] += 1
        if result["passed"]:
            bucket_passed[row["bucket"]] += 1
        for check in result["checks"]:
            field_totals[check["field"]] += 1
            if check["passed"]:
                field_passed[check["field"]] += 1

        expected = row["expected"]
        if "scope" in expected:
            scope_expected.append(expected["scope"])
            scope_predicted.append(str(plan.get("scope")))
        if "task_type" in expected:
            task_expected.append(expected["task_type"])
            task_predicted.append(str(plan.get("task_type")))

        if "exclusions" in expected:
            gold_exclusions = _set(expected["exclusions"])
            predicted_exclusions = _set(plan.get("exclusions"))
            neg_tp += len(gold_exclusions & predicted_exclusions)
            neg_fp += len(predicted_exclusions - gold_exclusions)
            neg_fn += len(gold_exclusions - predicted_exclusions)

        expected_hard = expected.get("hard_constraints") or {}
        if expected.get("scope") == "in_domain" and "hard_constraints" in expected:
            evaluated_hard_cases += 1
            predicted_hard = plan.get("hard_constraints") or {}
            added = False
            for field in ("origin", "days_max", "budget_max", "travel_time_max"):
                if field in expected_hard:
                    explicit_constraint_total += 1
                    if predicted_hard.get(field) != expected_hard[field]:
                        explicit_constraint_missed += 1
                elif predicted_hard.get(field) is not None:
                    added = True
            for field in ("transport_modes", "must_have_activities"):
                if field in expected_hard:
                    explicit_constraint_total += 1
                    if _set(predicted_hard.get(field)) != _set(expected_hard[field]):
                        explicit_constraint_missed += 1
                elif predicted_hard.get(field):
                    added = True
            added_hard_cases += int(added)
            if added:
                added_hard_case_ids.append(str(row.get("id") or ""))

    evaluated = sum(result.get("status") == "evaluated" for result in case_results)
    passed = sum(result.get("status") == "evaluated" and result.get("passed") for result in case_results)
    neg_precision = _safe_ratio(neg_tp, neg_tp + neg_fp)
    neg_recall = _safe_ratio(neg_tp, neg_tp + neg_fn)
    neg_f1 = None
    if neg_precision is not None and neg_recall is not None:
        neg_f1 = round(2 * neg_precision * neg_recall / (neg_precision + neg_recall), 6) if neg_precision + neg_recall else 0.0
    hard_fields = [field for field in field_totals if field.startswith("hard_constraints.")]
    hard_total = sum(field_totals[field] for field in hard_fields)
    hard_passed = sum(field_passed[field] for field in hard_fields)
    multi_total = bucket_totals.get("multi_turn_update", 0)
    multi_passed = bucket_passed.get("multi_turn_update", 0)
    return {
        "gold_count": len(gold_rows),
        "evaluated_count": evaluated,
        "skipped_count": len(gold_rows) - evaluated,
        "case_accuracy": _safe_ratio(passed, evaluated),
        "scope_macro_f1": _macro_f1(scope_expected, scope_predicted),
        "task_macro_f1": _macro_f1(task_expected, task_predicted),
        "hard_slot_accuracy": _safe_ratio(hard_passed, hard_total),
        "negation": {"precision": neg_precision, "recall": neg_recall, "f1": neg_f1, "tp": neg_tp, "fp": neg_fp, "fn": neg_fn},
        "explicit_constraint_loss_rate": _safe_ratio(explicit_constraint_missed, explicit_constraint_total),
        "added_hard_constraint_case_rate": _safe_ratio(added_hard_cases, evaluated_hard_cases),
        "added_hard_constraint_case_ids": added_hard_case_ids,
        "multi_turn_accuracy": _safe_ratio(multi_passed, multi_total),
        "field_accuracy": {
            field: {"passed": field_passed[field], "total": total, "accuracy": _safe_ratio(field_passed[field], total)}
            for field, total in sorted(field_totals.items())
        },
        "bucket_accuracy": {
            bucket: {"passed": bucket_passed[bucket], "total": total, "accuracy": _safe_ratio(bucket_passed[bucket], total)}
            for bucket, total in sorted(bucket_totals.items())
        },
        "failures": [
            {
                "id": result["id"],
                "bucket": result["bucket"],
                "queries": result.get("queries") or [],
                "failed_fields": [check["field"] for check in result.get("checks", []) if not check["passed"]],
                "failed_checks": [check for check in result.get("checks", []) if not check["passed"]],
            }
            for result in case_results
            if result.get("status") == "evaluated" and not result.get("passed")
        ],
        "skipped": [
            {"id": result["id"], "reason": result.get("reason")}
            for result in case_results
            if result.get("status") == "skipped"
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate Query Plan predictions against query_gold.jsonl.")
    parser.add_argument("--gold", type=Path, default=DEFAULT_GOLD)
    parser.add_argument("--predictions", type=Path, help="JSONL rows with id and plan; bypasses local parser.")
    parser.add_argument("--parser", default="inspitrip.recommendation.query_plan:build_rule_query_plan")
    parser.add_argument("--envelope-parser", help="Optional module:function for multi-turn delta envelopes.")
    parser.add_argument("--merge", help="Optional module:function for merge_query_plan(previous, envelope).")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--summary-only", action="store_true", help="Omit failure/skipped ID lists from stdout.")
    parser.add_argument("--require-all", action="store_true", help="Return non-zero if any gold row is skipped.")
    args = parser.parse_args()

    gold_rows = load_jsonl(args.gold)
    predictions: dict[str, dict[str, Any]] = {}
    skipped: dict[str, str] = {}
    if args.predictions:
        for row in load_jsonl(args.predictions):
            plan = row.get("plan") or row.get("query_plan")
            if isinstance(plan, dict):
                predictions[str(row["id"])] = plan
    else:
        plan_parser = load_callable(args.parser)
        if plan_parser is None:
            raise SystemExit("--parser is required when --predictions is omitted")
        envelope_parser = load_callable(args.envelope_parser)
        merge = load_callable(args.merge)
        for row in gold_rows:
            try:
                plan, reason = _predict_from_parser(row, plan_parser, envelope_parser, merge)
                if plan is not None:
                    predictions[str(row["id"])] = plan
                elif reason:
                    skipped[str(row["id"])] = reason
            except Exception as exc:  # evaluation must retain per-case parser failures
                skipped[str(row["id"])] = f"parser_error:{type(exc).__name__}:{exc}"

    report = evaluate(gold_rows, predictions, skipped=skipped)
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    display_report = report
    if args.summary_only:
        display_report = {key: value for key, value in report.items() if key not in {"failures", "skipped"}}
    print(json.dumps(display_report, ensure_ascii=False, indent=2))
    if args.require_all and report["skipped_count"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
