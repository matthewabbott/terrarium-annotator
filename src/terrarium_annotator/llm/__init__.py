"""LLM client seam: protocol, OpenAI-compatible HTTP, scripted/recording."""

from terrarium_annotator.llm.base import (
    ChatClient,
    ChatClientError,
    ChatResponse,
    ToolCall,
    parse_choice,
)
from terrarium_annotator.llm.openai_client import OpenAICompatibleClient
from terrarium_annotator.llm.scripted import (
    RecordingClient,
    ReplayClient,
    ScriptedModel,
    response_from_json,
    response_to_json,
)

__all__ = [
    "ChatClient",
    "ChatClientError",
    "ChatResponse",
    "OpenAICompatibleClient",
    "RecordingClient",
    "ReplayClient",
    "ScriptedModel",
    "ToolCall",
    "parse_choice",
    "response_from_json",
    "response_to_json",
]
