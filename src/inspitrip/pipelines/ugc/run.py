from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any
from dotenv import dotenv_values
from jsonschema import Draft7Validator

from inspitrip.paths import DEFAULT_ENV_PATH, PIPELINE_OUTPUT_DIR, PRIVATE_DATA_DIR, REPO_ROOT, SCHEMA_DIR

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from inspitrip.pipelines.ugc.client import ExtractionError, OpenAIExtractor
from inspitrip.pipelines.ugc.db import load_notes
from inspitrip.pipelines.ugc.prompt import (
    bind_taxonomy_enums,
    build_system_prompt,
    build_user_input,
)
from inspitrip.pipelines.ugc.writer import (
    aggregate_pois,
    build_mention_and_evidence_records,
    load_alias_map,
    load_enrichment,
    load_schema,
    write_jsonl,
)


ROOT = REPO_ROOT
DEFAULT_DB = PRIVATE_DATA_DIR / "ExploreData.db"
DEFAULT_OUTPUT = PIPELINE_OUTPUT_DIR / "ugc"
ENV_VALUES = dotenv_values(DEFAULT_ENV_PATH)


def _configure_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def _load_cached(path: Path, validator: Draft7Validator) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        data = payload.get("data", payload)
        if list(validator.iter_errors(data)):
            return None
        return data
    except (OSError, json.JSONDecodeError, AttributeError):
        return None


def _write_cache(path: Path, data: dict[str, Any], meta: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(
        json.dumps({"data": data, "meta": meta}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temp.replace(path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="ExploreData.db -> LLM POI mentions -> UGC evidence / POI candidates"
    )
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--alias-map", type=Path, default=PRIVATE_DATA_DIR / "alias_map.csv")
    parser.add_argument("--map-enrichment", type=Path, help="地图 API 补全结果 JSON")
    parser.add_argument("--limit", type=int, default=3, help="默认仅跑 3 条，避免误触发全量费用")
    parser.add_argument("--all", action="store_true", help="显式处理全部笔记")
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--note-id", action="append", dest="note_ids")
    parser.add_argument("--model", default=str(ENV_VALUES.get("OPENAI_MODEL") or "gpt-5.6-luna"))
    parser.add_argument("--base-url", default=ENV_VALUES.get("OPENAI_BASE_URL"))
    parser.add_argument("--api-style", choices=["responses", "chat"], default="responses")
    parser.add_argument(
        "--output-mode",
        choices=["auto", "structured", "json"],
        default="auto",
        help="auto 先用严格 JSON Schema，不支持时退回 JSON mode+本地校验",
    )
    parser.add_argument(
        "--reasoning-effort",
        choices=["none", "low", "medium", "high", "xhigh", "max"],
        help="不传则兼容更多第三方 API；信息抽取通常 low 足够",
    )
    parser.add_argument("--max-output-tokens", type=int, default=5000)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--retries", type=int, default=4)
    parser.add_argument("--retry-base-delay", type=float, default=5.0)
    parser.add_argument("--retry-max-delay", type=float, default=60.0)
    parser.add_argument("--request-interval", type=float, default=5.0)
    parser.add_argument("--refresh", action="store_true", help="忽略 note_id 缓存并重新调用")
    parser.add_argument("--dry-run", action="store_true", help="只检查输入和 prompt，不调用 API")
    parser.add_argument("--print-prompt", action="store_true", help="打印运行时 system prompt 后退出")
    return parser


def main(argv: list[str] | None = None) -> int:
    _configure_console()
    args = build_parser().parse_args(argv)

    taxonomy_path = SCHEMA_DIR / "intent_taxonomy.json"
    extraction_schema_path = SCHEMA_DIR / "note_extraction_schema.json"
    evidence_schema_path = SCHEMA_DIR / "ugc_evidence_schema.json"
    poi_schema_path = SCHEMA_DIR / "poi_schema.json"
    extraction_schema = bind_taxonomy_enums(
        load_schema(extraction_schema_path), taxonomy_path
    )
    evidence_schema = load_schema(evidence_schema_path)
    poi_schema = load_schema(poi_schema_path)
    extraction_validator = Draft7Validator(extraction_schema)
    instructions = build_system_prompt(taxonomy_path)

    if args.print_prompt:
        print(instructions)
        return 0

    limit = None if args.all else max(args.limit, 0)
    note_ids = set(args.note_ids) if args.note_ids else None
    notes = load_notes(
        args.db.resolve(), limit=limit, offset=max(args.offset, 0), note_ids=note_ids
    )
    print(f"读取 {len(notes)} 条笔记：{args.db.resolve()}")
    if not notes:
        print("没有符合条件的笔记。", file=sys.stderr)
        return 1
    if args.dry_run:
        for note in notes:
            print(f"- {note['note_id']} | {note['note_title'][:60]}")
        print(f"Prompt 字符数：{len(instructions)}；dry-run 未调用 API。")
        return 0

    api_key = str(ENV_VALUES.get("OPENAI_API_KEY") or "").strip()
    extractor: OpenAIExtractor | None = None
    aliases = load_alias_map(args.alias_map)
    output_dir = args.output_dir.resolve()
    cache_dir = output_dir / "cache"
    extractions: list[dict[str, Any]] = []
    mention_rows: list[dict[str, Any]] = []
    evidence_rows: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    api_calls = 0
    api_note_attempts = 0
    cached_count = 0
    usage_rows: list[dict[str, Any]] = []
    interrupted = False

    for index, note in enumerate(notes, 1):
        cache_path = cache_dir / f"{note['note_id']}.json"
        extraction = None if args.refresh else _load_cached(cache_path, extraction_validator)
        if extraction is not None:
            cached_count += 1
            print(f"[{index}/{len(notes)}] cache {note['note_id']} {note['note_title'][:36]}")
        else:
            if api_note_attempts and args.request_interval > 0:
                try:
                    time.sleep(args.request_interval)
                except KeyboardInterrupt:
                    interrupted = True
                    print("\n收到中断，正在保存已完成结果……", file=sys.stderr)
                    break
            api_note_attempts += 1
            print(f"[{index}/{len(notes)}] API   {note['note_id']} {note['note_title'][:36]}")
            try:
                if extractor is None:
                    if not api_key:
                        raise ExtractionError(
                            "缺少 OPENAI_API_KEY。请在环境变量或 .env 中配置；密钥不要写入代码。"
                        )
                    extractor = OpenAIExtractor(
                        api_key=api_key,
                        model=args.model,
                        schema=extraction_schema,
                        api_style=args.api_style,
                        output_mode=args.output_mode,
                        base_url=args.base_url,
                        timeout=args.timeout,
                        max_output_tokens=args.max_output_tokens,
                        reasoning_effort=args.reasoning_effort,
                    )
                result = extractor.extract(
                    instructions=instructions,
                    user_input=build_user_input(note),
                    retries=max(args.retries, 0),
                    retry_base_delay=max(args.retry_base_delay, 0.0),
                    retry_max_delay=max(args.retry_max_delay, 0.0),
                )
                extraction = result.data
                if extraction.get("note_id") != note["note_id"]:
                    print(
                        f"  警告：模型 note_id={extraction.get('note_id')}，"
                        f"已用数据库主键 {note['note_id']} 覆盖。",
                        file=sys.stderr,
                    )
                    extraction["note_id"] = note["note_id"]
                meta = {
                    "model": result.model,
                    "response_id": result.response_id,
                    "usage": result.usage,
                    "api_style": result.api_style,
                    "output_mode": result.output_mode,
                }
                _write_cache(cache_path, extraction, meta)
                usage_rows.append({"note_id": note["note_id"], **meta})
                api_calls += 1
            except KeyboardInterrupt:
                interrupted = True
                print("\n收到中断，正在保存已完成结果……", file=sys.stderr)
                break
            except Exception as exc:  # Continue the batch and preserve failures.
                failures.append(
                    {
                        "note_id": note["note_id"],
                        "note_title": note["note_title"],
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
                print(f"  失败：{exc}", file=sys.stderr)
                write_jsonl(output_dir / "failures.jsonl", failures)
                continue

        try:
            mentions, evidence = build_mention_and_evidence_records(
                note,
                extraction,
                aliases=aliases,
                evidence_schema=evidence_schema,
            )
        except Exception as exc:
            failures.append(
                {
                    "note_id": note["note_id"],
                    "note_title": note["note_title"],
                    "error": f"writer: {type(exc).__name__}: {exc}",
                }
            )
            print(f"  写入校验失败：{exc}", file=sys.stderr)
            write_jsonl(output_dir / "failures.jsonl", failures)
            continue
        extractions.append(extraction)
        mention_rows.extend(mentions)
        evidence_rows.extend(evidence)

    enrichment = load_enrichment(args.map_enrichment)
    poi_rows, candidate_rows, discarded_rows = aggregate_pois(
        mention_rows,
        poi_schema=poi_schema,
        enrichment=enrichment,
    )

    write_jsonl(output_dir / "extractions.jsonl", extractions)
    write_jsonl(output_dir / "poi_mentions.jsonl", mention_rows)
    write_jsonl(output_dir / "ugc_evidence.jsonl", evidence_rows)
    write_jsonl(output_dir / "poi_candidates.jsonl", candidate_rows)
    write_jsonl(output_dir / "poi_seed.jsonl", poi_rows)
    write_jsonl(output_dir / "discarded_pois.jsonl", discarded_rows)
    write_jsonl(output_dir / "failures.jsonl", failures)
    report = {
        "notes_selected": len(notes),
        "notes_succeeded": len(extractions),
        "api_calls": api_calls,
        "cache_hits": cached_count,
        "mentions": len(mention_rows),
        "ugc_evidence": len(evidence_rows),
        "poi_candidates": len(candidate_rows),
        "poi_schema_valid": len(poi_rows),
        "discarded_ad_only": len(discarded_rows),
        "failures": len(failures),
        "interrupted": interrupted,
        "model": args.model,
        "base_url": args.base_url or "https://api.openai.com/v1",
        "usage": usage_rows,
    }
    (output_dir / "run_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(
        "完成："
        f"成功笔记 {len(extractions)}/{len(notes)}，mention {len(mention_rows)}，"
        f"UGC evidence {len(evidence_rows)}，POI candidate {len(candidate_rows)}，"
        f"最终 poi_schema 合法 {len(poi_rows)}，失败 {len(failures)}。"
    )
    print(f"输出目录：{output_dir}")
    if candidate_rows and not poi_rows:
        print("提示：POI 候选仍缺地图交通或预算字段；补充 --map-enrichment 后才能进入 poi_seed.jsonl。")
    if interrupted:
        return 130
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
