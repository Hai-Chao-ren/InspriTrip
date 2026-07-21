from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from inspitrip.pipelines.ugc.db import load_notes
from inspitrip.paths import PIPELINE_OUTPUT_DIR, PRIVATE_DATA_DIR


RUN_MODULE = "inspitrip.pipelines.ugc.run"
DB = PRIVATE_DATA_DIR / "ExploreData.db"
OUTPUT = PIPELINE_OUTPUT_DIR / "ugc"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="逐条断点续跑 POI LLM 抽取")
    parser.add_argument("--initial-cooldown", type=int, default=300)
    parser.add_argument("--cooldown", type=int, default=300, help="失败后的冷却秒数")
    parser.add_argument("--timeout", type=int, default=90, help="单次 API 超时秒数")
    parser.add_argument("--attempts", type=int, default=6, help="最多轮询次数")
    parser.add_argument("--max-output-tokens", type=int, default=3500)
    parser.add_argument("--reasoning-effort", default="none")
    return parser


def cache_path(note_id: str) -> Path:
    return OUTPUT / "cache" / f"{note_id}.json"


def run_one(note_id: str, args: argparse.Namespace) -> int:
    command = [
        sys.executable,
        "-u",
        "-m",
        RUN_MODULE,
        "--note-id",
        note_id,
        "--reasoning-effort",
        args.reasoning_effort,
        "--timeout",
        str(args.timeout),
        "--retries",
        "0",
        "--max-output-tokens",
        str(args.max_output_tokens),
        "--request-interval",
        "0",
    ]
    return subprocess.run(command, cwd=ROOT, check=False).returncode


def materialize_all(args: argparse.Namespace) -> int:
    command = [
        sys.executable,
        "-u",
        str(RUN),
        "--all",
        "--reasoning-effort",
        args.reasoning_effort,
        "--timeout",
        str(args.timeout),
        "--retries",
        "0",
    ]
    return subprocess.run(command, cwd=ROOT, check=False).returncode


def main() -> int:
    args = build_parser().parse_args()
    notes = load_notes(DB)
    total = len(notes)
    missing = [note for note in notes if not cache_path(note["note_id"]).exists()]
    if missing and args.initial_cooldown > 0:
        print(f"[resume initial cooldown] {args.initial_cooldown}s", flush=True)
        time.sleep(args.initial_cooldown)
    rounds = max(args.attempts, 1)
    for attempt in range(1, rounds + 1):
        missing = [note for note in notes if not cache_path(note["note_id"]).exists()]
        if not missing:
            break
        print(f"[resume round] {attempt}/{rounds} missing={len(missing)}", flush=True)
        for note in missing:
            index = notes.index(note) + 1
            note_id = note["note_id"]
            print(
                f"[resume {index}/{total}] {note_id} round={attempt}/{rounds}",
                flush=True,
            )
            run_one(note_id, args)
            if cache_path(note_id).exists():
                print(f"[resume ok] {note_id}", flush=True)
            else:
                print(f"[resume defer] {note_id}", flush=True)
        remaining = [note for note in notes if not cache_path(note["note_id"]).exists()]
        if remaining and attempt < rounds:
            print(
                f"[resume cooldown] {args.cooldown}s remaining={len(remaining)}",
                flush=True,
            )
            time.sleep(max(args.cooldown, 0))

    remaining = [note for note in notes if not cache_path(note["note_id"]).exists()]
    if remaining:
        print(f"[resume blocked] remaining={len(remaining)}", flush=True)
        for note in remaining:
            print(f"- {note['note_id']} {note['note_title']}", flush=True)
        return 1

    print("[resume] all cache complete; materializing full outputs", flush=True)
    return materialize_all(args)


if __name__ == "__main__":
    raise SystemExit(main())
