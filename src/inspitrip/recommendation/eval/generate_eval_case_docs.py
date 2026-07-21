from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


EVAL_DIR = Path(__file__).resolve().parent


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: {exc}") from exc
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number}: row must be an object")
            rows.append(value)
    return rows


def load_report(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else {}


def _json_block(value: Any) -> str:
    return "```json\n" + json.dumps(value, ensure_ascii=False, indent=2) + "\n```"


def _md_cell(value: Any) -> str:
    return str(value or "").replace("|", "\\|").replace("\r", " ").replace("\n", "<br>")


def _percent(value: Any) -> str:
    try:
        return f"{float(value) * 100:.2f}%"
    except (TypeError, ValueError):
        return "未记录"


def render_query_cases(rows: list[dict[str, Any]], report: dict[str, Any]) -> str:
    bucket_counts = Counter(str(row.get("bucket") or "unknown") for row in rows)
    lines = [
        "# Query Plan 评测用例",
        "",
        "> 本文档由 `query_gold.jsonl` 自动生成；修改测试用例时应先修改 `build_query_gold.py`，再重新生成 JSONL 和本文档。",
        "",
        "## 概览",
        "",
        f"- 用例总数：{len(rows)}",
        f"- 单轮用例：{sum(len(row.get('turns') or []) == 1 for row in rows)}",
        f"- 多轮用例：{sum(len(row.get('turns') or []) > 1 for row in rows)}",
        f"- 当前 case accuracy：{_percent(report.get('case_accuracy'))}",
        f"- 当前 hard slot accuracy：{_percent(report.get('hard_slot_accuracy'))}",
        f"- 当前 negation F1：{_percent((report.get('negation') or {}).get('f1'))}",
        f"- 当前 multi-turn accuracy：{_percent(report.get('multi_turn_accuracy'))}",
        "",
        "Query 用例验证 `scope`、`task_type`、目标目的地、硬约束、排除项、mood/vibe/activity、evidence aspects、保守 rewrite，以及多轮修改/撤销。未在 `expected` 中标注的字段不作为负样本。",
        "",
        "## 分桶统计",
        "",
        "| Bucket | 数量 |",
        "|---|---:|",
    ]
    lines.extend(f"| `{bucket}` | {count} |" for bucket, count in sorted(bucket_counts.items()))
    lines.extend(["", "## 全部用例", ""])

    current_bucket = None
    for row in rows:
        bucket = str(row.get("bucket") or "unknown")
        if bucket != current_bucket:
            lines.extend([f"### `{bucket}`", ""])
            current_bucket = bucket
        case_id = str(row.get("id") or "")
        turns = row.get("turns") or []
        summary_queries = " → ".join(str(turn.get("query") or "") for turn in turns)
        lines.extend(
            [
                "<details>",
                f"<summary><code>{case_id}</code> — {_md_cell(summary_queries)}</summary>",
                "",
                "输入轮次：",
                "",
            ]
        )
        for index, turn in enumerate(turns, 1):
            lines.append(f"{index}. `{_md_cell(turn.get('query'))}`")
            if turn.get("expected_state_actions"):
                lines.extend(["", "   本轮期望状态操作：", "", _json_block(turn["expected_state_actions"])])
        lines.extend(
            [
                "",
                "最终期望：",
                "",
                _json_block(row.get("expected") or {}),
                "",
                f"- 评测目标：`{', '.join(str(value) for value in row.get('evaluation_targets') or [])}`",
                f"- Retrieval 裁决状态：`{row.get('retrieval_adjudication') or ''}`",
            ]
        )
        if row.get("notes"):
            lines.append(f"- 备注：{row['notes']}")
        lines.extend(["", "</details>", ""])
    return "\n".join(lines).rstrip() + "\n"


def _destination_names(rows: list[dict[str, Any]]) -> dict[str, str]:
    names: dict[str, str] = {}
    for row in rows:
        if row.get("bucket") != "exact_destination_name":
            continue
        destination_ids = row.get("relevant_destination_ids") or []
        if len(destination_ids) == 1:
            names[str(destination_ids[0])] = str(row.get("query") or "")
    return names


def render_retrieval_cases(rows: list[dict[str, Any]], report: dict[str, Any]) -> str:
    bucket_counts = Counter(str(row.get("bucket") or "unknown") for row in rows)
    names = _destination_names(rows)
    recall = report.get("recall") or {}
    lines = [
        "# Retrieval 评测用例",
        "",
        "> 本文档由 `retrieval_gold.jsonl` 自动生成；相关目的地来自当前 60 个 active destination 快照。",
        "",
        "## 概览",
        "",
        f"- 用例总数：{len(rows)}",
        f"- 覆盖目的地：{len({destination_id for row in rows for destination_id in row.get('relevant_destination_ids') or []})}",
        f"- 当前 Recall@20：{_percent(recall.get('20'))}",
        f"- 当前 Recall@30：{_percent(recall.get('30'))}",
        f"- 当前 Recall@60：{_percent(recall.get('60'))}",
        f"- 当前 MRR：{_percent(report.get('mrr'))}",
        "",
        "每个 active destination 有 3 条用例：目的地精确名称、`想去{目的地}`、`{目的地}怎么玩`。金标准仅表示该目的地必须被召回，不代表其他召回结果错误。",
        "",
        "## 分桶统计",
        "",
        "| Bucket | 数量 |",
        "|---|---:|",
    ]
    lines.extend(f"| `{bucket}` | {count} |" for bucket, count in sorted(bucket_counts.items()))
    lines.extend(["", "## 全部用例", ""])

    for bucket in sorted(bucket_counts):
        lines.extend(
            [
                f"### `{bucket}`",
                "",
                "| ID | Query | 相关目的地 | destination_id | 裁决方式 |",
                "|---|---|---|---|---|",
            ]
        )
        for row in (item for item in rows if str(item.get("bucket") or "unknown") == bucket):
            destination_ids = [str(value) for value in row.get("relevant_destination_ids") or []]
            destination_names = [names.get(destination_id, "未知名称") for destination_id in destination_ids]
            lines.append(
                "| `{}` | {} | {} | `{}` | `{}` |".format(
                    _md_cell(row.get("id")),
                    _md_cell(row.get("query")),
                    _md_cell("、".join(destination_names)),
                    _md_cell("`, `".join(destination_ids)),
                    _md_cell(row.get("adjudication")),
                )
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 Query/Retrieval 评测用例文档")
    parser.add_argument("--eval-dir", type=Path, default=EVAL_DIR)
    args = parser.parse_args()
    eval_dir = args.eval_dir.resolve()
    query_rows = load_jsonl(eval_dir / "query_gold.jsonl")
    retrieval_rows = load_jsonl(eval_dir / "retrieval_gold.jsonl")
    query_output = eval_dir / "query_test_cases.md"
    retrieval_output = eval_dir / "retrieval_test_cases.md"
    query_output.write_text(
        render_query_cases(query_rows, load_report(eval_dir / "query_eval_report.json")),
        encoding="utf-8",
    )
    retrieval_output.write_text(
        render_retrieval_cases(
            retrieval_rows,
            load_report(eval_dir / "retrieval_eval_report.json"),
        ),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "ok": True,
                "query_cases": len(query_rows),
                "retrieval_cases": len(retrieval_rows),
                "query_document": query_output.name,
                "retrieval_document": retrieval_output.name,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
