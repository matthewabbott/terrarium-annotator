"""Tool dispatcher for routing tool calls to implementations."""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Callable

from terrarium_annotator.tools.corpus_tools import CorpusTools
from terrarium_annotator.tools.glossary_tools import GlossaryTools
from terrarium_annotator.tools.result import ToolResult
from terrarium_annotator.tools.schemas import get_all_tool_schemas
from terrarium_annotator.tools.snapshot_tools import SnapshotTools, SummonState
from terrarium_annotator.tools.xml_formatter import format_error

LOGGER = logging.getLogger(__name__)

if TYPE_CHECKING:
    from terrarium_annotator.corpus import CorpusReader
    from terrarium_annotator.storage import GlossaryStore, RevisionHistory, SnapshotStore


class ToolDispatcher:
    """Route tool calls to implementations."""

    def __init__(
        self,
        glossary: GlossaryStore,
        corpus: CorpusReader,
        revisions: RevisionHistory,
        snapshots: SnapshotStore | None = None,
    ) -> None:
        """
        Initialize dispatcher with storage and corpus.

        Args:
            glossary: GlossaryStore for glossary operations.
            corpus: CorpusReader for corpus operations.
            revisions: RevisionHistory for logging changes.
            snapshots: Optional SnapshotStore (F7). If None, snapshot tools unavailable.
        """
        self._glossary = glossary
        self._glossary_tools = GlossaryTools(glossary, revisions)
        self._corpus_tools = CorpusTools(corpus)
        self._snapshots = snapshots

        # Initialize snapshot tools if snapshots provided
        self._snapshot_tools: SnapshotTools | None = None
        if snapshots is not None:
            self._snapshot_tools = SnapshotTools(snapshots, glossary)

        # Tool name -> (handler method, requires_post_context)
        self._handlers: dict[str, tuple[Callable[..., str], bool]] = {
            "glossary_search": (self._handle_search, False),
            "glossary_create": (self._handle_create, True),
            "glossary_update": (self._handle_update, True),
            "glossary_delete": (self._handle_delete, True),
            "read_post": (self._handle_read_post, False),
            "read_thread_range": (self._handle_read_range, False),
        }

        # Add snapshot handlers if available
        if self._snapshot_tools is not None:
            self._handlers.update({
                "list_snapshots": (self._handle_list_snapshots, False),
                "summon_snapshot": (self._handle_summon_snapshot, False),
                "summon_continue": (self._handle_summon_continue, False),
                "summon_dismiss": (self._handle_summon_dismiss, False),
            })

    # Write tools that should be blocked during summon
    _WRITE_TOOLS = {"glossary_create", "glossary_update", "glossary_delete"}

    def dispatch(
        self,
        tool_call: dict,
        *,
        current_post_id: int,
        current_thread_id: int,
    ) -> ToolResult:
        """
        Execute tool call.

        Args:
            tool_call: OpenAI format tool call dict with:
                - id: call ID
                - function.name: tool name
                - function.arguments: JSON string of args
            current_post_id: Post being processed (for revision logging)
            current_thread_id: Thread being processed (for revision logging)

        Returns: ToolResult with success status and XML response.
        """
        call_id = tool_call.get("id", "unknown")
        func = tool_call.get("function", {})
        tool_name = func.get("name", "")
        args_json = func.get("arguments", "{}")

        LOGGER.info("Tool call: %s (id=%s)", tool_name, call_id)

        # Block write operations during summon
        if self.has_active_summon and tool_name in self._WRITE_TOOLS:
            LOGGER.warning(
                "Tool blocked during summon: %s code=SUMMON_READ_ONLY", tool_name
            )
            return ToolResult(
                tool_name=tool_name,
                call_id=call_id,
                success=False,
                result=format_error(
                    "Write operations blocked during summon. Use summon_dismiss first.",
                    code="SUMMON_READ_ONLY",
                ),
                error="Summon is read-only",
            )

        # Parse JSON arguments
        try:
            args = json.loads(args_json)
        except json.JSONDecodeError as e:
            LOGGER.warning("Tool error: %s code=INVALID_JSON: %s", tool_name, e)
            return ToolResult(
                tool_name=tool_name,
                call_id=call_id,
                success=False,
                result=format_error(f"Invalid JSON arguments: {e}", code="INVALID_JSON"),
                error=str(e),
            )

        # Find handler
        handler_entry = self._handlers.get(tool_name)
        if handler_entry is None:
            LOGGER.warning("Tool error: %s code=UNKNOWN_TOOL", tool_name)
            return ToolResult(
                tool_name=tool_name,
                call_id=call_id,
                success=False,
                result=format_error(f"Unknown tool: {tool_name}", code="UNKNOWN_TOOL"),
                error=f"Unknown tool: {tool_name}",
            )

        handler, requires_post = handler_entry

        # Execute handler
        try:
            if requires_post:
                result = handler(args, current_post_id, current_thread_id)
            else:
                result = handler(args)

            # Detect success/failure from error tag presence
            success = "<error" not in result

            if success:
                LOGGER.debug("Tool success: %s", tool_name)
            else:
                # Extract error code from result
                code_match = re.search(r'code="([^"]+)"', result)
                code = code_match.group(1) if code_match else "UNKNOWN"
                LOGGER.warning("Tool error: %s code=%s", tool_name, code)

            return ToolResult(
                tool_name=tool_name,
                call_id=call_id,
                success=success,
                result=result,
                error=None if success else "Tool returned error",
            )

        except Exception as e:
            LOGGER.error("Tool exception: %s: %s", tool_name, e)
            return ToolResult(
                tool_name=tool_name,
                call_id=call_id,
                success=False,
                result=format_error(str(e), code="EXECUTION_ERROR"),
                error=str(e),
            )

    def get_tool_definitions(self) -> list[dict]:
        """Return OpenAI function calling schemas."""
        return get_all_tool_schemas(
            include_snapshot_tools=self._snapshots is not None
        )

    @property
    def has_active_summon(self) -> bool:
        """True if a summoned context is active."""
        if self._snapshot_tools is None:
            return False
        return self._snapshot_tools.has_active_summon

    def get_active_summon(self) -> SummonState | None:
        """Return active summon state."""
        if self._snapshot_tools is None:
            return None
        return self._snapshot_tools.get_active_summon()

    # Handler methods

    def _handle_search(self, args: dict) -> str:
        """Handle glossary_search tool call."""
        return self._glossary_tools.search(
            args["query"],
            tags=args.get("tags"),
            status=args.get("status", "all"),
            limit=args.get("limit", 10),
        )

    def _handle_create(self, args: dict, post_id: int, thread_id: int) -> str:
        """Handle glossary_create tool call."""
        return self._glossary_tools.create(
            term=args["term"],
            definition=args["definition"],
            tags=args.get("tags", []),
            status=args.get("status", "tentative"),
            post_id=post_id,
            thread_id=thread_id,
        )

    def _handle_update(self, args: dict, post_id: int, thread_id: int) -> str:
        """Handle glossary_update tool call."""
        return self._glossary_tools.update(
            entry_id=args["entry_id"],
            term=args.get("term"),
            definition=args.get("definition"),
            tags=args.get("tags"),
            status=args.get("status"),
            post_id=post_id,
            thread_id=thread_id,
        )

    def _handle_delete(self, args: dict, post_id: int, thread_id: int) -> str:
        """Handle glossary_delete tool call."""
        return self._glossary_tools.delete(
            entry_id=args["entry_id"],
            reason=args["reason"],
            post_id=post_id,
        )

    def _handle_read_post(self, args: dict) -> str:
        """Handle read_post tool call."""
        return self._corpus_tools.read_post(args["post_id"])

    def _handle_read_range(self, args: dict) -> str:
        """Handle read_thread_range tool call."""
        return self._corpus_tools.read_thread_range(
            args["thread_id"],
            start_post_id=args.get("start_post_id"),
            end_post_id=args.get("end_post_id"),
            tag_filter=args.get("tag_filter"),
        )

    # Snapshot handlers (F7)

    def _handle_list_snapshots(self, args: dict) -> str:
        """Handle list_snapshots tool call."""
        return self._snapshot_tools.list_snapshots(
            limit=args.get("limit", 20),
            snapshot_type=args.get("snapshot_type"),
        )

    def _handle_summon_snapshot(self, args: dict) -> str:
        """Handle summon_snapshot tool call."""
        return self._snapshot_tools.summon_snapshot(args["snapshot_id"])

    def _handle_summon_continue(self, args: dict) -> str:
        """Handle summon_continue tool call."""
        return self._snapshot_tools.summon_continue(args["message"])

    def _handle_summon_dismiss(self, args: dict) -> str:
        """Handle summon_dismiss tool call."""
        return self._snapshot_tools.summon_dismiss()
