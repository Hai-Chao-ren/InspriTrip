from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class FrontendChatRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    origin: str | None = Field(default=None, max_length=40)
    budget: int | None = Field(default=None, ge=0, le=1_000_000)
    days: int | None = Field(default=None, ge=1, le=365)
    conversation_id: str = Field(default="", max_length=128)
    user: str = Field(default="inspitrip-web", min_length=1, max_length=128)


class ResolveQueryPlanRequest(BaseModel):
    raw_query: str
    planner_output: Any = None
    form_values: dict[str, Any] = Field(default_factory=dict)
    conversation_id: str = ""


class RankCandidatesRequest(BaseModel):
    raw_query: str
    query_plan: dict[str, Any]
    retrieval_items: list[dict[str, Any]] = Field(default_factory=list)
    allow_unknown_hard_facts: bool = True
    top_n: int = Field(default=10, ge=1, le=50)
    final_limit: int = Field(default=5, ge=1, le=5)


class ValidateOutputRequest(BaseModel):
    llm_output: Any = None
    selected: list[dict[str, Any]] = Field(default_factory=list)
    live_context: dict[str, Any] = Field(default_factory=dict)


class ReverseLocationRequest(BaseModel):
    longitude: float = Field(ge=73, le=135)
    latitude: float = Field(ge=3, le=54)


class AnalyticsEventRequest(BaseModel):
    event_id: str = Field(min_length=1, max_length=80)
    event_name: str = Field(min_length=1, max_length=80)
    event_time: str = Field(default="", max_length=64)
    anonymous_user_id: str = Field(min_length=1, max_length=128)
    session_id: str = Field(min_length=1, max_length=128)
    conversation_id: str = Field(default="", max_length=128)
    request_id: str = Field(default="", max_length=128)
    experiment_id: str = Field(default="", max_length=80)
    variant: str = Field(default="", max_length=80)
    page: str = Field(default="", max_length=32)
    properties: dict[str, Any] = Field(default_factory=dict)


class AnalyticsBatchRequest(BaseModel):
    events: list[AnalyticsEventRequest] = Field(min_length=1, max_length=50)


class ExperimentAssignmentRequest(BaseModel):
    experiment_id: str = Field(min_length=1, max_length=80)
    anonymous_user_id: str = Field(min_length=1, max_length=128)


class RecommendationFeedbackRequest(BaseModel):
    anonymous_user_id: str = Field(min_length=1, max_length=128)
    session_id: str = Field(min_length=1, max_length=128)
    conversation_id: str = Field(default="", max_length=128)
    request_id: str = Field(min_length=1, max_length=128)
    destination_key: str = Field(min_length=1, max_length=128)
    feedback: str = Field(min_length=1, max_length=32)
    reason_code: str = Field(default="", max_length=80)
    experiment_id: str = Field(default="", max_length=80)
    variant: str = Field(default="", max_length=80)
    is_demo: bool = False
