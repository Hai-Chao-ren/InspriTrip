from __future__ import annotations

import json
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Callable

from .query_plan import (
    build_rule_query_delta,
    parse_query_plan_output,
    should_enter_retrieval,
)
from .query_state import decide_clarification, merge_query_plan


@dataclass
class QuerySessionState:
    plan: dict[str, Any]
    clarification_count: int
    updated_at: float


class QueryStateStore:
    """Small bounded MVP store for one-turn clarification state.

    Dify supplies its conversation id. Production deployments with multiple API
    replicas can replace this store with Redis without changing the resolver
    contract.
    """

    def __init__(
        self,
        *,
        max_entries: int = 1024,
        ttl_seconds: int = 6 * 60 * 60,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_entries = max(1, int(max_entries))
        self.ttl_seconds = max(1, int(ttl_seconds))
        self.clock = clock
        self._states: OrderedDict[str, QuerySessionState] = OrderedDict()
        self._lock = threading.Lock()

    def _copy(self, value: Any) -> Any:
        return json.loads(json.dumps(value, ensure_ascii=False))

    def get(self, conversation_id: str) -> QuerySessionState | None:
        key = str(conversation_id or "").strip()
        if not key:
            return None
        now = self.clock()
        with self._lock:
            state = self._states.get(key)
            if state is None:
                return None
            if state.updated_at + self.ttl_seconds <= now:
                self._states.pop(key, None)
                return None
            self._states.move_to_end(key)
            return QuerySessionState(
                plan=self._copy(state.plan),
                clarification_count=state.clarification_count,
                updated_at=state.updated_at,
            )

    def set(self, conversation_id: str, plan: dict[str, Any], clarification_count: int) -> None:
        key = str(conversation_id or "").strip()
        if not key:
            return
        with self._lock:
            self._states.pop(key, None)
            self._states[key] = QuerySessionState(
                plan=self._copy(plan),
                clarification_count=max(0, int(clarification_count or 0)),
                updated_at=self.clock(),
            )
            while len(self._states) > self.max_entries:
                self._states.popitem(last=False)

    def clear(self, conversation_id: str) -> None:
        with self._lock:
            self._states.pop(str(conversation_id or "").strip(), None)

    def __len__(self) -> int:
        with self._lock:
            return len(self._states)


def resolve_query_turn(
    *,
    raw_query: str,
    planner_output: Any,
    form_values: dict[str, Any] | None = None,
    conversation_id: str = "",
    store: QueryStateStore | None = None,
) -> dict[str, Any]:
    """Validate one planner turn, merge state and decide one clarification."""
    raw_query = str(raw_query or "").strip()
    form_values = dict(form_values or {})
    current_plan = parse_query_plan_output(
        planner_output,
        raw_query=raw_query,
        form_values=form_values,
    )
    delta = build_rule_query_delta(raw_query, form_values=form_values)
    delta["query_plan"] = current_plan

    state = store.get(conversation_id) if store is not None else None
    if state is None:
        merged = current_plan
        clarification_count = 0
    else:
        merged = merge_query_plan(state.plan, delta, form_values=form_values)
        clarification_count = state.clarification_count

    clarification = decide_clarification(merged, clarification_count)
    if clarification.get("should_clarify") and clarification_count < 1:
        clarification_count = 1
    if store is not None:
        store.set(conversation_id, merged, clarification_count)
    return {
        "query_plan": merged,
        "clarification": clarification,
        "clarification_count": clarification_count,
        "enter_retrieval": should_enter_retrieval(merged)
        and not bool(clarification.get("should_clarify")),
        "stateful": bool(str(conversation_id or "").strip()),
    }
