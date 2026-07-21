from __future__ import annotations

import hashlib
import re
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any


COLUMN_MAP = {
    "note_id": "作品ID",
    "note_title": "作品标题",
    "content": "作品描述",
    "source_url": "作品链接",
    "likes": "点赞数量",
    "collects": "收藏数量",
    "comments": "评论数量",
    "shares": "分享数量",
    "publish_date": "发布时间",
    "collected_date": "采集时间",
    "author_name": "作者昵称",
    "author_id": "作者ID",
    "raw_tags": "作品标签",
    "note_type": "作品类型",
}


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def parse_count(value: Any) -> int:
    text = _text(value).replace(",", "")
    if not text:
        return 0
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)", text)
    if not match:
        return 0
    number = float(match.group(1))
    if "万" in text:
        number *= 10_000
    return int(round(number))


def normalize_date(value: Any) -> str:
    text = _text(value)
    match = re.search(r"(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})", text)
    if not match:
        return ""
    year, month, day = (int(part) for part in match.groups())
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return ""


def _author_hash(author_id: str, author_name: str) -> str:
    identity = author_id or author_name
    if not identity:
        return ""
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]


def _pick_table(connection: sqlite3.Connection) -> str:
    tables = [
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
    ]
    if not tables:
        raise ValueError("数据库中没有表")
    if "explore_data" in tables:
        return "explore_data"
    return max(
        tables,
        key=lambda table: connection.execute(
            f'SELECT COUNT(*) FROM "{table}"'
        ).fetchone()[0],
    )


def load_notes(
    db_path: Path,
    *,
    limit: int | None = None,
    offset: int = 0,
    note_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    if not db_path.exists():
        raise FileNotFoundError(f"找不到数据库：{db_path}")

    connection = sqlite3.connect(str(db_path))
    connection.row_factory = sqlite3.Row
    try:
        table = _pick_table(connection)
        rows = connection.execute(f'SELECT * FROM "{table}" ORDER BY rowid').fetchall()
    finally:
        connection.close()

    notes: list[dict[str, Any]] = []
    today = date.today().isoformat()
    for row in rows:
        keys = set(row.keys())

        def get(field: str) -> str:
            column = COLUMN_MAP[field]
            return _text(row[column]) if column in keys else ""

        note_id = get("note_id")
        if note_ids and note_id not in note_ids:
            continue
        source_url = get("source_url")
        if not source_url and note_id:
            source_url = f"https://www.xiaohongshu.com/explore/{note_id}"
        publish_date = normalize_date(get("publish_date"))
        collected_date = normalize_date(get("collected_date")) or today
        author_id = get("author_id")
        author_name = get("author_name")
        notes.append(
            {
                "note_id": note_id,
                "note_title": get("note_title"),
                "content": get("content"),
                "source_url": source_url,
                "likes": parse_count(get("likes")),
                "collects": parse_count(get("collects")),
                "comments": parse_count(get("comments")),
                "shares": parse_count(get("shares")),
                "publish_date": publish_date,
                "collected_date": collected_date,
                "author_hash": _author_hash(author_id, author_name),
                "raw_tags": get("raw_tags"),
                "note_type": get("note_type"),
            }
        )

    selected = notes[offset:]
    if limit is not None:
        selected = selected[:limit]
    return selected

