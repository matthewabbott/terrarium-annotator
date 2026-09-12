"""LLM client seam: protocol, OpenAI-compatible HTTP, scripted/recording,
omp RPC adapter."""

from terrarium_annotator.llm.base import (
    AttemptEvent,
    AttemptObserver,
    ChatClient,
    ChatClientError,
    ChatResponse,
    ToolCall,
    parse_choice,
)
from terrarium_annotator.llm.omp_rpc import OmpRpcClient
from terrarium_annotator.llm.openai_client import OpenAICompatibleClient
from terrarium_annotator.llm.scripted import (
    RecordingClient,
    ReplayClient,
    ScriptedModel,
    response_from_json,
    response_to_json,
)
from terrarium_annotator.llm.telemetry import (
    InstrumentedClient,
    UsageLog,
    format_summary,
    make_run_id,
    summarize,
    usage_log_path,
)

__all__ = [
    "AttemptEvent",
    "AttemptObserver",
    "ChatClient",
    "ChatClientError",
    "ChatResponse",
    "InstrumentedClient",
    "OmpRpcClient",
    "OpenAICompatibleClient",
    "RecordingClient",
    "ReplayClient",
    "ScriptedModel",
    "ToolCall",
    "UsageLog",
    "format_summary",
    "make_run_id",
    "parse_choice",
    "response_from_json",
    "response_to_json",
    "summarize",
    "usage_log_path",
]
