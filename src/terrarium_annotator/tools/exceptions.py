"""Tool-specific exceptions."""

from __future__ import annotations


class ToolError(Exception):
    """Base exception for tool operations."""


class ToolNotFoundError(ToolError):
    """Tool name not recognized."""


class ToolValidationError(ToolError):
    """Tool arguments failed validation."""


class ToolExecutionError(ToolError):
    """Tool execution failed."""


class SnapshotToolsUnavailableError(ToolError):
    """Snapshot tools require SnapshotStore (F7)."""
