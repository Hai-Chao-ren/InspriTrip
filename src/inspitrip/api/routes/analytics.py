from pathlib import Path

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from inspitrip.analytics import AnalyticsStore, assign_variant
from inspitrip.api.models import (
    AnalyticsBatchRequest,
    ExperimentAssignmentRequest,
    RecommendationFeedbackRequest,
)
from inspitrip.paths import REPO_ROOT


router = APIRouter(prefix="/api", tags=["analytics"])
_store: AnalyticsStore | None = None


def get_store() -> AnalyticsStore:
    global _store
    if _store is None:
        _store = AnalyticsStore(Path(REPO_ROOT / "data" / "analytics.sqlite3"))
    return _store


@router.post("/experiments/assign")
def experiment_assignment(request: ExperimentAssignmentRequest):
    try:
        variant = assign_variant(request.anonymous_user_id, request.experiment_id)
    except ValueError as exc:
        return JSONResponse(status_code=404, content={"ok": False, "error": str(exc)})
    return {"ok": True, "experiment_id": request.experiment_id, "variant": variant}


@router.post("/analytics/events")
def collect_events(request: AnalyticsBatchRequest):
    try:
        accepted = get_store().record([event.model_dump() for event in request.events])
    except ValueError as exc:
        return JSONResponse(status_code=422, content={"ok": False, "error": str(exc)})
    return {"ok": True, "accepted": accepted}


@router.get("/analytics/summary")
def analytics_summary(scope: str = Query("production", pattern="^(production|demo|all)$")):
    return {"ok": True, **get_store().summary(scope=scope)}


@router.post("/feedback/recommendation")
def recommendation_feedback(request: RecommendationFeedbackRequest):
    try:
        get_store().record_feedback(request.model_dump())
    except ValueError as exc:
        return JSONResponse(status_code=422, content={"ok": False, "error": str(exc)})
    return {"ok": True, "saved": True}
