from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from inspitrip.recommendation.output_fidelity import (
    contains_prompt_injection,
    safe_supporting_evidence,
)


DEFAULT_ACTIVITY_THRESHOLD = 0.45
GENERAL_CAVEAT_ASPECT_PRIORITY = ("crowd", "commercialization", "cost", "transport")


def _score(mapping: Any, key: str) -> float:
    try:
        return float((mapping or {}).get(key) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def derive_diversity_bucket(
    candidate: dict[str, Any],
    *,
    activity_threshold: float = DEFAULT_ACTIVITY_THRESHOLD,
) -> str:
    """Derives an experience bucket from production fields, not `category=scenic`."""

    activity = candidate.get("activity_scores") or {}
    vibe = candidate.get("vibe_scores") or {}
    mood = candidate.get("mood_scores") or {}
    metadata = candidate.get("metadata") or {}
    name = str(candidate.get("name") or "")
    descriptive_text = " ".join(
        str(value or "")
        for value in (
            name,
            candidate.get("core_feeling"),
            candidate.get("atmosphere"),
            " ".join(candidate.get("activities") or []),
        )
    )

    # Geographic refinements split the sea-heavy inventory into meaningful experiences.
    if _score(activity, "act_sea") >= activity_threshold:
        if metadata.get("requires_ferry") or "岛" in name:
            return "island_escape"
        if any(token in name for token in ("渔村", "渔港", "村")):
            return "coastal_village"
        return "coastal_scenery"
    if _score(activity, "act_hike") >= activity_threshold or any(
        token in descriptive_text for token in ("登山", "徒步", "山野", "森林")
    ):
        return "mountain_trail"
    if _score(activity, "act_town") >= activity_threshold or _score(vibe, "vibe_ancient") >= activity_threshold:
        return "heritage_town"
    if _score(activity, "act_art") >= activity_threshold or _score(vibe, "vibe_artsy") >= activity_threshold:
        return "arts_and_culture"
    if max(_score(activity, "act_camp"), _score(activity, "act_ride")) >= activity_threshold:
        return "outdoor_roaming"
    if _score(activity, "act_food") >= activity_threshold or _score(vibe, "vibe_local") >= 0.7:
        return "local_food"
    if max(
        _score(activity, "act_cafe"),
        _score(activity, "act_stay"),
        _score(activity, "act_hotspring"),
    ) >= activity_threshold:
        return "slow_stay"
    if _score(vibe, "vibe_urban") >= activity_threshold or _score(mood, "mood_social") >= 0.7:
        return "urban_social"
    if max(
        _score(vibe, "vibe_nature"),
        _score(vibe, "vibe_unspoiled"),
        _score(mood, "mood_heal"),
        _score(mood, "mood_unwind"),
    ) >= activity_threshold:
        return "nature_retreat"
    return "general_discovery"


def annotate_diversity_buckets(
    candidates: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for candidate in candidates:
        row = dict(candidate)
        row["diversity_bucket"] = derive_diversity_bucket(row)
        result.append(row)
    return result


def refill_evidence_candidates(
    ranked_candidates: list[dict[str, Any]],
    enrich_batch: Callable[[list[dict[str, Any]]], list[dict[str, Any]]],
    *,
    final_limit: int,
    batch_size: int = 20,
) -> dict[str, Any]:
    """Iterates the ranked pool until evidence-backed capacity is full or exhausted.

    MMR is deliberately not run here. The caller must run final MMR over
    `eligible_with_evidence` after the evidence gate.
    """

    target = max(0, int(final_limit))
    page_size = max(1, int(batch_size))
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    batches_processed = 0
    examined_count = 0
    cursor = 0
    while cursor < len(ranked_candidates) and len(accepted) < target:
        batch = ranked_candidates[cursor : cursor + page_size]
        cursor += len(batch)
        batches_processed += 1
        enriched = enrich_batch(batch)
        by_id = {
            str(row.get("destination_id") or ""): row
            for row in enriched
            if row.get("destination_id")
        }
        for original in batch:
            examined_count += 1
            destination_id = str(original.get("destination_id") or "")
            row = by_id.get(destination_id)
            if row is None:
                rejected.append(
                    {
                        "destination_id": destination_id,
                        "reason": "evidence_loader_missing",
                    }
                )
                continue
            safe_support = safe_supporting_evidence(row)
            if row.get("evidence_gap") or not safe_support:
                rejected.append(
                    {
                        "destination_id": destination_id,
                        "reason": row.get("evidence_gap_reason")
                        or "no_safe_supporting_evidence",
                    }
                )
                continue
            accepted.append(row)
        # Finish the current evidence batch before stopping. This gives the final
        # post-gate MMR a real backed pool to diversify instead of exactly N rows.
    return {
        "eligible_with_evidence": accepted,
        "evidence_rejected": rejected,
        "batches_processed": batches_processed,
        "examined_count": examined_count,
        "exhausted": examined_count >= len(ranked_candidates),
    }


def _claim_entity_allowed(row: dict[str, Any]) -> bool:
    entity_type = str(row.get("entity_type") or "")
    entity_id = str(row.get("entity_id") or "")
    if not entity_type:
        if entity_id.startswith("SVC_"):
            entity_type = "service"
        elif entity_id.startswith("NODE_"):
            entity_type = "transport_node"
    return entity_type not in {"service", "transport_node"}


def aspect_coverage_status(
    candidate: dict[str, Any],
    requested_aspects: Iterable[str],
    *,
    live_item: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Reports fact/evidence/live coverage for every requested aspect."""

    requested = [str(value) for value in requested_aspects if value]
    metadata = candidate.get("metadata") or {}
    evidence = candidate.get("evidence") or {}
    evidence_rows = [
        row
        for group in ("supporting", "caveats")
        for row in evidence.get(group) or []
        if isinstance(row, dict)
        and not row.get("is_suspected_ad")
        and _claim_entity_allowed(row)
        and not contains_prompt_injection(row.get("claim") or row.get("key_quote"))
    ]
    evidence_aspects = {str(row.get("aspect") or "") for row in evidence_rows}
    live = live_item or {}
    web = live.get("web_verification") or {}
    recent_available = bool(web.get("available") and web.get("recent_crowd_and_trend_sources"))
    season_available = bool(web.get("available") and web.get("best_season_sources"))
    status: dict[str, str] = {}
    for aspect in requested:
        if aspect == "transport" and (
            candidate.get("travel_options") or metadata.get("transport")
        ):
            status[aspect] = "fact_layer_available"
        elif aspect == "cost" and metadata.get("budget_typical") is not None:
            status[aspect] = "fact_layer_available"
        elif aspect == "weather_season" and (live.get("weather") or {}).get("available"):
            status[aspect] = "fact_layer_available"
        elif aspect == "weather_season" and season_available:
            status[aspect] = "low_confidence_recent_verification"
        elif aspect in {"crowd", "commercialization"} and aspect in evidence_aspects:
            status[aspect] = "supported"
        elif aspect in {"crowd", "commercialization"} and recent_available:
            status[aspect] = "low_confidence_recent_verification"
        elif aspect in evidence_aspects:
            status[aspect] = "supported"
        else:
            status[aspect] = "insufficient"
    return status


def prioritize_caveats(
    rows: Iterable[dict[str, Any]],
    requested_aspects: Iterable[str],
) -> list[dict[str, Any]]:
    """Prioritize user-requested limitations before high-value general caveats."""

    requested_order = {
        str(value): index
        for index, value in enumerate(dict.fromkeys(str(value) for value in requested_aspects if value))
    }
    fallback_aspects = set(GENERAL_CAVEAT_ASPECT_PRIORITY)

    def numeric(row: dict[str, Any], key: str) -> float:
        try:
            return float(row.get(key) or 0.0)
        except (TypeError, ValueError):
            return 0.0

    def key(row: dict[str, Any]) -> tuple[Any, ...]:
        aspect = str(row.get("aspect") or "")
        if aspect in requested_order:
            aspect_priority = (0, requested_order[aspect])
        elif aspect in fallback_aspects:
            aspect_priority = (1, 0)
        else:
            aspect_priority = (2, 0)
        polarity_priority = 0 if row.get("polarity") == "negative" else 1
        return (
            *aspect_priority,
            polarity_priority,
            -numeric(row, "rerank_score"),
            -numeric(row, "source_quality"),
            str(row.get("claim_id") or row.get("evidence_id") or ""),
        )

    allowed = [
        dict(row)
        for row in rows
        if isinstance(row, dict)
        and not row.get("is_suspected_ad")
        and _claim_entity_allowed(row)
        and not contains_prompt_injection(row.get("claim") or row.get("key_quote"))
    ]
    return sorted(allowed, key=key)
