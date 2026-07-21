from __future__ import annotations

import argparse
import json
import subprocess
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import requests
from dotenv import dotenv_values


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_GOLD = Path(__file__).resolve().parent / "retrieval_gold.jsonl"
DEFAULT_OUTPUT = Path(__file__).resolve().parent / "retrieval_live_predictions.jsonl"


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _destination_ids(resources: Any) -> list[str]:
    result = []
    for item in resources if isinstance(resources, list) else []:
        metadata = item.get("metadata") or {}
        nested = metadata.get("doc_metadata") if isinstance(metadata.get("doc_metadata"), dict) else {}
        destination_id = str(
            item.get("destination_id")
            or metadata.get("destination_id")
            or metadata.get("entity_id")
            or nested.get("destination_id")
            or nested.get("entity_id")
            or ""
        )
        if destination_id and destination_id not in result:
            result.append(destination_id)
    return result


def _workflow_node_destination_ids(message_id: Any, *, container: str) -> list[str]:
    try:
        normalized_message_id = str(uuid.UUID(str(message_id)))
    except (ValueError, TypeError, AttributeError):
        return []
    sql = f"""
SELECT COALESCE(jsonb_agg(destination_id ORDER BY ordinal_position), '[]'::jsonb)
  FROM (
        SELECT COALESCE(
                   item->'metadata'->'doc_metadata'->>'destination_id',
                   item->'metadata'->>'destination_id',
                   item->>'destination_id'
               ) AS destination_id,
               ordinal_position
          FROM messages m
          JOIN workflow_node_executions n ON n.workflow_run_id = m.workflow_run_id
         CROSS JOIN LATERAL jsonb_array_elements(n.outputs::jsonb->'result')
              WITH ORDINALITY AS result_items(item, ordinal_position)
         WHERE m.id = '{normalized_message_id}'::uuid
           AND n.node_type = 'knowledge-retrieval'
           AND n.title = 'Destination Retrieval'
           AND n.status = 'succeeded'
       ) extracted
 WHERE COALESCE(destination_id, '') <> ''
"""
    try:
        completed = subprocess.run(
            [
                "docker",
                "exec",
                container,
                "psql",
                "-U",
                "postgres",
                "-d",
                "dify",
                "-Atc",
                sql,
            ],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
        )
        values = json.loads(completed.stdout.strip() or "[]")
        return list(dict.fromkeys(str(value) for value in values if value))
    except (subprocess.SubprocessError, json.JSONDecodeError, OSError):
        return []


def _collect_one(
    row: dict[str, Any],
    *,
    base_url: str,
    api_key: str,
    timeout: float,
    retries: int,
    db_container: str,
) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    started = time.perf_counter()
    last_reason = "unknown"
    for attempt in range(1, retries + 2):
        try:
            response = requests.post(
                f"{base_url}/chat-messages",
                headers=headers,
                json={
                    "inputs": {"origin": "", "budget": None, "days": None},
                    "query": str(row["query"]),
                    "response_mode": "blocking",
                    "conversation_id": "",
                    "user": f"retrieval-eval-{row['id']}",
                },
                timeout=timeout,
            )
            if response.ok:
                payload = response.json()
                resources = (payload.get("metadata") or {}).get("retriever_resources") or []
                destination_ids = _destination_ids(resources)
                retrieval_source = "chat_metadata"
                if not destination_ids:
                    destination_ids = _workflow_node_destination_ids(
                        payload.get("message_id"),
                        container=db_container,
                    )
                    retrieval_source = "workflow_node_execution"
                return {
                    "id": row["id"],
                    "retrieved_destination_ids": destination_ids,
                    "resource_count": len(destination_ids),
                    "retrieval_source": retrieval_source,
                    "http_status": response.status_code,
                    "attempts": attempt,
                    "elapsed_seconds": round(time.perf_counter() - started, 3),
                }
            last_reason = f"http_{response.status_code}"
            if response.status_code < 500:
                break
        except (requests.RequestException, ValueError) as exc:
            last_reason = type(exc).__name__
        if attempt <= retries:
            time.sleep(min(0.5 * attempt, 2.0))
    return {
        "id": row["id"],
        "retrieved_destination_ids": [],
        "resource_count": 0,
        "http_status": 0,
        "attempts": retries + 1,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "error": last_reason,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect sanitized Dify workflow retrieval predictions.")
    parser.add_argument("--gold", type=Path, default=DEFAULT_GOLD)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--retries", type=int, default=1)
    parser.add_argument("--db-container", default="docker-db_postgres-1")
    args = parser.parse_args()

    env = dotenv_values(ROOT / ".env")
    base_url = str(env.get("DIFY_APP_API_BASE") or "").rstrip("/")
    api_key = str(env.get("DIFY_APP_API_KEY") or "").strip()
    if not base_url or not api_key:
        print(json.dumps({"ok": False, "reason": "dify_app_not_configured"}))
        return 2
    rows = _load_jsonl(args.gold)
    if args.limit > 0:
        rows = rows[: args.limit]
    results: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=max(1, min(args.workers, 8))) as executor:
        futures = {
            executor.submit(
                _collect_one,
                row,
                base_url=base_url,
                api_key=api_key,
                timeout=max(10.0, args.timeout),
                retries=max(0, args.retries),
                db_container=args.db_container,
            ): row
            for row in rows
        }
        for future in as_completed(futures):
            result = future.result()
            results[str(result["id"])] = result
    ordered = [results[str(row["id"])] for row in rows]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(
        "".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in ordered),
        encoding="utf-8",
    )
    temporary.replace(args.output)
    succeeded = sum(row.get("http_status") == 200 for row in ordered)
    resource_counts = [int(row.get("resource_count") or 0) for row in ordered if row.get("http_status") == 200]
    report = {
        "ok": succeeded == len(ordered),
        "requested": len(ordered),
        "succeeded": succeeded,
        "failed": len(ordered) - succeeded,
        "minimum_resource_count": min(resource_counts) if resource_counts else 0,
        "maximum_resource_count": max(resource_counts) if resource_counts else 0,
        "average_elapsed_seconds": round(
            sum(float(row.get("elapsed_seconds") or 0) for row in ordered) / len(ordered), 3
        ) if ordered else 0.0,
        "output": str(args.output.resolve()),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
