from __future__ import annotations

import json
import random
import re
import sys
import time
from dataclasses import dataclass
from typing import Any, Literal

from jsonschema import Draft7Validator
from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI


ApiStyle = Literal["responses", "chat"]
OutputMode = Literal["auto", "structured", "json"]


class ExtractionError(RuntimeError):
    """Raised when the provider cannot return a valid extraction."""


class RetryableExtractionError(ExtractionError):
    """A transient empty/non-JSON gateway response that should be retried."""


@dataclass
class ExtractionResult:
    data: dict[str, Any]
    model: str
    response_id: str
    usage: dict[str, Any]
    api_style: ApiStyle
    output_mode: str


def _strip_json_fence(text: str) -> str:
    text = text.strip()
    match = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.DOTALL)
    return match.group(1).strip() if match else text


def _validation_message(validator: Draft7Validator, data: Any) -> str:
    errors = sorted(validator.iter_errors(data), key=lambda error: list(error.path))
    if not errors:
        return ""
    parts = []
    for error in errors[:8]:
        path = ".".join(str(item) for item in error.absolute_path) or "$"
        parts.append(f"{path}: {error.message}")
    return "; ".join(parts)


def _usage_dict(usage: Any) -> dict[str, Any]:
    if usage is None:
        return {}
    if hasattr(usage, "model_dump"):
        return usage.model_dump(exclude_none=True)
    return dict(usage) if isinstance(usage, dict) else {}


def _content_parts_text(content: Any) -> tuple[str, str]:
    """Extract text/refusal from Responses-style content blocks."""
    if isinstance(content, str):
        return content, ""
    texts: list[str] = []
    refusal = ""
    for part in content or []:
        if isinstance(part, dict):
            part_type = part.get("type", "")
            if part_type in {"output_text", "text"} and isinstance(part.get("text"), str):
                texts.append(part["text"])
            elif part_type == "refusal":
                refusal = str(part.get("refusal") or part.get("text") or "")
        else:
            part_type = getattr(part, "type", "")
            text_value = getattr(part, "text", None)
            if part_type in {"output_text", "text"} and isinstance(text_value, str):
                texts.append(text_value)
            elif part_type == "refusal":
                refusal = str(
                    getattr(part, "refusal", None) or text_value or ""
                )
    return "".join(texts), refusal


def _mapping_response_payload(
    payload: dict[str, Any], fallback_model: str
) -> tuple[str, str, str, dict[str, Any]]:
    """Normalize an API envelope or a direct extraction object."""
    if payload.get("error"):
        raise ExtractionError(f"API 返回错误对象：{payload['error']}")

    # Some OpenAI-compatible providers return the model JSON directly instead
    # of a Responses API envelope.
    if "note_id" in payload and "mentions" in payload:
        return json.dumps(payload, ensure_ascii=False), "", fallback_model, {}

    output_text = payload.get("output_text")
    if isinstance(output_text, str) and output_text:
        return (
            output_text,
            str(payload.get("id") or ""),
            str(payload.get("model") or fallback_model),
            _usage_dict(payload.get("usage")),
        )

    texts: list[str] = []
    refusal = ""
    for item in payload.get("output") or []:
        if isinstance(item, dict):
            item_text, item_refusal = _content_parts_text(item.get("content"))
            if not item_text and item.get("type") in {"output_text", "text"}:
                item_text = str(item.get("text") or "")
        else:
            item_text, item_refusal = _content_parts_text(getattr(item, "content", None))
            if not item_text and getattr(item, "type", "") in {"output_text", "text"}:
                item_text = str(getattr(item, "text", "") or "")
        if item_text:
            texts.append(item_text)
        if item_refusal:
            refusal = item_refusal
    if texts:
        return (
            "".join(texts),
            str(payload.get("id") or ""),
            str(payload.get("model") or fallback_model),
            _usage_dict(payload.get("usage")),
        )
    if refusal:
        raise ExtractionError(f"模型拒绝：{refusal}")

    # A few compatible gateways wrap a Chat Completions envelope even on the
    # /responses route.
    choices = payload.get("choices") or []
    if choices:
        first = choices[0]
        message = first.get("message", {}) if isinstance(first, dict) else {}
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, str) and content:
            return (
                content,
                str(payload.get("id") or ""),
                str(payload.get("model") or fallback_model),
                _usage_dict(payload.get("usage")),
            )

    for wrapper_key in ("data", "response", "result"):
        wrapped = payload.get(wrapper_key)
        if isinstance(wrapped, dict):
            return _mapping_response_payload(wrapped, fallback_model)
        if isinstance(wrapped, str) and wrapped.strip():
            return _coerce_responses_response(wrapped, fallback_model)

    keys = ",".join(sorted(str(key) for key in payload.keys()))
    raise ExtractionError(f"无法从 Responses 字典提取文本；返回字段：{keys}")


def _coerce_responses_response(
    response: Any, fallback_model: str
) -> tuple[str, str, str, dict[str, Any]]:
    """Accept official SDK objects and looser OpenAI-compatible responses."""
    if isinstance(response, str):
        text = response.strip()
        if not text:
            raise RetryableExtractionError("Responses API 返回空字符串")
        nested: Any = text
        for _depth in range(2):
            if not isinstance(nested, str):
                break
            candidate = nested.strip()
            try:
                nested = json.loads(candidate)
            except json.JSONDecodeError:
                return candidate, "", fallback_model, {}
        if isinstance(nested, dict):
            return _mapping_response_payload(nested, fallback_model)
        if isinstance(nested, str):
            return nested, "", fallback_model, {}
        raise ExtractionError(
            f"Responses API 字符串解析后类型不受支持：{type(nested).__name__}"
        )

    if isinstance(response, dict):
        return _mapping_response_payload(response, fallback_model)

    try:
        output_text = getattr(response, "output_text", None)
    except (AttributeError, TypeError):
        output_text = None
    if isinstance(output_text, str) and output_text:
        return (
            output_text,
            str(getattr(response, "id", "") or ""),
            str(getattr(response, "model", fallback_model) or fallback_model),
            _usage_dict(getattr(response, "usage", None)),
        )

    if hasattr(response, "model_dump"):
        dumped = response.model_dump(exclude_none=True)
        if isinstance(dumped, dict):
            return _mapping_response_payload(dumped, fallback_model)

    response_type = type(response).__name__
    raise ExtractionError(f"无法解析 Responses 返回值类型：{response_type}")


def _structured_output_unsupported(exc: APIStatusError) -> bool:
    status = getattr(exc, "status_code", None)
    message = str(exc).lower()
    keywords = (
        "json_schema",
        "response_format",
        "text.format",
        "structured output",
        "unsupported parameter",
        "unknown parameter",
    )
    return status in {400, 404, 405, 422} and any(
        keyword in message for keyword in keywords
    )


def _retryable_status(exc: APIStatusError) -> bool:
    status = getattr(exc, "status_code", None)
    return status in {408, 409, 425, 429} or (isinstance(status, int) and status >= 500)


def _non_json_error(raw_text: str, exc: json.JSONDecodeError) -> RetryableExtractionError:
    preview = re.sub(r"\s+", " ", raw_text.strip())[:160]
    if not preview:
        preview = "<empty>"
    return RetryableExtractionError(
        f"API 返回非 JSON 内容：{preview!r}；解析错误：{exc.msg}"
    )


class OpenAIExtractor:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        schema: dict[str, Any],
        api_style: ApiStyle = "responses",
        output_mode: OutputMode = "auto",
        base_url: str | None = None,
        timeout: float = 120.0,
        max_output_tokens: int = 5000,
        reasoning_effort: str | None = None,
    ) -> None:
        # Retry only in this pipeline so SDK-internal retries do not multiply
        # the configured timeout/backoff during large batch runs.
        self.client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            max_retries=0,
        )
        self.model = model
        self.schema = schema
        self.api_style = api_style
        self.output_mode = output_mode
        self.max_output_tokens = max_output_tokens
        self.reasoning_effort = reasoning_effort
        self.validator = Draft7Validator(schema)

    def extract(
        self,
        *,
        instructions: str,
        user_input: str,
        retries: int = 4,
        retry_base_delay: float = 5.0,
        retry_max_delay: float = 60.0,
    ) -> ExtractionResult:
        feedback = ""
        last_error: Exception | None = None
        mode = "structured" if self.output_mode == "auto" else self.output_mode
        for attempt in range(retries + 1):
            request_input = user_input
            if feedback:
                request_input += f"\n上一次输出未通过校验：{feedback}。请只修正 JSON。"
            try:
                return self._extract_once(instructions, request_input, mode)
            except APIStatusError as exc:
                if (
                    self.output_mode == "auto"
                    and mode == "structured"
                    and _structured_output_unsupported(exc)
                ):
                    mode = "json"
                    feedback = "供应商不支持 json_schema，改用 JSON mode。"
                    last_error = exc
                    continue
                if not _retryable_status(exc):
                    raise ExtractionError(
                        f"API 请求失败（HTTP {getattr(exc, 'status_code', '?')}）：{exc}"
                    ) from exc
                last_error = exc
                feedback = str(exc)[:1000]
            except (APIConnectionError, APITimeoutError, RetryableExtractionError) as exc:
                last_error = exc
                feedback = str(exc)[:1000]
            except ExtractionError as exc:
                # Schema/content errors may be corrected by feeding the concise
                # validation message back to the model, but still back off so a
                # bad gateway cannot be hammered by immediate retries.
                last_error = exc
                feedback = str(exc)[:1000]

            if attempt >= retries:
                break
            base = max(retry_base_delay, 0.0)
            delay = min(max(retry_max_delay, base), base * (2**attempt))
            delay += random.uniform(0.0, min(base, 2.0)) if base else 0.0
            print(
                f"  临时失败：{last_error}；{delay:.1f}s 后重试 "
                f"({attempt + 1}/{retries})",
                file=sys.stderr,
                flush=True,
            )
            if delay:
                time.sleep(delay)
        raise ExtractionError(f"重试 {retries} 次后仍失败：{last_error}")

    def _extract_once(
        self, instructions: str, user_input: str, mode: str
    ) -> ExtractionResult:
        if self.api_style == "responses":
            raw_text, response_id, response_model, usage = self._responses_request(
                instructions, user_input, mode
            )
        else:
            raw_text, response_id, response_model, usage = self._chat_request(
                instructions, user_input, mode
            )
        cleaned_text = _strip_json_fence(raw_text)
        try:
            data = json.loads(cleaned_text)
        except json.JSONDecodeError as exc:
            raise _non_json_error(cleaned_text, exc) from exc
        validation_error = _validation_message(self.validator, data)
        if validation_error:
            raise ExtractionError(validation_error)
        return ExtractionResult(
            data=data,
            model=response_model or self.model,
            response_id=response_id,
            usage=usage,
            api_style=self.api_style,
            output_mode=mode,
        )

    def _responses_request(
        self, instructions: str, user_input: str, mode: str
    ) -> tuple[str, str, str, dict[str, Any]]:
        if mode == "structured":
            text_config = {
                "format": {
                    "type": "json_schema",
                    "name": "poi_note_extraction",
                    "strict": True,
                    "schema": self.schema,
                }
            }
        else:
            text_config = {"format": {"type": "json_object"}}

        kwargs: dict[str, Any] = {
            "model": self.model,
            "instructions": instructions,
            "input": user_input,
            "text": text_config,
            "max_output_tokens": self.max_output_tokens,
            "store": False,
        }
        if self.reasoning_effort:
            kwargs["reasoning"] = {"effort": self.reasoning_effort}
        response = self.client.responses.create(**kwargs)
        return _coerce_responses_response(response, self.model)

    def _chat_request(
        self, instructions: str, user_input: str, mode: str
    ) -> tuple[str, str, str, dict[str, Any]]:
        if mode == "structured":
            response_format = {
                "type": "json_schema",
                "json_schema": {
                    "name": "poi_note_extraction",
                    "strict": True,
                    "schema": self.schema,
                },
            }
        else:
            response_format = {"type": "json_object"}
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": instructions},
                {"role": "user", "content": user_input},
            ],
            "response_format": response_format,
            "max_completion_tokens": self.max_output_tokens,
        }
        completion = self.client.chat.completions.create(**kwargs)
        choice = completion.choices[0]
        if getattr(choice.message, "refusal", None):
            raise ExtractionError(f"模型拒绝：{choice.message.refusal}")
        content = choice.message.content or ""
        if not content:
            raise ExtractionError("Chat Completions API 返回空文本")
        return (
            content,
            completion.id,
            getattr(completion, "model", self.model),
            _usage_dict(getattr(completion, "usage", None)),
        )

    @staticmethod
    def _responses_refusal(response: Any) -> str:
        for item in getattr(response, "output", []) or []:
            for content in getattr(item, "content", []) or []:
                refusal = getattr(content, "refusal", None)
                if refusal:
                    return f"模型拒绝：{refusal}"
        return ""
