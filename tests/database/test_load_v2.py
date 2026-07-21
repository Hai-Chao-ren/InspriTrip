from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from inspitrip.pipelines.database.load_v2 import (
    text_or_default,
    upsert_claim_snapshot,
    validate_claim_snapshot,
)


def claim(claim_id: str) -> dict:
    return {
        "claim_id": claim_id,
        "evidence_id": f"EV_{claim_id}",
        "entity_id": "DEST_TEST",
        "destination_id": "DEST_TEST",
        "note_id": "NOTE_TEST",
        "aspect": "transport",
        "polarity": "negative",
        "claim": "公交不方便",
        "key_quote": "公交不方便",
        "mood": [],
        "vibe": [],
        "activity": [],
        "conditions": {"holiday": True},
        "author_hash": "AUTHOR_TEST",
        "publish_date": "2026-06-01",
        "collected_date": "2026-07-01",
        "source_quality": 0.8,
        "is_suspected_ad": False,
        "source_url": "https://example.com/item/test",
    }


class RecordingCursor:
    def __init__(self, rowcount: int = 0):
        self.calls: list[tuple[str, tuple | None]] = []
        self.rowcount = rowcount

    def execute(self, query: str, params: tuple | None = None) -> None:
        self.calls.append((query, params))


class ClaimSnapshotValidationTests(unittest.TestCase):
    def test_nullable_database_text_is_normalized(self) -> None:
        self.assertEqual("", text_or_default(None))
        self.assertEqual("低", text_or_default(None, "低"))
        self.assertEqual("ok", text_or_default("ok", "低"))

    def test_empty_missing_and_duplicate_claim_ids_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "空 Claim"):
            validate_claim_snapshot([])
        with self.assertRaisesRegex(ValueError, "空 claim_id"):
            validate_claim_snapshot([{"claim_id": ""}])
        with self.assertRaisesRegex(ValueError, "重复 claim_id"):
            validate_claim_snapshot([claim("CLM_1"), claim("CLM_1")])

    def test_valid_snapshot_preserves_order(self) -> None:
        self.assertEqual(
            ["CLM_2", "CLM_1"],
            validate_claim_snapshot([claim("CLM_2"), claim("CLM_1")]),
        )


class ClaimSnapshotSyncTests(unittest.TestCase):
    def test_upserts_all_claims_then_deletes_stale_rows(self) -> None:
        cursor = RecordingCursor(rowcount=146)
        rows = [claim("CLM_1"), claim("CLM_2")]

        deleted = upsert_claim_snapshot(cursor, rows)

        self.assertEqual(146, deleted)
        self.assertEqual(3, len(cursor.calls))
        self.assertTrue(all("INSERT INTO ugc_evidence_claims" in call[0] for call in cursor.calls[:2]))
        delete_sql, delete_params = cursor.calls[-1]
        self.assertEqual(
            "DELETE FROM ugc_evidence_claims WHERE NOT (claim_id = ANY(%s::text[]))",
            delete_sql,
        )
        self.assertEqual((["CLM_1", "CLM_2"],), delete_params)

    def test_upsert_refreshes_traceability_fields(self) -> None:
        cursor = RecordingCursor()
        upsert_claim_snapshot(cursor, [claim("CLM_1")])
        upsert_sql = cursor.calls[0][0]
        for field in (
            "evidence_id", "entity_id", "note_id", "author_hash",
            "publish_date", "collected_date", "source_url",
        ):
            self.assertIn(f"{field}=EXCLUDED.{field}", upsert_sql)

    def test_sync_rejects_explicit_empty_id_set_before_sql(self) -> None:
        cursor = RecordingCursor()
        with self.assertRaisesRegex(ValueError, "空 Claim"):
            upsert_claim_snapshot(cursor, [], [])
        self.assertEqual([], cursor.calls)


if __name__ == "__main__":
    unittest.main()
