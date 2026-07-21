from __future__ import annotations

from typing import Any

from dotenv import dotenv_values

from inspitrip.paths import DEFAULT_ENV_PATH, SCHEMA_DIR
from inspitrip.recommendation.claim_reranker import EvidenceReranker, XinferenceClaimReranker
from inspitrip.recommendation.query_plan import normalize_query_plan
from inspitrip.recommendation.ranking_extensions import (
    annotate_diversity_buckets,
    prioritize_caveats,
    refill_evidence_candidates,
)
from inspitrip.recommendation.ranking import (
    filter_and_rank,
    load_weights,
    mmr_select,
    travel_row_supports_mode,
)
from inspitrip.recommendation.repository import RecommendationRepository


SUPPORT_POOL_PER_DESTINATION = 8
CAVEAT_POOL_PER_DESTINATION = 4
SUPPORT_LIMIT_PER_DESTINATION = 2
CAVEAT_LIMIT_PER_DESTINATION = 1
ENV_VALUES = dotenv_values(DEFAULT_ENV_PATH)


def _minimum_claim_rerank_score() -> float:
    try:
        value = float(ENV_VALUES.get("CLAIM_RERANK_MIN_SCORE") or "0.1")
    except ValueError:
        value = 0.1
    return max(0.0, min(1.0, value))


def _doc_metadata(item: dict[str, Any]) -> dict[str, Any]:
    metadata = dict(item.get("metadata") or {})
    nested = metadata.get("doc_metadata")
    if isinstance(nested, dict):
        metadata.update(nested)
    return metadata


def candidate_ids(items: list[dict[str, Any]]) -> list[str]:
    ids: list[str] = []
    for item in items:
        metadata = _doc_metadata(item)
        destination_id = str(
            item.get("destination_id")
            or metadata.get("destination_id")
            or metadata.get("entity_id")
            or ""
        )
        if destination_id and destination_id not in ids:
            ids.append(destination_id)
    return ids


def hydrate_candidates(
    retrieval_items: list[dict[str, Any]],
    repository: RecommendationRepository,
    *,
    include_active_inventory: bool = False,
) -> list[dict[str, Any]]:
    ids = candidate_ids(retrieval_items)
    active_profiles: list[dict[str, Any]] = []
    if include_active_inventory:
        loader = getattr(repository, "get_active_profiles", None)
        if callable(loader):
            active_profiles = list(loader())
    profile_by_id = {
        str(row["destination_id"]): row
        for row in [*repository.get_profiles(ids), *active_profiles]
        if row.get("destination_id")
    }
    hydrated: list[dict[str, Any]] = []
    hydrated_ids: set[str] = set()
    for item in retrieval_items:
        metadata = _doc_metadata(item)
        destination_id = str(
            item.get("destination_id")
            or metadata.get("destination_id")
            or metadata.get("entity_id")
            or ""
        )
        profile = profile_by_id.get(destination_id)
        if not profile or destination_id in hydrated_ids:
            continue
        candidate = dict(profile)
        score = metadata.get("score", item.get("score"))
        if score is not None:
            candidate["semantic_match"] = score
        candidate["recall_source"] = "dify_retrieval"
        hydrated.append(candidate)
        hydrated_ids.add(destination_id)
    for profile in active_profiles:
        destination_id = str(profile.get("destination_id") or "")
        if not destination_id or destination_id in hydrated_ids:
            continue
        candidate = dict(profile)
        candidate["semantic_match"] = 0.0
        candidate["recall_source"] = "active_inventory"
        hydrated.append(candidate)
        hydrated_ids.add(destination_id)
    return hydrated


def _preferred_query_tags(query_plan: dict[str, Any]) -> set[str]:
    soft = query_plan.get("soft_preferences") or {}
    tags = {
        str(item.get("id") or "")
        for dimension in ("mood", "vibe", "activity")
        for item in soft.get(dimension) or []
        if item.get("id")
    }
    tags.update(
        str(value)
        for value in (query_plan.get("hard_constraints") or {}).get("must_have_activities") or []
        if value
    )
    return tags


def _claim_entity_type(row: dict[str, Any]) -> str:
    entity_type = str(row.get("entity_type") or "")
    if entity_type:
        return entity_type
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


def _query_match_score(
    row: dict[str, Any],
    *,
    aspects: set[str],
    tags: set[str],
) -> float:
    claim_tags = (
        set(row.get("mood") or [])
        | set(row.get("vibe") or [])
        | set(row.get("activity") or [])
    )
    tag_matches = len(claim_tags & tags)
    aspect_match = bool(str(row.get("aspect") or "") in aspects)
    return float(tag_matches * 2 + int(aspect_match))


def _flatten(grouped: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    return [row for rows in grouped.values() for row in rows]


def _group_by_destination(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        destination_id = str(row.get("destination_id") or "")
        if destination_id:
            result.setdefault(destination_id, []).append(row)
    return result


def _take_independent_authors(
    rows: list[dict[str, Any]],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    authors: set[str] = set()
    for row in rows:
        author = str(
            row.get("author_hash")
            or row.get("note_id")
            or row.get("evidence_id")
            or row.get("claim_id")
            or ""
        )
        if author in authors:
            continue
        authors.add(author)
        selected.append(row)
        if len(selected) >= limit:
            break
    return selected


def _attach_travel_options(
    selected: list[dict[str, Any]],
    travel_rows: list[dict[str, Any]],
    query_plan: dict[str, Any],
) -> list[dict[str, Any]]:
    hard = query_plan.get("hard_constraints") or {}
    origin = str(hard.get("origin") or "")
    requested_modes = set(hard.get("transport_modes") or [])
    by_destination: dict[str, list[dict[str, Any]]] = {}
    if origin:
        for row in travel_rows:
            if str(row.get("origin_city") or "") != origin:
                continue
            if not requested_modes and str(row.get("transport_mode") or "") not in {"自驾", "公共交通"}:
                continue
            if requested_modes:
                confirmed_match = any(
                    travel_row_supports_mode(row, mode) is True
                    for mode in requested_modes
                )
                broad_failure_match = any(
                    (mode == "自驾" and row.get("transport_mode") == "自驾")
                    or (mode == "公共交通" and row.get("transport_mode") == "公共交通")
                    for mode in requested_modes
                )
                if not confirmed_match and not broad_failure_match:
                    continue
            by_destination.setdefault(str(row.get("destination_id") or ""), []).append(row)
    result = []
    for candidate in selected:
        enriched = dict(candidate)
        rows = by_destination.get(str(candidate.get("destination_id") or ""), [])
        enriched["travel_options"] = sorted(
            (dict(row) for row in rows),
            key=lambda row: (
                row.get("travel_minutes") is None,
                str(row.get("transport_mode") or ""),
            ),
        )
        result.append(enriched)
    return result


def attach_evidence(
    selected: list[dict[str, Any]],
    query_plan: dict[str, Any],
    repository: RecommendationRepository,
    *,
    raw_query: str = "",
    reranker: EvidenceReranker | None = None,
) -> list[dict[str, Any]]:
    ids = [row["destination_id"] for row in selected]
    if not ids:
        return []

    aspects = {
        str(value)
        for value in query_plan.get("evidence_aspects") or []
        if value
    }
    preferred_tags = _preferred_query_tags(query_plan)
    discovery_types = (
        ("destination", "experience")
        if query_plan.get("task_type") == "destination_discovery"
        else ()
    )

    supporting = repository.get_claims(
        ids,
        sorted(aspects),
        per_destination=SUPPORT_POOL_PER_DESTINATION,
        polarities=("positive",),
        entity_types=discovery_types,
        tag_ids=tuple(sorted(preferred_tags)),
    )
    caveats = repository.get_claims(
        ids,
        sorted(aspects),
        per_destination=CAVEAT_POOL_PER_DESTINATION,
        polarities=("negative", "mixed"),
        entity_types=discovery_types,
        tag_ids=(),
    )
    missing_caveat_ids = [destination_id for destination_id in ids if not caveats.get(destination_id)]
    if missing_caveat_ids:
        fallback_caveats = repository.get_claims(
            missing_caveat_ids,
            [],
            per_destination=CAVEAT_POOL_PER_DESTINATION,
            polarities=("negative", "mixed"),
            entity_types=discovery_types,
            tag_ids=(),
        )
        for destination_id in missing_caveat_ids:
            caveats[destination_id] = fallback_caveats.get(destination_id, [])

    allowed_types = set(discovery_types)
    has_query_signals = bool(aspects or preferred_tags)
    support_rows: list[dict[str, Any]] = []
    for row in _flatten(supporting):
        if row.get("polarity") != "positive" or row.get("is_suspected_ad"):
            continue
        if allowed_types and _claim_entity_type(row) not in allowed_types:
            continue
        match_score = _query_match_score(row, aspects=aspects, tags=preferred_tags)
        if has_query_signals and match_score <= 0:
            continue
        enriched = dict(row)
        enriched["query_match_score"] = match_score
        support_rows.append(enriched)

    caveat_rows = [
        dict(row)
        for row in _flatten(caveats)
        if row.get("polarity") in {"negative", "mixed"}
        and not row.get("is_suspected_ad")
        and (not allowed_types or _claim_entity_type(row) in allowed_types)
    ]
    claim_reranker = reranker or XinferenceClaimReranker.from_env()
    rerank_query = raw_query.strip() or str(query_plan.get("semantic_query") or "").strip()
    ranked_support, support_rerank_status = claim_reranker.rerank(
        rerank_query,
        support_rows,
    )
    ranked_caveats, caveat_rerank_status = claim_reranker.rerank(
        rerank_query,
        caveat_rows,
    )
    ranked_caveats = prioritize_caveats(
        ranked_caveats,
        query_plan.get("evidence_aspects") or [],
    )
    support_destinations_before_rerank_gate = {
        str(row.get("destination_id") or "") for row in ranked_support
    }
    if support_rerank_status == "xinference_bge":
        minimum_score = _minimum_claim_rerank_score()
        ranked_support = [
            row
            for row in ranked_support
            if float(row.get("rerank_score") or 0) >= minimum_score
        ]
    support_destinations_after_rerank_gate = {
        str(row.get("destination_id") or "") for row in ranked_support
    }
    rerank_rejected_destinations = (
        support_destinations_before_rerank_gate - support_destinations_after_rerank_gate
    )
    support_by_destination = _group_by_destination(ranked_support)
    caveat_by_destination = _group_by_destination(ranked_caveats)

    result = []
    for candidate in selected:
        destination_id = candidate["destination_id"]
        positive = _take_independent_authors(
            support_by_destination.get(destination_id, []),
            limit=SUPPORT_LIMIT_PER_DESTINATION,
        )
        selected_caveats = _take_independent_authors(
            caveat_by_destination.get(destination_id, []),
            limit=CAVEAT_LIMIT_PER_DESTINATION,
        )
        enriched = dict(candidate)
        enriched["evidence"] = {
            "supporting": positive,
            "caveats": selected_caveats,
            "evidence_ids": [
                row.get("claim_id")
                for row in positive + selected_caveats
                if row.get("claim_id")
            ],
            "supporting_rerank": support_rerank_status,
            "caveat_rerank": caveat_rerank_status,
        }
        enriched["evidence_gap"] = not bool(positive)
        if enriched["evidence_gap"]:
            enriched["evidence_gap_reason"] = (
                "claim_rerank_below_threshold"
                if destination_id in rerank_rejected_destinations
                else "no_query_matched_supporting_claim"
            )
        result.append(enriched)
    return result


def rank_retrieval_items(
    *,
    raw_query: str,
    query_plan_payload: dict[str, Any],
    retrieval_items: list[dict[str, Any]],
    repository: RecommendationRepository,
    allow_unknown_hard_facts: bool = True,
    top_n: int = 10,
    final_limit: int = 5,
    evidence_reranker: EvidenceReranker | None = None,
) -> dict[str, Any]:
    query_plan = normalize_query_plan(
        query_plan_payload,
        raw_query=raw_query,
        schema_path=SCHEMA_DIR / "query_plan_schema.json",
    )
    include_active_inventory = query_plan.get("task_type") == "destination_discovery"
    candidates = hydrate_candidates(
        retrieval_items,
        repository,
        include_active_inventory=include_active_inventory,
    )
    travel_rows = repository.get_travel_rows(
        [row["destination_id"] for row in candidates]
    )
    ranked = filter_and_rank(
        candidates,
        query_plan,
        travel_rows=travel_rows,
        allow_unknown_hard_facts=allow_unknown_hard_facts,
        top_n=top_n,
        final_limit=final_limit,
        apply_mmr=False,
    )
    ranked["eligible"] = annotate_diversity_buckets(ranked["eligible"])

    def enrich_batch(batch: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return attach_evidence(
            batch,
            query_plan,
            repository,
            raw_query=raw_query,
            reranker=evidence_reranker,
        )

    refill = refill_evidence_candidates(
        ranked["eligible"],
        enrich_batch,
        final_limit=final_limit,
        batch_size=max(20, top_n),
    )
    backed = refill["eligible_with_evidence"]
    rejected_details = refill["evidence_rejected"]
    ranked["evidence_rejected"] = [row.get("destination_id") for row in rejected_details]
    ranked["evidence_rejected_details"] = rejected_details
    ranked["evidence_gaps"] = [
        {"destination_id": row.get("destination_id"), "reason": row.get("reason")}
        for row in rejected_details
    ]
    ranked["evidence_refill"] = {
        "batches_processed": refill["batches_processed"],
        "examined_count": refill["examined_count"],
        "exhausted": refill["exhausted"],
    }
    selected_with_evidence = mmr_select(
        backed,
        limit=final_limit,
        lambda_value=float(load_weights().get("mmr_lambda", 0.75)),
    )
    ranked["selected"] = _attach_travel_options(
        selected_with_evidence,
        travel_rows,
        query_plan,
    )
    ranked["query_plan"] = query_plan
    ranked["retrieval_count"] = len(retrieval_items)
    ranked["hydrated_count"] = len(candidates)
    retrieval_unique_count = len(candidate_ids(retrieval_items))
    retrieval_hydrated_count = sum(
        row.get("recall_source") == "dify_retrieval" for row in candidates
    )
    supplemented_count = sum(
        row.get("recall_source") == "active_inventory" for row in candidates
    )
    active_inventory_count = sum(
        row.get("status") == "active" for row in candidates
    )
    parsed_item_count = sum(bool(candidate_ids([item])) for item in retrieval_items)
    ranked["candidate_pool_diagnostics"] = {
        "version": "p3-v1",
        "raw_retrieval_item_count": len(retrieval_items),
        "retrieval_unique_count": retrieval_unique_count,
        "metadata_unparsed_count": len(retrieval_items) - parsed_item_count,
        "retrieval_hydrated_count": retrieval_hydrated_count,
        "active_inventory_count": active_inventory_count,
        "inventory_supplemented_count": supplemented_count,
        "eligible_after_hard_filter_count": len(ranked.get("eligible") or []),
    }
    return ranked
