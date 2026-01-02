"""Tool result dataclass."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ToolResult:
    """Result from a tool execution."""

    tool_name: str
    call_id: str
    success: bool
    result: str  # XML response
    error: str | None = None
