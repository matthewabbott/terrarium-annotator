"""Scripted (fixture-replay) and recording clients.

ScriptedModel drives the full runner end-to-end in tests with zero model
spend (dev-verification L1). RecordingClient wraps any client and logs
request/response pairs as JSONL for L4 replay fixtures.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

from terrarium_annotator.llm.base import (
    ChatClient,
    ChatClientError,
    ChatResponse,
    ToolCall,
)


class ScriptedModel:
    """Replays a fixed script of responses (or exceptions), one per call.

    Every received request is recorded in `.requests` for assertions.
    """

    def __init__(self, script: list[ChatResponse | Exception]) -> None:
        self._script = list(script)
        self.requests: list[dict] = []

    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        temperature: float = 0.4,
        max_tokens: int = 2048,
    ) -> ChatResponse:
        # Deep-copy: callers mutate `messages` between calls (the runner's
        # tool loop grows it in place); recording by reference would alias
        # every request to the final list state.
        self.requests.append(
            copy.deepcopy(
                {
                    "messages": messages,
                    "tools": tools,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                }
            )
        )
        if not self._script:
            raise ChatClientError("script exhausted")
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def response_to_json(resp: ChatResponse) -> dict:
    return {
        "content": resp.content,
        "tool_calls": [
            {"name": tc.name, "arguments": tc.arguments, "id": tc.id}
            for tc in resp.tool_calls
        ],
    }


def response_from_json(data: dict) -> ChatResponse:
    return ChatResponse(
        content=data.get("content"),
        tool_calls=tuple(
            ToolCall(name=tc["name"], arguments=tc["arguments"], id=tc.get("id", ""))
            for tc in data.get("tool_calls", [])
        ),
    )


class RecordingClient:
    """Wraps a ChatClient, appending {request, response} JSONL records."""

    def __init__(self, inner: ChatClient, record_path: Path | str) -> None:
        self._inner = inner
        self._path = Path(record_path)

    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        temperature: float = 0.4,
        max_tokens: int = 2048,
    ) -> ChatResponse:
        resp = self._inner.chat(
            messages, tools, temperature=temperature, max_tokens=max_tokens
        )
        with self._path.open("a", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    {
                        "request": {
                            "messages": messages,
                            "tools": tools,
                            "temperature": temperature,
                            "max_tokens": max_tokens,
                        },
                        "response": response_to_json(resp),
                    }
                )
                + "\n"
            )
        return resp


class ReplayClient:
    """L4: replays a RecordingClient JSONL fixture, asserting the request
    sequence matches exactly (idempotence of the recorded stream)."""

    def __init__(self, record_path: Path | str) -> None:
        self._records = [
            json.loads(line)
            for line in Path(record_path).read_text().splitlines()
            if line.strip()
        ]
        self._pos = 0

    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        temperature: float = 0.4,
        max_tokens: int = 2048,
    ) -> ChatResponse:
        if self._pos >= len(self._records):
            raise ChatClientError("replay exhausted")
        record = self._records[self._pos]
        self._pos += 1
        expected = record["request"]
        actual = {
            "messages": messages,
            "tools": tools,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if actual != expected:
            raise ChatClientError(
                f"replay divergence at call {self._pos}: request differs from recording"
            )
        return response_from_json(record["response"])
