from __future__ import annotations

from collections import Counter, defaultdict
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any


ALLOWED_EVENTS = {
    "session_start",
    "experiment_exposure",
    "location_request_start",
    "location_permission_result",
    "origin_resolved",
    "city_picker_open",
    "city_search",
    "inspiration_submit",
    "clarification_view",
    "clarification_complete",
    "recommend_request_start",
    "recommend_request_end",
    "result_set_view",
    "recommendation_impression",
    "evidence_expand",
    "recommendation_feedback",
    "followup_submit",
    "fallback_view",
    "retry_click",
}

EXPERIMENTS = {
    "location_prompt_timing_v1": ("auto_prompt", "intent_prompt"),
}

SENSITIVE_PROPERTY_KEYS = {
    "longitude",
    "latitude",
    "coordinates",
    "formatted_address",
    "raw_query",
    "query",
    "api_key",
    "authorization",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def assign_variant(anonymous_user_id: str, experiment_id: str) -> str:
    variants = EXPERIMENTS.get(experiment_id)
    if not variants:
        raise ValueError("unknown experiment")
    digest = hashlib.sha256(f"{experiment_id}:{anonymous_user_id}".encode("utf-8")).digest()
    return variants[int.from_bytes(digest[:8], "big") % len(variants)]


def stable_destination_key(name: str, city: str) -> str:
    value = f"{str(name).strip()}|{str(city).strip()}".encode("utf-8")
    return hashlib.sha256(value).hexdigest()[:16]


def sanitize_properties(value: Any, *, depth: int = 0) -> Any:
    if depth > 4:
        return None
    if isinstance(value, dict):
        result = {}
        for key, item in list(value.items())[:50]:
            safe_key = str(key)[:64]
            if safe_key.lower() in SENSITIVE_PROPERTY_KEYS:
                continue
            result[safe_key] = sanitize_properties(item, depth=depth + 1)
        return result
    if isinstance(value, list):
        return [sanitize_properties(item, depth=depth + 1) for item in value[:20]]
    if isinstance(value, str):
        return value[:500]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:500]


class AnalyticsStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        return connection

    def _ensure_schema(self) -> None:
        with closing(self._connect()) as connection:
            connection.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS analytics_events (
                    event_id TEXT PRIMARY KEY,
                    event_name TEXT NOT NULL,
                    event_time TEXT NOT NULL,
                    received_at TEXT NOT NULL,
                    anonymous_user_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL DEFAULT '',
                    request_id TEXT NOT NULL DEFAULT '',
                    experiment_id TEXT NOT NULL DEFAULT '',
                    variant TEXT NOT NULL DEFAULT '',
                    page TEXT NOT NULL DEFAULT '',
                    properties_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_analytics_event_name
                    ON analytics_events(event_name);
                CREATE INDEX IF NOT EXISTS idx_analytics_session
                    ON analytics_events(session_id);
                CREATE INDEX IF NOT EXISTS idx_analytics_experiment
                    ON analytics_events(experiment_id, variant);
                CREATE TABLE IF NOT EXISTS recommendation_feedback (
                    anonymous_user_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL DEFAULT '',
                    request_id TEXT NOT NULL,
                    destination_key TEXT NOT NULL,
                    feedback TEXT NOT NULL,
                    reason_code TEXT NOT NULL DEFAULT '',
                    experiment_id TEXT NOT NULL DEFAULT '',
                    variant TEXT NOT NULL DEFAULT '',
                    is_demo INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (
                        anonymous_user_id, session_id, request_id, destination_key
                    )
                );
                """
            )
            columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(recommendation_feedback)")
            }
            if "is_demo" not in columns:
                connection.execute(
                    "ALTER TABLE recommendation_feedback ADD COLUMN is_demo INTEGER NOT NULL DEFAULT 0"
                )
            connection.commit()

    def record(self, events: list[dict[str, Any]]) -> int:
        rows = []
        received_at = utc_now()
        for event in events:
            event_name = str(event.get("event_name") or "")
            if event_name not in ALLOWED_EVENTS:
                raise ValueError(f"unsupported event: {event_name}")
            properties = sanitize_properties(event.get("properties") or {})
            encoded = json.dumps(properties, ensure_ascii=False, separators=(",", ":"))
            if len(encoded.encode("utf-8")) > 16_384:
                raise ValueError("event properties too large")
            rows.append(
                (
                    str(event.get("event_id") or "")[:80],
                    event_name,
                    str(event.get("event_time") or received_at)[:64],
                    received_at,
                    str(event.get("anonymous_user_id") or "")[:128],
                    str(event.get("session_id") or "")[:128],
                    str(event.get("conversation_id") or "")[:128],
                    str(event.get("request_id") or "")[:128],
                    str(event.get("experiment_id") or "")[:80],
                    str(event.get("variant") or "")[:80],
                    str(event.get("page") or "")[:32],
                    encoded,
                )
            )
        with closing(self._connect()) as connection:
            before = connection.total_changes
            connection.executemany(
                """
                INSERT OR IGNORE INTO analytics_events (
                    event_id, event_name, event_time, received_at,
                    anonymous_user_id, session_id, conversation_id, request_id,
                    experiment_id, variant, page, properties_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            accepted = connection.total_changes - before
            connection.commit()
            return accepted

    def record_feedback(self, feedback: dict[str, Any]) -> None:
        action = str(feedback.get("feedback") or "")
        if action not in {"want_to_go", "not_interested", "report_issue"}:
            raise ValueError("unsupported feedback")
        with closing(self._connect()) as connection:
            connection.execute(
                """
                INSERT INTO recommendation_feedback (
                    anonymous_user_id, session_id, conversation_id, request_id,
                    destination_key, feedback, reason_code,
                    experiment_id, variant, is_demo, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (
                    anonymous_user_id, session_id, request_id, destination_key
                ) DO UPDATE SET
                    feedback=excluded.feedback,
                    reason_code=excluded.reason_code,
                    experiment_id=excluded.experiment_id,
                    variant=excluded.variant,
                    is_demo=excluded.is_demo,
                    updated_at=excluded.updated_at
                """,
                (
                    str(feedback.get("anonymous_user_id") or "")[:128],
                    str(feedback.get("session_id") or "")[:128],
                    str(feedback.get("conversation_id") or "")[:128],
                    str(feedback.get("request_id") or "")[:128],
                    str(feedback.get("destination_key") or "")[:128],
                    action,
                    str(feedback.get("reason_code") or "")[:80],
                    str(feedback.get("experiment_id") or "")[:80],
                    str(feedback.get("variant") or "")[:80],
                    int(bool(feedback.get("is_demo"))),
                    utc_now(),
                ),
            )
            connection.commit()

    def summary(self, scope: str = "production") -> dict[str, Any]:
        if scope not in {"production", "demo", "all"}:
            raise ValueError("invalid analytics scope")
        event_where = {
            "production": "COALESCE(json_extract(properties_json, '$.is_demo'), 0) = 0",
            "demo": "COALESCE(json_extract(properties_json, '$.is_demo'), 0) = 1",
            "all": "1 = 1",
        }[scope]
        feedback_where = {
            "production": "is_demo = 0",
            "demo": "is_demo = 1",
            "all": "1 = 1",
        }[scope]
        with closing(self._connect()) as connection:
            rows = connection.execute(
                f"""
                SELECT event_name, anonymous_user_id, session_id,
                       experiment_id, variant, properties_json
                  FROM analytics_events
                 WHERE {event_where}
                 ORDER BY received_at, rowid
                """
            ).fetchall()
            feedback_rows = connection.execute(
                f"""
                SELECT anonymous_user_id, session_id, feedback,
                       experiment_id, variant
                  FROM recommendation_feedback
                 WHERE {feedback_where}
                """
            ).fetchall()

        events_by_name = Counter(row["event_name"] for row in rows)
        sessions_by_name: dict[str, set[str]] = defaultdict(set)
        experiment_users: dict[str, dict[str, dict[str, set[str]]]] = defaultdict(
            lambda: defaultdict(lambda: defaultdict(set))
        )
        positive_sessions = {
            row["session_id"]
            for row in feedback_rows
            if row["feedback"] == "want_to_go"
        }
        successful_result_sessions: set[str] = set()
        for row in rows:
            event_name = row["event_name"]
            session_id = row["session_id"]
            sessions_by_name[event_name].add(session_id)
            try:
                properties = json.loads(row["properties_json"] or "{}")
            except ValueError:
                properties = {}
            if event_name == "recommend_request_end" and properties.get("status") == "success":
                successful_result_sessions.add(session_id)
            experiment_id = row["experiment_id"]
            variant = row["variant"]
            if experiment_id and variant:
                experiment_users[experiment_id][variant][event_name].add(
                    row["anonymous_user_id"]
                )

        submitted_sessions = sessions_by_name.get("inspiration_submit", set())
        north_star = (
            len(positive_sessions & submitted_sessions) / len(submitted_sessions)
            if submitted_sessions
            else 0.0
        )
        experiments = {}
        feedback_users_by_experiment: dict[str, dict[str, set[str]]] = defaultdict(
            lambda: defaultdict(set)
        )
        for row in feedback_rows:
            if row["experiment_id"] and row["variant"]:
                feedback_users_by_experiment[row["experiment_id"]][row["variant"]].add(
                    row["anonymous_user_id"]
                )
        for experiment_id, variants in experiment_users.items():
            experiments[experiment_id] = {}
            for variant, event_users in variants.items():
                exposed = event_users.get("experiment_exposure", set())
                origin = event_users.get("origin_resolved", set())
                submitted = event_users.get("inspiration_submit", set())
                feedback_users = feedback_users_by_experiment[experiment_id][variant]
                experiments[experiment_id][variant] = {
                    "exposed_users": len(exposed),
                    "origin_resolved_users": len(origin),
                    "submitted_users": len(submitted),
                    "feedback_users": len(feedback_users),
                    "origin_resolution_rate": round(len(origin & exposed) / len(exposed), 4)
                    if exposed
                    else 0.0,
                    "submit_rate": round(len(submitted & exposed) / len(exposed), 4)
                    if exposed
                    else 0.0,
                }

        return {
            "scope": scope,
            "event_count": len(rows),
            "feedback_count": len(feedback_rows),
            "session_count": len({row["session_id"] for row in rows}),
            "events_by_name": dict(sorted(events_by_name.items())),
            "funnel": {
                "submitted_sessions": len(submitted_sessions),
                "successful_result_sessions": len(successful_result_sessions),
                "result_view_sessions": len(sessions_by_name.get("result_set_view", set())),
                "positive_feedback_sessions": len(positive_sessions),
                "effective_inspiration_conversion_rate": round(north_star, 4),
            },
            "experiments": experiments,
        }
