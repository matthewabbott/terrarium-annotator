"""LLM client seam: the provider-neutral interface.

Design: docs/design/dev-verification.md (Model serving). Everything above
this layer speaks `ChatClient.chat()`; providers are interchangeable
(OpenAI-compatible HTTP now; omp RPC or local servers later).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class ToolCall:
    """One tool invocation requested by the model."""

    name: str
    arguments: dict[str, Any]
    id: str = ""


@dataclass(frozen=True)
class ChatResponse:
    """Parsed assistant turn."""

    content: str | None
    tool_calls: tuple[ToolCall, ...] = ()
    raw: dict = field(default_factory=dict)


class ChatClientError(Exception):
    """Provider or protocol failure."""


class ChatClient(Protocol):
    """The seam. Implementations must be safe to call sequentially forever."""

    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        temperature: float = 0.4,
        max_tokens: int = 2048,
    ) -> ChatResponse: ...


def parse_choice(raw: dict) -> ChatResponse:
    """Parse one OpenAI-style response body. Raises ChatClientError on drift."""
    try:
        message = raw["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ChatClientError(f"malformed response envelope: {exc}") from exc
    tool_calls: list[ToolCall] = []
    for tc in message.get("tool_calls") or []:
        try:
            args_raw = tc["function"]["arguments"]
            args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
            if not isinstance(args, dict):
                raise TypeError("arguments is not an object")
            tool_calls.append(
                ToolCall(
                    name=tc["function"]["name"], arguments=args, id=tc.get("id", "")
                )
            )
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ChatClientError(f"malformed tool call: {exc}") from exc
    return ChatResponse(
        content=message.get("content"), tool_calls=tuple(tool_calls), raw=raw
    )
