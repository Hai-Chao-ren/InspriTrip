from __future__ import annotations

import json
from pathlib import Path
from typing import Any


DEFAULT_WEIGHTS_PATH = Path(__file__).with_name("scoring_weights.json")


def load_weights(path: Path | None = None) -> dict[str, Any]:
    return json.loads((path or DEFAULT_WEIGHTS_PATH).read_text(encoding="utf-8"))


def _clamp(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return min(max(number, 0.0), 1.0)


def _tag_match(query_tags: list[dict[str, Any]], destination_scores: dict[str, Any]) -> float | None:
    if not query_tags:
        return None
    numerator = 0.0
    denominator = 0.0
    for item in query_tags:
        confidence = _clamp(item.get("confidence"))
        numerator += confidence * _clamp(destination_scores.get(item.get("id")))
        denominator += confidence
    return numerator / denominator if denominator else None


def score_candidate(
    candidate: dict[str, Any],
    query_plan: dict[str, Any],
    *,
    weights: dict[str, Any] | None = None,
) -> dict[str, Any]:
    weights = weights or load_weights()
    soft = query_plan.get("soft_preferences") or {}
    components: dict[str, float | None] = {
        "semantic": _clamp(
            candidate.get("semantic_match", candidate.get("rerank_score", candidate.get("score", 0.5))),
            0.5,
        ),
        "mood": _tag_match(list(soft.get("mood") or []), dict(candidate.get("mood_scores") or {})),
        "vibe": _tag_match(list(soft.get("vibe") or []), dict(candidate.get("vibe_scores") or {})),
        "activity": _tag_match(
            list(soft.get("activity") or []), dict(candidate.get("activity_scores") or {})
        ),
    }
    active = {
        key: value
        for key, value in components.items()
        if value is not None
    }
    preference_weights = weights["preference"]
    active_weight = sum(float(preference_weights[key]) for key in active) or 1.0
    preference_score = sum(
        float(preference_weights[key]) * float(value) for key, value in active.items()
    ) / active_weight
    final_weights = weights["final"]
    final_score = (
        float(final_weights["preference"]) * preference_score
        + float(final_weights["evidence_quality"]) * _clamp(candidate.get("evidence_quality"))
        + float(final_weights["freshness"]) * _clamp(candidate.get("freshness_score"))
        + float(final_weights["private_discovery"]) * _clamp(candidate.get("private_discovery_value"))
    )
    result = dict(candidate)
    result["preference_score"] = round(preference_score, 6)
    result["final_score"] = round(final_score, 6)
    result["score_components"] = {
        key: (None if value is None else round(float(value), 6))
        for key, value in components.items()
    }
    return result


def _candidate_text(candidate: dict[str, Any]) -> str:
    values = [
        candidate.get("name"),
        candidate.get("core_feeling"),
        candidate.get("atmosphere"),
        " ".join(candidate.get("suitable_scenes") or []),
        " ".join(candidate.get("activities") or []),
        " ".join(candidate.get("limitations") or []),
    ]
    return " ".join(str(value or "") for value in values)


def _travel_minutes(row: dict[str, Any]) -> int | None:
    value = row.get("door_to_door_typical")
    if value is None:
        value = row.get("travel_minutes")
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _travel_row_is_partial(row: dict[str, Any]) -> bool:
    raw_status = row.get("raw_status") or {}
    try:
        successes = int(raw_status.get("success_count") or 0)
        failures = int(raw_status.get("failure_count") or 0)
    except (TypeError, ValueError):
        return False
    return successes > 0 and failures > 0


def _has_meaningful_railway_segment(row: dict[str, Any]) -> bool:
    if row.get("rail_segment_typical") is not None:
        return True
    return any(
        isinstance(segment, dict)
        and any(
            segment.get(field) not in (None, "")
            for field in (
                "trip",
                "type",
                "duration_minutes",
                "departure_stop",
                "arrival_stop",
            )
        )
        for segment in row.get("railway_segments") or []
    )


def travel_row_supports_mode(row: dict[str, Any], requested_mode: str) -> bool | None:
    """Return True for confirmed support, False for confirmed mismatch, None for unknown.

    The matrix stores broad 自驾/公共交通 rows. High-speed rail and ferry may
    only pass when their subtype evidence is present; a generic successful
    public-transit row is therefore a confirmed mismatch for those modes.
    """
    row_mode = str(row.get("transport_mode") or "")
    usable = _travel_minutes(row) is not None and not row.get("failure_reason")
    if requested_mode == "自驾":
        if row_mode != "自驾":
            return False
        return True if usable else None
    if requested_mode == "公共交通":
        if row_mode != "公共交通":
            return False
        return True if usable else None
    if requested_mode == "高铁":
        if row_mode != "公共交通":
            return False
        if _has_meaningful_railway_segment(row):
            return True
        return False if usable else None
    if requested_mode == "轮渡":
        if bool(row.get("requires_ferry") or row.get("contains_ferry")):
            return True
        return False if usable else None
    if requested_mode in {"大巴", "地铁"}:
        if row_mode != "公共交通":
            return False
        # The current matrix does not preserve a trustworthy bus/subway
        # subtype marker. Keep it unknown instead of passing generic transit.
        return None
    return None


def _origin_rows(
    candidate: dict[str, Any],
    origin: Any,
    travel_index: dict[tuple[str, str, str], dict[str, Any]],
) -> list[dict[str, Any]]:
    if not origin:
        return []
    destination_id = str(candidate.get("destination_id") or "")
    return [
        row
        for mode in ("自驾", "公共交通")
        if (row := travel_index.get((destination_id, str(origin), mode))) is not None
    ]


def _evaluate_transport_modes(
    candidate: dict[str, Any],
    query_plan: dict[str, Any],
    travel_index: dict[tuple[str, str, str], dict[str, Any]],
    *,
    allow_unknown_hard_facts: bool,
) -> tuple[list[str], list[str]]:
    hard = query_plan.get("hard_constraints") or {}
    requested_modes = [str(value) for value in hard.get("transport_modes") or []]
    if not requested_modes:
        return [], []
    rows = _origin_rows(candidate, hard.get("origin"), travel_index)
    confirmed_rows: list[dict[str, Any]] = []
    unknown_modes: list[str] = []
    unavailable_modes: list[str] = []
    for mode in requested_modes:
        statuses = [travel_row_supports_mode(row, mode) for row in rows]
        if any(status is True for status in statuses):
            confirmed_rows.extend(row for row, status in zip(rows, statuses) if status is True)
        elif statuses and all(status is False for status in statuses):
            unavailable_modes.append(mode)
        else:
            unknown_modes.append(mode)
    if confirmed_rows:
        assumptions = []
        if all(_travel_row_is_partial(row) for row in confirmed_rows):
            assumptions.append("travel_partial_failure")
        return [], assumptions
    if unknown_modes:
        code = "transport_mode_unknown:" + ",".join(unknown_modes)
        return ([], [code]) if allow_unknown_hard_facts else ([code], [])
    return [f"transport_mode_unavailable:{mode}" for mode in unavailable_modes], []


def _travel_value(
    candidate: dict[str, Any],
    query_plan: dict[str, Any],
    travel_index: dict[tuple[str, str, str], dict[str, Any]],
) -> tuple[int | None, bool]:
    hard = query_plan.get("hard_constraints") or {}
    rows = _origin_rows(candidate, hard.get("origin"), travel_index)
    requested_modes = [str(value) for value in hard.get("transport_modes") or []]
    if requested_modes:
        rows = [
            row
            for row in rows
            if any(travel_row_supports_mode(row, mode) is True for mode in requested_modes)
        ]
    else:
        rows = [
            row
            for row in rows
            if travel_row_supports_mode(row, str(row.get("transport_mode") or "")) is True
        ]
    with_minutes = [(minutes, row) for row in rows if (minutes := _travel_minutes(row)) is not None]
    if not with_minutes:
        return None, False
    minutes, chosen = min(with_minutes, key=lambda item: item[0])
    return minutes, _travel_row_is_partial(chosen)


def hard_filter_candidate(
    candidate: dict[str, Any],
    query_plan: dict[str, Any],
    *,
    travel_index: dict[tuple[str, str, str], dict[str, Any]] | None = None,
    allow_unknown_hard_facts: bool = True,
    minimum_activity_score: float = 0.45,
) -> tuple[bool, list[str], list[str]]:
    reasons: list[str] = []
    assumptions: list[str] = []
    if candidate.get("status") not in (None, "", "active"):
        reasons.append("destination_inactive")
    hard = query_plan.get("hard_constraints") or {}
    metadata = candidate.get("metadata") or {}
    days_max = hard.get("days_max")
    duration_min = metadata.get("duration_min")
    if days_max is not None:
        if duration_min is None:
            if allow_unknown_hard_facts:
                assumptions.append("duration_unknown")
            else:
                reasons.append("duration_unknown")
        elif int(duration_min) > int(days_max):
            reasons.append("duration_exceeded")
    budget_max = hard.get("budget_max")
    budget_typical = metadata.get("budget_typical")
    budget_filterable = bool(metadata.get("budget_filterable"))
    if budget_max is not None:
        if budget_filterable and budget_typical is not None and int(budget_typical) > int(budget_max):
            reasons.append("budget_exceeded")
        elif not budget_filterable or budget_typical is None:
            if allow_unknown_hard_facts:
                assumptions.append("budget_unknown")
            else:
                reasons.append("budget_unknown")
    for activity in hard.get("must_have_activities") or []:
        if _clamp((candidate.get("activity_scores") or {}).get(activity)) < minimum_activity_score:
            reasons.append(f"missing_activity:{activity}")
    text = _candidate_text(candidate)
    for exclusion in query_plan.get("exclusions") or []:
        if str(exclusion).startswith("act_"):
            if _clamp((candidate.get("activity_scores") or {}).get(exclusion)) >= minimum_activity_score:
                reasons.append(f"excluded_activity:{exclusion}")
        elif exclusion and exclusion in text:
            reasons.append(f"excluded:{exclusion}")
    mode_reasons, mode_assumptions = _evaluate_transport_modes(
        candidate,
        query_plan,
        travel_index or {},
        allow_unknown_hard_facts=allow_unknown_hard_facts,
    )
    reasons.extend(mode_reasons)
    assumptions.extend(mode_assumptions)
    travel_max = hard.get("travel_time_max")
    if travel_max is not None:
        value, partial = _travel_value(candidate, query_plan, travel_index or {})
        if value is None:
            if allow_unknown_hard_facts:
                assumptions.append("travel_time_unknown")
            else:
                reasons.append("travel_time_unknown")
        elif value > int(travel_max):
            reasons.append("travel_time_exceeded")
        elif partial:
            assumptions.append("travel_partial_failure")
    return not reasons, list(dict.fromkeys(reasons)), list(dict.fromkeys(assumptions))


def _reason_detail(reason: str) -> dict[str, Any]:
    code, separator, value = str(reason).partition(":")
    constraint_by_code = {
        "destination_inactive": "status",
        "duration_exceeded": "duration",
        "duration_unknown": "duration",
        "budget_exceeded": "budget",
        "budget_unknown": "budget",
        "missing_activity": "activity",
        "excluded_activity": "exclusion",
        "excluded": "exclusion",
        "transport_mode_unavailable": "transport",
        "transport_mode_unknown": "transport",
        "travel_time_exceeded": "transport",
        "travel_time_unknown": "transport",
    }
    result: dict[str, Any] = {
        "code": code,
        "constraint": constraint_by_code.get(code, "other"),
    }
    if separator:
        result["value"] = value
    return result


def _apply_fact_adjustment(
    scored: dict[str, Any],
    assumptions: list[str],
    weights: dict[str, Any],
) -> dict[str, Any]:
    config = dict(weights.get("fact_adjustment") or {})
    penalty_by_code = dict(config.get("penalties") or {})
    applied: list[dict[str, Any]] = []
    for assumption in assumptions:
        code = str(assumption).partition(":")[0]
        try:
            penalty = max(0.0, float(penalty_by_code.get(code) or 0.0))
        except (TypeError, ValueError):
            penalty = 0.0
        if penalty:
            applied.append({"code": code, "penalty": round(penalty, 6)})
    try:
        maximum = max(0.0, float(config.get("maximum_penalty") or 0.0))
    except (TypeError, ValueError):
        maximum = 0.0
    total_penalty = sum(item["penalty"] for item in applied)
    if maximum:
        total_penalty = min(total_penalty, maximum)
    base_score = float(scored.get("final_score") or 0.0)
    result = dict(scored)
    result["final_score_pre_fact_adjustment"] = round(base_score, 6)
    result["final_score"] = round(max(0.0, base_score - total_penalty), 6)
    result["fact_adjustment"] = {
        "version": str(config.get("version") or "unversioned"),
        "penalty": round(total_penalty, 6),
        "applied": applied,
    }
    return result


def _similarity(left: dict[str, Any], right: dict[str, Any]) -> float:
    left_tags = set((left.get("mood_scores") or {})) | set((left.get("vibe_scores") or {})) | set(
        (left.get("activity_scores") or {})
    )
    right_tags = set((right.get("mood_scores") or {})) | set((right.get("vibe_scores") or {})) | set(
        (right.get("activity_scores") or {})
    )
    union = left_tags | right_tags
    tag_similarity = len(left_tags & right_tags) / len(union) if union else 0.0
    same_city = 1.0 if left.get("city") and left.get("city") == right.get("city") else 0.0
    left_bucket = left.get("diversity_bucket") or left.get("category")
    right_bucket = right.get("diversity_bucket") or right.get("category")
    same_category = 1.0 if left_bucket and left_bucket == right_bucket else 0.0
    return 0.4 * tag_similarity + 0.3 * same_city + 0.3 * same_category


def mmr_select(
    candidates: list[dict[str, Any]],
    *,
    limit: int = 5,
    lambda_value: float = 0.75,
) -> list[dict[str, Any]]:
    remaining = list(candidates)
    selected: list[dict[str, Any]] = []
    while remaining and len(selected) < limit:
        if not selected:
            chosen = max(remaining, key=lambda item: item.get("final_score", 0.0))
        else:
            chosen = max(
                remaining,
                key=lambda item: lambda_value * item.get("final_score", 0.0)
                - (1.0 - lambda_value) * max(_similarity(item, prior) for prior in selected),
            )
        selected.append(chosen)
        remaining.remove(chosen)
    return selected


def filter_and_rank(
    candidates: list[dict[str, Any]],
    query_plan: dict[str, Any],
    *,
    travel_rows: list[dict[str, Any]] | None = None,
    allow_unknown_hard_facts: bool = True,
    top_n: int = 10,
    final_limit: int = 5,
    weights: dict[str, Any] | None = None,
    apply_mmr: bool = True,
) -> dict[str, Any]:
    weights = weights or load_weights()
    travel_index = {
        (str(row.get("destination_id")), str(row.get("origin_city")), str(row.get("transport_mode"))): row
        for row in travel_rows or []
    }
    eligible: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for candidate in candidates:
        accepted, reasons, assumptions = hard_filter_candidate(
            candidate,
            query_plan,
            travel_index=travel_index,
            allow_unknown_hard_facts=allow_unknown_hard_facts,
            minimum_activity_score=float(weights.get("minimum_activity_score", 0.45)),
        )
        if not accepted:
            rejected.append(
                {
                    "destination_id": candidate.get("destination_id"),
                    "reasons": reasons,
                    "reason_details": [_reason_detail(reason) for reason in reasons],
                }
            )
            continue
        scored = score_candidate(candidate, query_plan, weights=weights)
        scored["assumptions"] = assumptions
        eligible.append(_apply_fact_adjustment(scored, assumptions, weights))
    eligible.sort(key=lambda item: item.get("final_score", 0.0), reverse=True)
    reranked = eligible[:top_n]
    selected = (
        mmr_select(
            reranked,
            limit=final_limit,
            lambda_value=float(weights.get("mmr_lambda", 0.75)),
        )
        if apply_mmr
        else reranked[:final_limit]
    )
    by_code: dict[str, int] = {}
    by_constraint: dict[str, int] = {}
    for row in rejected:
        for detail in row["reason_details"]:
            code = str(detail["code"])
            constraint = str(detail["constraint"])
            by_code[code] = by_code.get(code, 0) + 1
            by_constraint[constraint] = by_constraint.get(constraint, 0) + 1
    assumption_counts: dict[str, int] = {}
    for row in eligible:
        for assumption in row.get("assumptions") or []:
            code = str(assumption).partition(":")[0]
            assumption_counts[code] = assumption_counts.get(code, 0) + 1
    adjustment_version = str((weights.get("fact_adjustment") or {}).get("version") or "unversioned")
    return {
        "selected": selected,
        "eligible": eligible,
        "rejected": rejected,
        "rejection_diagnostics": {
            "version": "p3-v1",
            "total_rejected": len(rejected),
            "by_code": dict(sorted(by_code.items())),
            "by_constraint": dict(sorted(by_constraint.items())),
            "items": rejected,
        },
        "assumption_diagnostics": {
            "version": adjustment_version,
            "total_candidates_with_assumptions": sum(bool(row.get("assumptions")) for row in eligible),
            "by_code": dict(sorted(assumption_counts.items())),
        },
    }
