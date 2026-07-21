from __future__ import annotations

import json
from typing import Any, Iterable

from .query_plan import empty_query_plan, normalize_query_plan


HARD_SCALARS = (
    "hard_constraints.origin",
    "hard_constraints.days_max",
    "hard_constraints.budget_max",
    "hard_constraints.travel_time_max",
)
REQUIRED_HARD_SLOTS = (
    "hard_constraints.origin",
    "hard_constraints.budget_max",
    "hard_constraints.days_max",
)
REQUIRED_SLOT_LABELS = {
    "hard_constraints.origin": "出发城市",
    "hard_constraints.budget_max": "人均预算",
    "hard_constraints.days_max": "出行天数",
}
HARD_ARRAYS = (
    "hard_constraints.transport_modes",
    "hard_constraints.must_have_activities",
)
SOFT_ARRAYS = (
    "soft_preferences.mood",
    "soft_preferences.vibe",
    "soft_preferences.activity",
)
KNOWN_SLOTS = {
    "scope", "task_type", "target_destination", "semantic_query", "exclusions",
    "evidence_aspects", *HARD_SCALARS, *HARD_ARRAYS, *SOFT_ARRAYS,
}


def _copy(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False))


def _get(plan: dict[str, Any], path: str) -> Any:
    value: Any = plan
    for part in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def _set(plan: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    target = plan
    for part in parts[:-1]:
        target = target.setdefault(part, {})
    target[parts[-1]] = _copy(value)


def _dedupe(values: Iterable[Any]) -> list[Any]:
    result: list[Any] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result


def _explicit_from_partial(value: dict[str, Any]) -> set[str]:
    explicit: set[str] = set()
    for key in ("scope", "task_type", "target_destination", "semantic_query", "exclusions", "evidence_aspects"):
        if key in value:
            explicit.add(key)
    for parent in ("hard_constraints", "soft_preferences"):
        nested = value.get(parent)
        if isinstance(nested, dict):
            explicit.update(f"{parent}.{key}" for key in nested)
    return explicit & KNOWN_SLOTS


def _meaningful_slots(plan: dict[str, Any]) -> set[str]:
    result: set[str] = set()
    for slot in KNOWN_SLOTS:
        value = _get(plan, slot)
        if value not in (None, "", []):
            result.add(slot)
    return result


def _normalize_delta(
    current_delta: dict[str, Any],
) -> tuple[dict[str, Any], set[str], set[str], set[str], str, dict[str, set[str]]]:
    if not isinstance(current_delta, dict):
        raise ValueError("current_delta must be an object")
    if "query_plan" in current_delta:
        raw_plan = current_delta.get("query_plan") or {}
        plan = normalize_query_plan(raw_plan, raw_query=str(current_delta.get("raw_query") or ""))
        explicit = set(current_delta.get("explicit_slots") or []) & KNOWN_SLOTS
        clear = set(current_delta.get("clear_slots") or []) & KNOWN_SLOTS
        replace = set(current_delta.get("replace_slots") or []) & KNOWN_SLOTS
        raw_query = str(current_delta.get("raw_query") or "").strip()
        operations = {
            "remove_activity_ids": set(current_delta.get("remove_activity_ids") or []),
            "remove_exclusions": set(current_delta.get("remove_exclusions") or []),
            "remove_soft_tag_ids": set(current_delta.get("remove_soft_tag_ids") or []),
            "remove_evidence_aspects": set(current_delta.get("remove_evidence_aspects") or []),
        }
        return plan, explicit, clear, replace, raw_query, operations
    explicit = _explicit_from_partial(current_delta)
    raw_plan = empty_query_plan()
    for slot in explicit:
        _set(raw_plan, slot, _get(current_delta, slot))
    plan = normalize_query_plan(raw_plan)
    return plan, explicit or _meaningful_slots(plan), set(), set(), "", {
        "remove_activity_ids": set(),
        "remove_exclusions": set(),
        "remove_soft_tag_ids": set(),
        "remove_evidence_aspects": set(),
    }


def _apply_form_values(plan: dict[str, Any], form_values: dict[str, Any] | None) -> dict[str, Any]:
    if not form_values:
        return plan
    result = _copy(plan)
    mappings = {
        "origin": "hard_constraints.origin",
        "days": "hard_constraints.days_max",
        "days_max": "hard_constraints.days_max",
        "budget": "hard_constraints.budget_max",
        "budget_max": "hard_constraints.budget_max",
        "travel_time_max": "hard_constraints.travel_time_max",
        "transport_modes": "hard_constraints.transport_modes",
    }
    for key, slot in mappings.items():
        if key not in form_values or form_values[key] in (None, ""):
            continue
        value = form_values[key]
        if slot in HARD_SCALARS and slot != "hard_constraints.origin":
            try:
                value = int(float(value))
            except (TypeError, ValueError):
                continue
        elif slot == "hard_constraints.origin":
            value = str(value).strip() or None
        elif slot == "hard_constraints.transport_modes" and isinstance(value, str):
            value = [part.strip() for part in value.replace("，", ",").split(",") if part.strip()]
        _set(result, slot, value)
    return normalize_query_plan(result)


def _merge_weighted(previous: list[dict[str, Any]], current: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {str(item.get("id")): _copy(item) for item in previous if item.get("id")}
    for item in current:
        tag_id = str(item.get("id") or "")
        if tag_id:
            by_id[tag_id] = _copy(item)
    return sorted(by_id.values(), key=lambda item: float(item.get("confidence") or 0), reverse=True)[:2]


def _merge_semantic(previous: str, current: str, *, replace: bool) -> str:
    previous = previous.strip()
    current = current.strip()
    if replace or not previous:
        return current
    if not current or current in previous:
        return previous
    if previous in current:
        return current
    return f"{previous}；{current}"


def merge_query_plan(
    previous_plan: dict[str, Any] | None,
    current_delta: dict[str, Any],
    *,
    form_values: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Merge one turn using explicit user > form > previous > null priority."""
    previous = normalize_query_plan(previous_plan or empty_query_plan())
    current, explicit, clear, replace, _raw_query, operations = _normalize_delta(current_delta)

    # Form values override confirmed state, then explicit turn operations win.
    merged = _apply_form_values(previous, form_values)
    for slot in clear:
        if slot in HARD_SCALARS or slot in {"target_destination", "semantic_query"}:
            _set(merged, slot, None if slot != "semantic_query" else "")
        else:
            _set(merged, slot, [])

    for slot in explicit - clear:
        current_value = _get(current, slot)
        if slot in HARD_SCALARS or slot in {"scope", "task_type", "target_destination"}:
            _set(merged, slot, current_value)
        elif slot in HARD_ARRAYS or slot in {"exclusions", "evidence_aspects"}:
            old = _get(merged, slot) or []
            _set(merged, slot, current_value if slot in replace else _dedupe([*old, *(current_value or [])]))
        elif slot in SOFT_ARRAYS:
            old = _get(merged, slot) or []
            _set(merged, slot, current_value if slot in replace else _merge_weighted(old, current_value or []))
        elif slot == "semantic_query":
            merged["semantic_query"] = _merge_semantic(
                str(merged.get("semantic_query") or ""),
                str(current_value or ""),
                replace=slot in replace,
            )

    remove_activity_ids = operations["remove_activity_ids"]
    if remove_activity_ids:
        merged["hard_constraints"]["must_have_activities"] = [
            value
            for value in merged["hard_constraints"].get("must_have_activities") or []
            if value not in remove_activity_ids
        ]
        merged["soft_preferences"]["activity"] = [
            item
            for item in merged["soft_preferences"].get("activity") or []
            if item.get("id") not in remove_activity_ids
        ]
    if operations["remove_exclusions"]:
        merged["exclusions"] = [
            value for value in merged.get("exclusions") or []
            if value not in operations["remove_exclusions"]
        ]
    if operations["remove_soft_tag_ids"]:
        for dimension in ("mood", "vibe", "activity"):
            merged["soft_preferences"][dimension] = [
                item
                for item in merged["soft_preferences"].get(dimension) or []
                if item.get("id") not in operations["remove_soft_tag_ids"]
            ]
    if operations["remove_evidence_aspects"]:
        merged["evidence_aspects"] = [
            value for value in merged.get("evidence_aspects") or []
            if value not in operations["remove_evidence_aspects"]
        ]

    exclusions = set(merged.get("exclusions") or [])
    hard_activities = merged["hard_constraints"].get("must_have_activities") or []
    merged["hard_constraints"]["must_have_activities"] = [
        activity for activity in hard_activities if activity not in exclusions
    ]
    soft_activities = merged["soft_preferences"].get("activity") or []
    merged["soft_preferences"]["activity"] = [
        item for item in soft_activities if item.get("id") not in exclusions
    ]

    if "hard_constraints.budget_max" in clear:
        merged["evidence_aspects"] = [value for value in merged["evidence_aspects"] if value != "cost"]
    if "hard_constraints.travel_time_max" in clear and not merged["hard_constraints"].get("origin") and not merged["hard_constraints"].get("transport_modes"):
        merged["evidence_aspects"] = [value for value in merged["evidence_aspects"] if value != "transport"]

    return normalize_query_plan(merged)


def missing_required_hard_slots(plan: dict[str, Any]) -> list[str]:
    """Return missing business-required slots after query and form values merge."""
    normalized = normalize_query_plan(plan)
    return [slot for slot in REQUIRED_HARD_SLOTS if _get(normalized, slot) in (None, "")]


def build_required_clarification_question(missing_slots: Iterable[str], *, repeated: bool = False) -> str:
    labels = [REQUIRED_SLOT_LABELS[slot] for slot in missing_slots if slot in REQUIRED_SLOT_LABELS]
    if not labels:
        return ""
    if len(labels) == 1:
        label_text = labels[0]
    else:
        label_text = "、".join(labels[:-1]) + "和" + labels[-1]
    if repeated:
        return f"还需要补齐{label_text}，确认后才能生成可执行的目的地推荐。"
    return f"为了给出可执行的推荐，还需要确认{label_text}。我会一次问全，不让你逐项来回补充。"


def decide_clarification(plan: dict[str, Any], clarification_count: int) -> dict[str, Any]:
    """Block retrieval until origin, budget and days are all confirmed."""
    normalized = normalize_query_plan(plan)
    count = max(0, int(clarification_count or 0))
    if normalized["scope"] != "in_domain" or normalized["task_type"] not in {"destination_discovery", "experience_lookup"}:
        return {
            "should_clarify": False,
            "question": None,
            "missing_slots": [],
            "reason": "route_does_not_need_retrieval",
        }
    missing = missing_required_hard_slots(normalized)
    if not missing:
        return {
            "should_clarify": False,
            "question": None,
            "missing_slots": [],
            "reason": "enough_information",
        }
    return {
        "should_clarify": True,
        "question": build_required_clarification_question(missing, repeated=count >= 1),
        "missing_slots": missing,
        "reason": "required_slots_still_missing" if count >= 1 else "missing_required_slots",
    }
