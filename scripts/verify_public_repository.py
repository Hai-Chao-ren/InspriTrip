from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKIP_PARTS = {".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache"}
TEXT_SUFFIXES = {".py", ".md", ".html", ".css", ".js", ".json", ".jsonl", ".yml", ".yaml", ".toml", ".txt", ".csv", ".sql"}


def text_files() -> list[Path]:
    return [
        path
        for path in ROOT.rglob("*")
        if path.is_file()
        and path.suffix.lower() in TEXT_SUFFIXES
        and not any(part in SKIP_PARTS for part in path.parts)
    ]


def main() -> int:
    violations: list[dict[str, str]] = []
    if (ROOT / ".env").exists():
        violations.append({"kind": "FILLED_ENV_PRESENT", "file": ".env"})

    patterns = {
        "ABSOLUTE_WINDOWS_PATH": re.compile(r"[A-Za-z]:\\(?:work|Users)\\", re.I),
        "OPENAI_STYLE_SECRET": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"),
        "DIFY_STYLE_SECRET": re.compile(r"\bdataset-[A-Za-z0-9_-]{12,}"),
        "REAL_XHS_SOURCE": re.compile(r"https?://(?:www\.)?xiaohongshu\.com/(?:explore|discovery/item)/[0-9a-f]{12,}", re.I),
    }
    for path in text_files():
        relative = path.relative_to(ROOT).as_posix()
        text = path.read_text(encoding="utf-8")
        for kind, pattern in patterns.items():
            if pattern.search(text):
                violations.append({"kind": kind, "file": relative})

    claims_path = ROOT / "data" / "demo" / "ugc_claims.jsonl"
    for line_number, line in enumerate(claims_path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        if not str(row.get("source_url") or "").startswith("demo://synthetic/"):
            violations.append({"kind": "NON_SYNTHETIC_SOURCE", "file": f"data/demo/ugc_claims.jsonl:{line_number}"})
        if not str(row.get("author_hash") or "").startswith("synthetic_author_"):
            violations.append({"kind": "NON_SYNTHETIC_AUTHOR", "file": f"data/demo/ugc_claims.jsonl:{line_number}"})

    workflow = (ROOT / "workflows" / "dify" / "InspiTrip.template.yml").read_text(encoding="utf-8")
    if "REPLACE_WITH_DATASET_ID" not in workflow or "YOUR_BACKEND_HOST" not in workflow:
        violations.append({"kind": "WORKFLOW_NOT_TEMPLATED", "file": "workflows/dify/InspiTrip.template.yml"})

    report = {"ok": not violations, "scanned_text_files": len(text_files()), "violations": violations}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
