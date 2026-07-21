from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Protocol

import requests
from dotenv import dotenv_values

from inspitrip.paths import DEFAULT_ENV_PATH

ENV_VALUES = dotenv_values(DEFAULT_ENV_PATH)


class EvidenceReranker(Protocol):
    def rerank(
        self,
        query: str,
        rows: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], str]: ...


def _fallback_sort(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        (dict(row) for row in rows),
        key=lambda row: (
            -float(row.get("query_match_score") or 0),
            -float(row.get("source_quality") or 0),
            str(row.get("publish_date") or ""),
            str(row.get("claim_id") or ""),
        ),
    )


@dataclass(frozen=True)
class XinferenceClaimReranker:
    """Rerank UGC claims through the local Xinference BGE endpoint.

    Missing configuration, timeouts and malformed responses are deliberately
    non-fatal. Callers still receive deterministic structured/source-quality
    ordering and a status suitable for transparent runtime diagnostics.
    """

    base_url: str
    model: str
    timeout_seconds: float = 5.0

    @classmethod
    def from_env(cls) -> "XinferenceClaimReranker":
        base_url = str(
            ENV_VALUES.get("XINFERENCE_BASE_URL")
            or ENV_VALUES.get("OPENAI_BASE_URL")
            or ""
        ).strip()
        model = str(ENV_VALUES.get("XINFERENCE_RERANK_MODEL") or "").strip()
        try:
            timeout = float(ENV_VALUES.get("CLAIM_RERANK_TIMEOUT_SECONDS") or "5")
        except ValueError:
            timeout = 5.0
        return cls(
            base_url=base_url,
            model=model,
            timeout_seconds=min(30.0, max(0.1, timeout)),
        )

    def rerank(
        self,
        query: str,
        rows: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], str]:
        if not rows:
            return _fallback_sort(rows), "not_needed"
        if not self.base_url or not self.model or not query.strip():
            return _fallback_sort(rows), "fallback_unconfigured"

        documents = [
            str(row.get("claim") or row.get("key_quote") or "").strip()
            for row in rows
        ]
        if any(not document for document in documents):
            return _fallback_sort(rows), "fallback_invalid_claim"

        try:
            response = requests.post(
                f"{self.base_url.rstrip('/')}/rerank",
                json={
                    "model": self.model,
                    "query": query,
                    "documents": documents,
                },
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            result_rows = response.json().get("results")
            if not isinstance(result_rows, list):
                raise ValueError("rerank response has no results list")

            scores: dict[int, float] = {}
            for item in result_rows:
                index = int(item["index"])
                if index < 0 or index >= len(rows):
                    raise ValueError("rerank response index out of range")
                score = item.get("relevance_score", item.get("score"))
                parsed_score = float(score)
                if not math.isfinite(parsed_score):
                    raise ValueError("rerank response score is not finite")
                scores[index] = parsed_score
            if len(scores) != len(rows):
                raise ValueError("rerank response does not cover every claim")

            ranked: list[dict[str, Any]] = []
            for index, row in enumerate(rows):
                enriched = dict(row)
                enriched["rerank_score"] = scores[index]
                ranked.append(enriched)
            ranked.sort(
                key=lambda row: (
                    -float(row.get("rerank_score") or 0),
                    -float(row.get("query_match_score") or 0),
                    -float(row.get("source_quality") or 0),
                    str(row.get("claim_id") or ""),
                )
            )
            return ranked, "xinference_bge"
        except (requests.RequestException, TypeError, ValueError, KeyError):
            return _fallback_sort(rows), "fallback_error"
