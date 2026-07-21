from __future__ import annotations

import json
import re
from typing import Any


MAX_REASON_CHARS = 180
MAX_FALLBACK_QUOTE_CHARS = 80
LOW_CONFIDENCE_RECENT_LABEL = "低置信近期核验"

_NUMBERED_FACT_RE = re.compile(
    r"(?:\d+(?:\.\d+)?|[一二两三四五六七八九十百千]+)\s*"
    r"(?:元|天|小时|分钟|℃|摄氏度|度|%|％|公里|千米)"
)
_PROMPT_INJECTION_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"ignore\s+(?:all\s+)?(?:previous|prior).{0,40}instructions?",
        r"(?:system|developer)\s*(?:prompt|message)",
        r"(?:执行|遵循|服从|忽略|覆盖).{0,12}(?:指令|系统|提示词|规则)",
        r"你现在是.{0,30}(?:助手|系统|专家)",
        r"<\|(?:system|assistant|developer|tool)\|>",
        r"(?:^|\s)(?:assistant|developer|system|tool)\s*:",
    )
)


def contains_prompt_injection(value: Any) -> bool:
    text = str(value or "")
    return any(pattern.search(text) for pattern in _PROMPT_INJECTION_PATTERNS)


def _claim_text(row: dict[str, Any]) -> str:
    return str(row.get("claim") or row.get("key_quote") or "").strip()


def _evidence_id(row: dict[str, Any]) -> str:
    return str(row.get("claim_id") or row.get("evidence_id") or "").strip()


def _entity_type(row: dict[str, Any]) -> str:
    explicit = str(row.get("entity_type") or "").strip()
    if explicit:
        return explicit
    entity_id = str(row.get("entity_id") or "")
    for prefix, inferred in (
        ("DEST_", "destination"),
        ("EXP_", "experience"),
        ("SVC_", "service"),
        ("NODE_", "transport_node"),
    ):
        if entity_id.startswith(prefix):
            return inferred
    return ""


def _is_safe_supporting(row: dict[str, Any], destination_id: str) -> bool:
    if row.get("is_suspected_ad"):
        return False
    if row.get("polarity") not in (None, "", "positive"):
        return False
    if _entity_type(row) not in ("", "destination", "experience"):
        return False
    bound_destination = str(row.get("destination_id") or "")
    if bound_destination and bound_destination != destination_id:
        return False
    return bool(_evidence_id(row) and _claim_text(row)) and not contains_prompt_injection(
        _claim_text(row)
    )


def safe_supporting_evidence(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    destination_id = str(candidate.get("destination_id") or "")
    evidence = candidate.get("evidence") or {}
    return [
        dict(row)
        for row in evidence.get("supporting") or []
        if isinstance(row, dict) and _is_safe_supporting(row, destination_id)
    ]


def build_reason_context(selected: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the only unstructured text the expression LLM may see."""
    context: list[dict[str, Any]] = []
    for candidate in selected:
        supporting = safe_supporting_evidence(candidate)
        context.append(
            {
                "destination_id": str(candidate.get("destination_id") or ""),
                "name": candidate.get("name"),
                "query_feeling": candidate.get("core_feeling"),
                "supporting_evidence": [
                    {
                        "evidence_id": _evidence_id(row),
                        "text": _claim_text(row),
                    }
                    for row in supporting[:2]
                ],
            }
        )
    return context


def _normalize_for_overlap(value: Any) -> str:
    return "".join(
        character.lower()
        for character in str(value or "")
        if character.isalnum()
    )


def _has_evidence_overlap(reason: str, evidence_text: str) -> bool:
    left = _normalize_for_overlap(reason)
    right = _normalize_for_overlap(evidence_text)
    if not left or not right:
        return False
    if min(len(left), len(right)) >= 6:
        right_sixgrams = {right[index : index + 6] for index in range(len(right) - 5)}
        if any(left[index : index + 6] in right_sixgrams for index in range(len(left) - 5)):
            return True
    if min(len(left), len(right)) >= 4:
        left_fourgrams = {left[index : index + 4] for index in range(len(left) - 3)}
        right_fourgrams = {right[index : index + 4] for index in range(len(right) - 3)}
        if len(left_fourgrams & right_fourgrams) >= 2:
            return True
    left_words = set(re.findall(r"[a-z]{3,}", str(reason).lower()))
    right_words = set(re.findall(r"[a-z]{3,}", str(evidence_text).lower()))
    return len(left_words & right_words) >= 2


def _parse_llm_output(payload: Any) -> tuple[list[dict[str, Any]], str | None]:
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (TypeError, ValueError, json.JSONDecodeError):
            return [], "invalid_json"
    if isinstance(payload, dict):
        for key in ("recommendations", "items", "selected"):
            if key in payload:
                payload = payload.get(key)
                break
    if not isinstance(payload, list):
        return [], "invalid_shape"
    if not all(isinstance(row, dict) for row in payload):
        return [], "invalid_item"
    return [dict(row) for row in payload], None


def _fallback_recommendation(candidate: dict[str, Any]) -> dict[str, Any]:
    supporting = safe_supporting_evidence(candidate)
    if not supporting:
        return {
            "destination_id": str(candidate.get("destination_id") or ""),
            "reason": "感觉理由未通过证据校验，请仅参考事实卡。",
            "evidence_ids": [],
            "fallback": True,
        }
    evidence = supporting[0]
    evidence_id = _evidence_id(evidence)
    quote = " ".join(_claim_text(evidence).split())
    if len(quote) > MAX_FALLBACK_QUOTE_CHARS:
        quote = quote[:MAX_FALLBACK_QUOTE_CHARS].rstrip() + "…"
    reason = (
        "已找到与用户偏好相关且通过校验的体验证据。"
        if _NUMBERED_FACT_RE.search(quote)
        else f"证据原文提到：“{quote}”"
    )
    return {
        "destination_id": str(candidate.get("destination_id") or ""),
        "reason": reason,
        "evidence_ids": [evidence_id],
        "fallback": True,
    }


def validate_and_repair_llm_output(
    payload: Any,
    selected: list[dict[str, Any]],
) -> dict[str, Any]:
    """Conservatively validates the small LLM expression contract.

    The returned recommendation list always follows the backend-selected destination
    order. Invalid or unverifiable free text is replaced by a deterministic template.
    Numeric facts are intentionally rejected here because facts are rendered by code.
    """

    rows, parse_error = _parse_llm_output(payload)
    selected_ids = [str(row.get("destination_id") or "") for row in selected]
    output_ids = [str(row.get("destination_id") or "") for row in rows]
    errors: list[dict[str, Any]] = []
    if parse_error:
        errors.append({"code": parse_error})
    if output_ids != selected_ids:
        errors.append(
            {
                "code": "destination_set_or_order_mismatch",
                "expected": selected_ids,
                "received": output_ids,
            }
        )

    rows_by_id: dict[str, dict[str, Any]] = {}
    duplicate_ids: set[str] = set()
    for row in rows:
        destination_id = str(row.get("destination_id") or "")
        if destination_id in rows_by_id:
            duplicate_ids.add(destination_id)
        else:
            rows_by_id[destination_id] = row
    for destination_id in sorted(duplicate_ids):
        errors.append({"code": "duplicate_destination", "destination_id": destination_id})

    repaired: list[dict[str, Any]] = []
    for candidate in selected:
        destination_id = str(candidate.get("destination_id") or "")
        row = rows_by_id.get(destination_id)
        supporting = safe_supporting_evidence(candidate)
        supporting_by_id = {_evidence_id(item): item for item in supporting}
        item_errors: list[str] = []
        if row is None or destination_id in duplicate_ids:
            item_errors.append("missing_or_duplicate_destination")
        else:
            reason = str(row.get("reason") or "").strip()
            raw_evidence_ids = row.get("evidence_ids")
            evidence_ids = (
                [str(value) for value in raw_evidence_ids if value]
                if isinstance(raw_evidence_ids, list)
                else []
            )
            if not reason or len(reason) > MAX_REASON_CHARS:
                item_errors.append("invalid_reason_length")
            if contains_prompt_injection(reason):
                item_errors.append("prompt_injection_text")
            if _NUMBERED_FACT_RE.search(reason):
                item_errors.append("numeric_fact_in_free_text")
            if not evidence_ids:
                item_errors.append("missing_supporting_evidence")
            elif any(evidence_id not in supporting_by_id for evidence_id in evidence_ids):
                item_errors.append("invalid_supporting_evidence")
            elif not any(
                _has_evidence_overlap(reason, _claim_text(supporting_by_id[evidence_id]))
                for evidence_id in evidence_ids
            ):
                item_errors.append("reason_not_grounded_in_evidence_text")
        if item_errors:
            errors.extend(
                {"code": code, "destination_id": destination_id} for code in item_errors
            )
            repaired.append(_fallback_recommendation(candidate))
        else:
            repaired.append(
                {
                    "destination_id": destination_id,
                    "reason": str(row.get("reason") or "").strip(),
                    "evidence_ids": [str(value) for value in row.get("evidence_ids") if value],
                    "fallback": False,
                }
            )
    return {
        "passed": not errors,
        "selected_destination_ids": selected_ids,
        "recommendations": repaired,
        "fallback_used": any(row["fallback"] for row in repaired),
        "errors": errors,
    }


def _safe_caveats(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    destination_id = str(candidate.get("destination_id") or "")
    evidence = candidate.get("evidence") or {}
    rows: list[dict[str, Any]] = []
    for row in evidence.get("caveats") or []:
        if not isinstance(row, dict) or row.get("is_suspected_ad"):
            continue
        if _entity_type(row) not in ("", "destination", "experience"):
            continue
        if row.get("destination_id") and str(row.get("destination_id")) != destination_id:
            continue
        if contains_prompt_injection(_claim_text(row)):
            continue
        rows.append(dict(row))
    return rows


def build_verified_fact_cards(
    selected: list[dict[str, Any]],
    live_context: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Builds structured cards without deriving or recalculating facts."""

    live_items = ((live_context or {}).get("items") or {})
    cards: list[dict[str, Any]] = []
    for candidate in selected:
        destination_id = str(candidate.get("destination_id") or "")
        metadata = candidate.get("metadata") or {}
        live = live_items.get(destination_id) or {}
        web = live.get("web_verification") or {}
        card: dict[str, Any] = {
            "destination_id": destination_id,
            "name": candidate.get("name"),
            "city": candidate.get("city"),
            "core_feeling": candidate.get("core_feeling"),
            "atmosphere": candidate.get("atmosphere"),
            "assumptions": list(candidate.get("assumptions") or []),
            "duration": {
                "min_days": metadata.get("duration_min"),
                "max_days": metadata.get("duration_max"),
                "source": metadata.get("duration_source"),
            },
            "budget": {
                "typical": metadata.get("budget_typical"),
                "confidence": metadata.get("budget_confidence"),
                "filterable": metadata.get("budget_filterable"),
            },
            "travel_options": [dict(row) for row in candidate.get("travel_options") or []],
            "caveats": _safe_caveats(candidate),
        }
        weather = live.get("weather") or {}
        if weather.get("available"):
            card["weather"] = dict(weather)
        if web.get("available"):
            card["external_verification"] = {
                "label": web.get("verification_label")
                or LOW_CONFIDENCE_RECENT_LABEL,
                "confidence": "低",
                "best_season_sources": list(web.get("best_season_sources") or []),
                "recent_crowd_and_trend_sources": list(
                    web.get("recent_crowd_and_trend_sources") or []
                ),
            }
        cards.append(card)
    return cards


def diagnose_empty_result(result: dict[str, Any]) -> dict[str, Any]:
    if result.get("ok") is False or result.get("backend_degraded") or result.get("error"):
        return {
            "code": "backend_degraded",
            "message": "推荐后端发生降级，当前无法完成可靠推荐。",
        }
    retrieval_count = int(result.get("retrieval_count") or 0)
    hydrated_count = int(result.get("hydrated_count") or 0)
    if retrieval_count <= 0:
        return {"code": "retrieval_zero", "message": "知识检索没有召回目的地。"}
    if hydrated_count <= 0:
        return {
            "code": "metadata_parse_failure",
            "message": "检索结果未能解析出可用的目的地标识。",
        }
    eligible = result.get("eligible") or []
    rejected = result.get("rejected") or []
    if not eligible and rejected:
        reasons = {
            str(reason)
            for row in rejected
            for reason in (row.get("reasons") or [])
        }
        if reasons and reasons.issubset({"budget_unknown", "travel_time_unknown"}):
            return {
                "code": "strict_mode_unknown_facts",
                "message": "严格模式下，候选的预算或交通事实尚未核实。",
            }
        return {
            "code": "all_hard_constraints_rejected",
            "message": "召回目的地均不满足当前硬条件。",
            "rejection_reasons": sorted(reasons),
        }
    if eligible and not (result.get("selected") or []) and (
        result.get("evidence_rejected") or result.get("evidence_gaps")
    ):
        return {
            "code": "no_matching_evidence",
            "message": "候选满足条件，但没有通过准入的匹配证据。",
        }
    return {
        "code": "empty_result_unknown",
        "message": "当前没有可安全输出的推荐结果。",
    }
