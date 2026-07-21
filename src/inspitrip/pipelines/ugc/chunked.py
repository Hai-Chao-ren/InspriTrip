from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from dotenv import dotenv_values
from jsonschema import Draft7Validator

from inspitrip.paths import DEFAULT_ENV_PATH, PIPELINE_OUTPUT_DIR, PRIVATE_DATA_DIR, REPO_ROOT, SCHEMA_DIR

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from inspitrip.pipelines.ugc.client import OpenAIExtractor
from inspitrip.pipelines.ugc.db import load_notes
from inspitrip.pipelines.ugc.prompt import (
    bind_taxonomy_enums,
    build_system_prompt,
    build_user_input,
)
from inspitrip.pipelines.ugc.writer import load_schema


ROOT = REPO_ROOT
ENV_VALUES = dotenv_values(DEFAULT_ENV_PATH)


def split_text(text: str, max_chars: int) -> list[str]:
    chunks: list[str] = []
    current = ""
    for paragraph in (part.strip() for part in text.splitlines()):
        if not paragraph:
            continue
        while len(paragraph) > max_chars:
            head, paragraph = paragraph[:max_chars], paragraph[max_chars:]
            if current:
                chunks.append(current)
                current = ""
            chunks.append(head)
        candidate = f"{current}\n{paragraph}".strip()
        if current and len(candidate) > max_chars:
            chunks.append(current)
            current = paragraph
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks or [text]


def unique_list(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def merge_mentions(parts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[tuple[str, str, str], dict[str, Any]] = {}
    for mention in parts:
        key = (
            (mention.get("canonical_name") or mention.get("raw_place_name") or "").strip(),
            str(mention.get("city") or ""),
            str(mention.get("province") or "其他"),
        )
        if key not in merged:
            merged[key] = mention
            continue
        target = merged[key]
        for field in ("mood", "vibe", "activity"):
            target[field] = unique_list(target.get(field, []) + mention.get(field, []))
        seen_signals = {
            json.dumps(signal, ensure_ascii=False, sort_keys=True)
            for signal in target.get("budget_signals", [])
        }
        for signal in mention.get("budget_signals", []):
            encoded = json.dumps(signal, ensure_ascii=False, sort_keys=True)
            if encoded not in seen_signals:
                target.setdefault("budget_signals", []).append(signal)
                seen_signals.add(encoded)
        if not target.get("key_quote") and mention.get("key_quote"):
            target["key_quote"] = mention["key_quote"]
        target["trip_level"] = bool(target.get("trip_level") or mention.get("trip_level"))
        if target.get("duration_days_observed") is None and mention.get(
            "duration_days_observed"
        ) is not None:
            target["duration_raw_quote"] = mention.get("duration_raw_quote", "")
            target["duration_days_observed"] = mention["duration_days_observed"]
            target["duration_confidence"] = mention.get("duration_confidence", "低")
    return list(merged.values())


def main() -> int:
    parser = argparse.ArgumentParser(description="对超时的多地点笔记分块抽取")
    parser.add_argument("--note-id", required=True)
    parser.add_argument("--chunk-chars", type=int, default=550)
    parser.add_argument("--timeout", type=float, default=90)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--max-output-tokens", type=int, default=2500)
    args = parser.parse_args()

    api_key = str(ENV_VALUES.get("OPENAI_API_KEY") or "")
    base_url = ENV_VALUES.get("OPENAI_BASE_URL")
    model = str(ENV_VALUES.get("OPENAI_MODEL") or "gpt-5.6-luna")
    if not api_key:
        raise SystemExit("缺少 OPENAI_API_KEY")

    notes = load_notes(
        PRIVATE_DATA_DIR / "ExploreData.db",
        note_ids={args.note_id},
    )
    if not notes:
        raise SystemExit(f"找不到 note_id={args.note_id}")
    note = notes[0]
    taxonomy = SCHEMA_DIR / "intent_taxonomy.json"
    schema = bind_taxonomy_enums(
        load_schema(SCHEMA_DIR / "note_extraction_schema.json"), taxonomy
    )
    validator = Draft7Validator(schema)
    extractor = OpenAIExtractor(
        api_key=api_key,
        base_url=base_url,
        model=model,
        schema=schema,
        output_mode="auto",
        timeout=args.timeout,
        max_output_tokens=args.max_output_tokens,
        reasoning_effort="none",
    )
    chunks = split_text(note.get("content", ""), max(args.chunk_chars, 200))
    results: list[dict[str, Any]] = []
    metas: list[dict[str, Any]] = []
    instructions = build_system_prompt(taxonomy)
    for index, chunk in enumerate(chunks, 1):
        print(f"[chunk {index}/{len(chunks)}] chars={len(chunk)}", flush=True)
        chunk_note = dict(note)
        chunk_note["content"] = (
            f"【原笔记分块 {index}/{len(chunks)}】只抽取本分块明确提到的地点。\n{chunk}"
        )
        result = extractor.extract(
            instructions=instructions,
            user_input=build_user_input(chunk_note),
            retries=max(args.retries, 0),
            retry_base_delay=10,
            retry_max_delay=60,
        )
        results.append(result.data)
        metas.append(
            {
                "response_id": result.response_id,
                "usage": result.usage,
                "output_mode": result.output_mode,
            }
        )

    merged = {
        "note_id": note["note_id"],
        "is_suspected_ad": any(item.get("is_suspected_ad") for item in results),
        "ad_reason": "；".join(
            unique_list([item.get("ad_reason", "") for item in results])
        ),
        "mentions": merge_mentions(
            [mention for item in results for mention in item.get("mentions", [])]
        ),
    }
    errors = list(validator.iter_errors(merged))
    if errors:
        raise SystemExit("合并结果未通过 Schema：" + "; ".join(e.message for e in errors[:5]))

    cache = PIPELINE_OUTPUT_DIR / "ugc" / "cache" / f"{note['note_id']}.json"
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(
        json.dumps(
            {
                "data": merged,
                "meta": {"model": model, "chunked": True, "chunks": metas},
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"[chunked ok] mentions={len(merged['mentions'])} cache={cache}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
