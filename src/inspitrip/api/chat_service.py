from __future__ import annotations

import uuid
import re
from typing import Any

import requests

from inspitrip.analytics import stable_destination_key
from inspitrip.api.models import FrontendChatRequest
from inspitrip.api.settings import RuntimeSettings
from inspitrip.paths import DEMO_DATA_DIR
from inspitrip.recommendation.query_plan import build_rule_query_plan
from inspitrip.recommendation.repository import JsonlRecommendationRepository
from inspitrip.recommendation.service import rank_retrieval_items


_CARD_HEADER_RE = re.compile(r"^\s*\d+[.、]\s*(.+?)(?:（([^）]*)）)?\s*$")
_EMPTY_MARKERS = (
    "知识检索没有召回目的地",
    "召回目的地均不满足当前硬条件",
    "没有通过准入的匹配证据",
    "当前没有可安全输出的推荐结果",
)


def _display_name(value: Any) -> str:
    return str(value or "").replace("示例·", "").strip()


def _budget_text(metadata: dict[str, Any]) -> str:
    typical = metadata.get("budget_typical")
    minimum = metadata.get("budget_min")
    maximum = metadata.get("budget_max")
    if typical is not None:
        return f"人均约 ¥{int(typical)}"
    if minimum is not None and maximum is not None:
        return f"人均 ¥{int(minimum)}–{int(maximum)}"
    return "预算仍需核实"


def _duration_text(metadata: dict[str, Any]) -> str:
    minimum = metadata.get("duration_min")
    maximum = metadata.get("duration_max")
    if minimum is None:
        return "建议天数仍需核实"
    if maximum in (None, minimum):
        return f"建议 {int(minimum)} 天"
    return f"建议 {int(minimum)}–{int(maximum)} 天"


def _card(row: dict[str, Any]) -> dict[str, Any]:
    metadata = dict(row.get("metadata") or {})
    evidence = dict(row.get("evidence") or {})
    supporting = list(evidence.get("supporting") or [])
    caveats = list(evidence.get("caveats") or [])
    name = _display_name(row.get("name"))
    details = [
        {"label": "预算", "text": _budget_text(metadata), "type": "fact"},
        {"label": "时间", "text": _duration_text(metadata), "type": "fact"},
    ]
    if supporting:
        details.append(
            {
                "label": "合成证据",
                "text": str(supporting[0].get("claim") or "").strip(),
                "type": "context",
            }
        )
    if caveats:
        details.append(
            {
                "label": "透明限制",
                "text": str(caveats[0].get("claim") or "").strip(),
                "type": "warning",
            }
        )
    return {
        "destination_id": row.get("destination_id"),
        "destination_key": stable_destination_key(name, str(row.get("city") or "")),
        "name": name,
        "city": row.get("city") or "",
        "reason": row.get("core_feeling") or row.get("atmosphere") or "与当前感觉偏好匹配。",
        "score": round(float(row.get("final_score") or 0), 4),
        "details": details,
        "synthetic": True,
    }


def demo_chat(request: FrontendChatRequest) -> dict[str, Any]:
    query = request.query.strip()
    conversation_id = request.conversation_id.strip() or f"demo-{uuid.uuid4().hex[:16]}"
    if not request.origin:
        return {
            "ok": True,
            "kind": "message",
            "answer": "先告诉我从哪座城市出发，我才能判断两天内是否真的可达。",
            "recommendations": [],
            "conversation_id": conversation_id,
            "message_id": f"msg-{uuid.uuid4().hex[:12]}",
            "needs_clarification": {"field": "origin", "label": "出发城市"},
            "demo": True,
        }
    plan = build_rule_query_plan(
        query,
        form_values={"origin": request.origin, "budget": request.budget, "days": request.days},
    )
    ranked = rank_retrieval_items(
        raw_query=query,
        query_plan_payload=plan,
        retrieval_items=[],
        repository=JsonlRecommendationRepository(DEMO_DATA_DIR),
        allow_unknown_hard_facts=True,
        top_n=10,
        final_limit=3,
    )
    cards = [_card(row) for row in ranked.get("selected") or []]
    if cards:
        answer = "我用合成目的地数据跑完了意图解析、硬约束过滤、排序和证据门控。"
        kind = "recommendations"
    else:
        answer = "当前合成样例没有满足全部条件的结果。可以放宽预算、天数或交通要求。"
        kind = "empty"
    return {
        "ok": True,
        "kind": kind,
        "answer": answer,
        "recommendations": cards,
        "conversation_id": conversation_id,
        "message_id": f"msg-{uuid.uuid4().hex[:12]}",
        "query_plan": ranked.get("query_plan") or plan,
        "diagnostics": ranked.get("candidate_pool_diagnostics") or {},
        "demo": True,
    }


def _parse_dify_recommendations(answer: str) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for raw_line in answer.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = _CARD_HEADER_RE.match(line)
        if match:
            current = {
                "name": match.group(1).strip().strip("*"),
                "city": (match.group(2) or "").strip(),
                "reason": "",
                "details": [],
                "synthetic": False,
            }
            cards.append(current)
            continue
        if current is None or not line.startswith(("-", "•")):
            continue
        detail = line[1:].strip()
        label, separator, value = detail.partition("：")
        if not separator:
            label, separator, value = detail.partition(":")
        if not separator:
            label, value = "核实信息", detail
        label = label.strip().strip("*")
        value = value.strip().strip("*")
        if label == "感觉理由":
            current["reason"] = value
            continue
        detail_type = "warning" if label in {"透明降级", "UGC 限制"} else "fact"
        if label.startswith("实时天气") or "核验" in label:
            detail_type = "context"
        current["details"].append({"label": label, "text": value, "type": detail_type})
    for card in cards:
        card["destination_key"] = stable_destination_key(card["name"], card["city"])
    return [card for card in cards if card.get("name")]


def full_chat(request: FrontendChatRequest, settings: RuntimeSettings) -> tuple[int, dict[str, Any]]:
    if not settings.dify_api_base or not settings.dify_api_key:
        return 503, {
            "ok": False,
            "error": "完整模式尚未配置 Dify。请填写 .env，或切回 INSPITRIP_MODE=demo。",
        }
    try:
        response = requests.post(
            f"{settings.dify_api_base}/chat-messages",
            headers={
                "Authorization": f"Bearer {settings.dify_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "inputs": {"origin": request.origin, "budget": request.budget, "days": request.days},
                "query": request.query.strip(),
                "response_mode": "blocking",
                "conversation_id": request.conversation_id.strip(),
                "user": request.user.strip(),
            },
            timeout=90,
        )
    except requests.Timeout:
        return 504, {"ok": False, "error": "推荐生成超时，请稍后重试。", "retryable": True}
    except requests.RequestException:
        return 502, {"ok": False, "error": "暂时无法连接推荐服务。", "retryable": True}
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    if response.status_code != 200:
        return 502, {"ok": False, "error": "推荐服务暂时不可用。", "retryable": True}
    answer = str(payload.get("answer") or "").strip()
    recommendations = _parse_dify_recommendations(answer)
    kind = "recommendations" if recommendations else "empty" if any(marker in answer for marker in _EMPTY_MARKERS) else "message"
    return 200, {
        "ok": True,
        "kind": kind,
        "answer": answer,
        "recommendations": recommendations,
        "conversation_id": str(payload.get("conversation_id") or ""),
        "message_id": str(payload.get("message_id") or ""),
        "demo": False,
    }
