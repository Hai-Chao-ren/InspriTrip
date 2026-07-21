from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from jsonschema import Draft7Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = ROOT / "schemas"
DATA_DIR = ROOT / "data" / "demo"


def _load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


class PublicBoundaryTests(unittest.TestCase):
    def test_demo_readme_declares_synthetic_provenance(self) -> None:
        text = (DATA_DIR / "README.md").read_text(encoding="utf-8").lower()
        self.assertIn("synthetic", text)
        self.assertIn("does not contain scraped ugc", text)

    def test_gitignore_blocks_runtime_secrets_and_analytics(self) -> None:
        text = (ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn(".env", text)
        self.assertIn("analytics.sqlite3", text)

    def test_env_example_defaults_to_demo(self) -> None:
        text = (ROOT / ".env.example").read_text(encoding="utf-8")
        self.assertIn("INSPITRIP_MODE=demo", text)

    def test_site_is_static_and_uses_no_api_endpoint(self) -> None:
        script = (ROOT / "site" / "app.js").read_text(encoding="utf-8")
        self.assertNotIn("fetch(", script)
        self.assertNotIn("/api/", script)

    def test_site_keeps_required_palette(self) -> None:
        css = (ROOT / "site" / "styles.css").read_text(encoding="utf-8").upper()
        for color in ("#15803D", "#059669", "#D97706", "#FEF3E2", "#F0FDF4", "#0F172A", "#E2EFE7", "#DC2626"):
            self.assertIn(color, css)

    def test_public_text_contains_no_local_workspace_path(self) -> None:
        candidates = list((ROOT / "site").rglob("*")) + list((ROOT / "docs").rglob("*")) + [ROOT / "README.md"]
        for path in candidates:
            if path.is_file() and path.suffix.lower() in {".md", ".html", ".css", ".js"}:
                text = path.read_text(encoding="utf-8")
                self.assertNotRegex(text, re.compile(r"[A-Za-z]:\\(?:work|Users)\\", re.I), path.as_posix())


def _attach_schema_tests() -> None:
    for path in sorted(SCHEMA_DIR.glob("*.json")):
        def test(self: unittest.TestCase, schema_path: Path = path) -> None:
            schema = json.loads(schema_path.read_text(encoding="utf-8"))
            Draft7Validator.check_schema(schema)
        setattr(PublicBoundaryTests, f"test_schema_{path.stem}", test)


def _attach_record_tests() -> None:
    specs = (
        ("entities.jsonl", "entity_schema.json", "entity_id"),
        ("destination_profiles.jsonl", "destination_profile_schema.json", "destination_id"),
        ("ugc_claims.jsonl", "ugc_claim_schema.json", "claim_id"),
    )
    for filename, schema_name, key_name in specs:
        schema = json.loads((SCHEMA_DIR / schema_name).read_text(encoding="utf-8"))
        validator = Draft7Validator(schema, format_checker=FormatChecker())
        for index, row in enumerate(_load_jsonl(DATA_DIR / filename), 1):
            def test(
                self: unittest.TestCase,
                record: dict = row,
                record_validator: Draft7Validator = validator,
                primary_key: str = key_name,
            ) -> None:
                errors = list(record_validator.iter_errors(record))
                self.assertEqual([], errors)
                self.assertTrue(str(record.get(primary_key) or "").startswith("DEMO_"))
            setattr(PublicBoundaryTests, f"test_{Path(filename).stem}_{index:02d}", test)


_attach_schema_tests()
_attach_record_tests()


if __name__ == "__main__":
    unittest.main()
