from fastapi import APIRouter
from fastapi.responses import JSONResponse

from inspitrip.api.models import (
    RankCandidatesRequest,
    ResolveQueryPlanRequest,
    ValidateOutputRequest,
)
from inspitrip.recommendation.output_fidelity import (
    build_verified_fact_cards,
    validate_and_repair_llm_output,
)
from inspitrip.recommendation.query_runtime import QueryStateStore, resolve_query_turn
from inspitrip.recommendation.repository import build_repository
from inspitrip.recommendation.service import rank_retrieval_items


router = APIRouter(prefix="/api/v2", tags=["recommendation"])
_query_state = QueryStateStore()


@router.post("/query_plan/resolve")
def resolve_query_plan(request: ResolveQueryPlanRequest):
    try:
        result = resolve_query_turn(
            raw_query=request.raw_query,
            planner_output=request.planner_output,
            form_values=request.form_values,
            conversation_id=request.conversation_id,
            store=_query_state,
        )
    except ValueError as exc:
        return JSONResponse(status_code=422, content={"ok": False, "error": str(exc)})
    return {"ok": True, **result}


@router.post("/rank_candidates")
def rank_candidates(request: RankCandidatesRequest):
    try:
        result = rank_retrieval_items(
            raw_query=request.raw_query,
            query_plan_payload=request.query_plan,
            retrieval_items=request.retrieval_items,
            repository=build_repository(),
            allow_unknown_hard_facts=request.allow_unknown_hard_facts,
            top_n=request.top_n,
            final_limit=request.final_limit,
        )
    except (ValueError, RuntimeError) as exc:
        return JSONResponse(status_code=422, content={"ok": False, "error": str(exc)})
    return {"ok": True, **result}


@router.post("/output/validate")
def validate_output(request: ValidateOutputRequest):
    validation = validate_and_repair_llm_output(request.llm_output, request.selected)
    return {
        "ok": True,
        "validation": validation,
        "fact_cards": build_verified_fact_cards(request.selected, request.live_context),
    }
