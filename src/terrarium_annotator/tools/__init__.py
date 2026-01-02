"""Tool layer for glossary and corpus operations.

This module provides:
- ToolDispatcher: Routes tool calls to implementations
- ToolResult: Result dataclass for tool execution
- Tool exceptions for error handling
- OpenAI function calling schemas
"""

from __future__ import annotations

from terrarium_annotator.tools.dispatcher import ToolDispatcher
from terrarium_annotator.tools.exceptions import (
    SnapshotToolsUnavailableError,
    ToolError,
    ToolExecutionError,
    ToolNotFoundError,
    ToolValidationError,
)
from terrarium_annotator.tools.result import ToolResult
from terrarium_annotator.tools.schemas import get_all_tool_schemas

__all__ = [
    "ToolDispatcher",
    "ToolResult",
    "ToolError",
    "ToolNotFoundError",
    "ToolValidationError",
    "ToolExecutionError",
    "SnapshotToolsUnavailableError",
    "get_all_tool_schemas",
]
