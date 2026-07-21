from __future__ import annotations

import json
import time
from pathlib import Path

import requests
from dotenv import dotenv_values

from inspitrip.paths import DEFAULT_ENV_PATH, PIPELINE_OUTPUT_DIR


ROOT = Path(__file__).resolve().parents[3]


def main() -> int:
    env = dotenv_values(DEFAULT_ENV_PATH)
    base_url = str(env.get("XINFERENCE_BASE_URL") or env.get("OPENAI_BASE_URL") or "").rstrip("/")
    model = str(env.get("XINFERENCE_RERANK_MODEL") or "bge-reranker")
    if not base_url or not model:
        print(json.dumps({"ok": False, "reason": "reranker_not_configured"}))
        return 2
    documents = [
        json.loads(line)["text"]
        for line in (PIPELINE_OUTPUT_DIR / "dify" / "destination_documents.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    started = time.perf_counter()
    try:
        response = requests.post(
            f"{base_url}/rerank",
            json={"model": model, "query": "一个人安静看海", "documents": documents},
            timeout=30,
        )
        elapsed = time.perf_counter() - started
        payload = response.json() if response.ok else {}
        results = payload.get("results") if isinstance(payload, dict) else None
        indexes = {
            int(item["index"])
            for item in results or []
            if isinstance(item, dict) and "index" in item
        }
        full_coverage = indexes == set(range(len(documents)))
        report = {
            "ok": bool(response.ok and isinstance(results, list) and full_coverage),
            "http_ok": response.ok,
            "input_documents": len(documents),
            "result_count": len(results or []),
            "unique_indexes": len(indexes),
            "full_coverage": full_coverage,
            "elapsed_seconds": round(elapsed, 3),
        }
    except requests.RequestException as exc:
        report = {
            "ok": False,
            "reason": type(exc).__name__,
            "input_documents": len(documents),
            "elapsed_seconds": round(time.perf_counter() - started, 3),
        }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
