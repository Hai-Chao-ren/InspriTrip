from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from inspitrip.recommendation.eval.build_query_gold import (
    build_query_gold,
    build_retrieval_gold,
    _load_active_destinations,
)
from inspitrip.recommendation.eval.run_query_eval import evaluate as evaluate_queries, load_jsonl
from inspitrip.recommendation.eval.run_retrieval_eval import evaluate as evaluate_retrieval
from inspitrip.recommendation.eval.run_retrieval_eval import _candidate_ids
from inspitrip.recommendation.eval.collect_dify_retrieval_predictions import (
    _destination_ids,
    _workflow_node_destination_ids,
)
from inspitrip.recommendation.eval.generate_eval_case_docs import (
    render_query_cases,
    render_retrieval_cases,
)


EVAL_DIR = ROOT / "src" / "inspitrip" / "recommendation" / "eval"
ENTITIES = ROOT / "data" / "demo" / "entities.jsonl"


class QueryGoldTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.destinations = _load_active_destinations(ENTITIES)
        cls.rows = load_jsonl(EVAL_DIR / "query_gold.jsonl")

    def test_query_gold_is_deterministic_unique_and_complete(self) -> None:
        rebuilt = build_query_gold(self.destinations)
        self.assertEqual(rebuilt, self.rows)
        self.assertEqual(200, len(self.rows))
        self.assertEqual(200, len({row["id"] for row in self.rows}))
        self.assertTrue(all(row.get("turns") for row in self.rows))

    def test_query_gold_has_required_language_coverage(self) -> None:
        feeling_count = sum(str(row["bucket"]).startswith("feeling_") for row in self.rows)
        multi_count = sum(row["bucket"] == "multi_turn_update" for row in self.rows)
        negation_count = sum(row["id"].startswith("QG-NEG-") for row in self.rows)
        self.assertGreaterEqual(feeling_count, 100)
        self.assertEqual(20, multi_count)
        self.assertEqual(30, negation_count)

    def test_destination_relevance_is_only_exact_snapshot_adjudication(self) -> None:
        active_ids = {row["destination_id"] for row in self.destinations}
        labelled = [row for row in self.rows if row.get("relevant_destination_ids")]
        self.assertEqual(8, len(labelled))
        for row in labelled:
            self.assertEqual("entity_snapshot_exact_name", row["retrieval_adjudication"])
            self.assertTrue(set(row["relevant_destination_ids"]) <= active_ids)

    def test_query_evaluator_can_score_perfect_partial_predictions(self) -> None:
        sample = [row for row in self.rows if len(row["turns"]) == 1][:3]
        predictions = {}
        for row in sample:
            expected = row["expected"]
            plan = {
                "scope": expected["scope"],
                "task_type": expected["task_type"],
                "target_destination": expected.get("target_destination"),
                "hard_constraints": dict(expected.get("hard_constraints") or {}),
                "exclusions": list(expected.get("exclusions") or []),
                "soft_preferences": {
                    field: [{"id": tag, "confidence": 1.0} for tag in expected.get("soft_preferences", {}).get(field, [])]
                    for field in ("mood", "vibe", "activity")
                },
                "evidence_aspects": list(expected.get("evidence_aspects") or []),
                "semantic_query": " ".join(expected.get("semantic_must_include") or []),
            }
            predictions[row["id"]] = plan
        report = evaluate_queries(sample, predictions)
        self.assertEqual(3, report["evaluated_count"])
        self.assertEqual(1.0, report["case_accuracy"])

    def test_query_evaluator_failure_keeps_expected_and_predicted_values(self) -> None:
        sample = [
            {
                "id": "AUDIT-1",
                "bucket": "audit",
                "turns": [{"query": "不要徒步"}],
                "expected": {"scope": "in_domain", "exclusions": ["act_hike"]},
            }
        ]
        predictions = {
            "AUDIT-1": {
                "scope": "in_domain",
                "exclusions": [],
                "hard_constraints": {},
                "soft_preferences": {},
            }
        }
        report = evaluate_queries(sample, predictions)
        failed = report["failures"][0]["failed_checks"][0]
        self.assertEqual("exclusions", failed["field"])
        self.assertEqual(["act_hike"], failed["expected"])
        self.assertEqual([], failed["predicted"])

    def test_unannotated_hard_fields_are_not_counted_as_added_constraints(self) -> None:
        sample = [
            {
                "id": "PARTIAL-HARD-GOLD",
                "bucket": "audit",
                "turns": [{"query": "周末想看海"}],
                "expected": {"scope": "in_domain", "task_type": "destination_discovery"},
            }
        ]
        predictions = {
            "PARTIAL-HARD-GOLD": {
                "scope": "in_domain",
                "task_type": "destination_discovery",
                "hard_constraints": {"days_max": 2, "must_have_activities": ["act_sea"]},
                "soft_preferences": {},
                "exclusions": [],
            }
        }
        report = evaluate_queries(sample, predictions)
        self.assertIsNone(report["added_hard_constraint_case_rate"])

    def test_evidence_aspect_gold_is_a_required_subset(self) -> None:
        sample = [
            {
                "id": "ASPECT-SUBSET",
                "bucket": "audit",
                "turns": [{"query": "避开人潮，一个人安静走走"}],
                "expected": {
                    "scope": "in_domain",
                    "task_type": "destination_discovery",
                    "evidence_aspects": ["crowd"],
                },
            }
        ]
        predictions = {
            "ASPECT-SUBSET": {
                "scope": "in_domain",
                "task_type": "destination_discovery",
                "hard_constraints": {},
                "soft_preferences": {},
                "exclusions": [],
                "evidence_aspects": ["crowd", "solo", "mood_fit"],
            }
        }
        report = evaluate_queries(sample, predictions)
        self.assertEqual(1.0, report["case_accuracy"])

    def test_query_case_document_contains_every_gold_id(self) -> None:
        document = render_query_cases(self.rows, {})
        self.assertEqual(200, document.count("<summary><code>QG-"))
        self.assertTrue(all(row["id"] in document for row in self.rows))


class RetrievalGoldTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.destinations = _load_active_destinations(ENTITIES)
        cls.rows = load_jsonl(EVAL_DIR / "retrieval_gold.jsonl")

    def test_retrieval_gold_covers_each_active_destination_three_times(self) -> None:
        rebuilt = build_retrieval_gold(self.destinations)
        self.assertEqual(rebuilt, self.rows)
        active_ids = {row["destination_id"] for row in self.destinations}
        counts = {destination_id: 0 for destination_id in active_ids}
        for row in self.rows:
            for destination_id in row["relevant_destination_ids"]:
                counts[destination_id] += 1
        self.assertEqual(18, len(self.rows))
        self.assertEqual({3}, set(counts.values()))

    def test_retrieval_evaluator_reports_recall_at_30_and_60(self) -> None:
        sample = self.rows[:2]
        predictions = {
            sample[0]["id"]: sample[0]["relevant_destination_ids"],
            sample[1]["id"]: [],
        }
        active_ids = {row["destination_id"] for row in self.destinations}
        report = evaluate_retrieval(sample, predictions, active_ids=active_ids)
        self.assertEqual(0.5, report["recall"]["30"])
        self.assertEqual(0.5, report["recall"]["60"])
        self.assertEqual([], report["invalid_destination_ids"])

    def test_nested_dify_metadata_is_parsed_and_sanitized(self) -> None:
        resources = [
            {
                "metadata": {
                    "dataset_id": "must-not-be-copied",
                    "doc_metadata": {"destination_id": "DEST_SAFE"},
                },
                "content": "must-not-be-copied",
            }
        ]
        self.assertEqual(["DEST_SAFE"], _destination_ids(resources))
        self.assertEqual(["DEST_SAFE"], _candidate_ids({"retrieval_items": resources}))

    def test_invalid_message_id_never_reaches_local_database(self) -> None:
        self.assertEqual([], _workflow_node_destination_ids("not-a-uuid", container="unused"))

    def test_retrieval_case_document_contains_every_gold_id(self) -> None:
        document = render_retrieval_cases(self.rows, {})
        self.assertEqual(18, document.count("| `RG-"))
        self.assertTrue(all(row["id"] in document for row in self.rows))


if __name__ == "__main__":
    unittest.main()
