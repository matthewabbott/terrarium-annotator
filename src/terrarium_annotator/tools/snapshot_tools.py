"""Snapshot tool implementations for summon workflow."""

from __future__ import annotations

from dataclasses import dataclass, field
from html import escape
from typing import TYPE_CHECKING

from terrarium_annotator.tools.xml_formatter import format_error, format_success

if TYPE_CHECKING:
    from terrarium_annotator.context.annotation import AnnotationContext
    from terrarium_annotator.context.compactor import CompactionState
    from terrarium_annotator.storage import (
        GlossaryStore,
        Snapshot,
        SnapshotEntry,
        SnapshotStore,
    )


@dataclass
class SummonState:
    """State for active summon session."""

    snapshot_id: int
    context: AnnotationContext
    compaction_state: CompactionState
    entry_states: dict[int, SnapshotEntry]
    conversation: list[dict] = field(default_factory=list)


class SnapshotTools:
    """Tool implementations for snapshot operations."""

    def __init__(
        self,
        snapshots: SnapshotStore,
        glossary: GlossaryStore,
    ) -> None:
        """
        Initialize snapshot tools.

        Args:
            snapshots: SnapshotStore for snapshot operations.
            glossary: GlossaryStore for current state comparison.
        """
        self._snapshots = snapshots
        self._glossary = glossary
        self._active_summon: SummonState | None = None

    @property
    def has_active_summon(self) -> bool:
        """True if a summoned context is active."""
        return self._active_summon is not None

    def get_active_summon(self) -> SummonState | None:
        """Return active summon state for dispatcher."""
        return self._active_summon

    def list_snapshots(
        self,
        limit: int = 20,
        snapshot_type: str | None = None,
    ) -> str:
        """
        List recent snapshots.

        Args:
            limit: Maximum snapshots to return.
            snapshot_type: Optional type filter.

        Returns:
            XML-formatted snapshot list.
        """
        snapshots = self._snapshots.list_recent(limit, snapshot_type)
        return self._format_snapshot_list(snapshots)

    def summon_snapshot(self, snapshot_id: int) -> str:
        """
        Summon a snapshot for read-only exploration.

        Args:
            snapshot_id: ID of snapshot to summon.

        Returns:
            XML with snapshot summary and entry states.
        """
        if self._active_summon is not None:
            return format_error(
                f"Summon already active (snapshot {self._active_summon.snapshot_id}). "
                "Use summon_dismiss first.",
                code="SUMMON_ACTIVE",
            )

        snapshot = self._snapshots.get(snapshot_id)
        if snapshot is None:
            return format_error(
                f"Snapshot {snapshot_id} not found",
                code="NOT_FOUND",
            )

        try:
            context, compaction = self._snapshots.restore_context(snapshot_id)
            entries = self._snapshots.restore_entries_view(snapshot_id)
        except Exception as e:
            return format_error(
                f"Failed to restore snapshot: {e}",
                code="RESTORE_FAILED",
            )

        self._active_summon = SummonState(
            snapshot_id=snapshot_id,
            context=context,
            compaction_state=compaction,
            entry_states=entries,
        )

        return self._format_summon_start(snapshot, entries)

    def summon_continue(self, message: str) -> str:
        """
        Continue conversation in summoned context.

        Args:
            message: User's follow-up message.

        Returns:
            Acknowledgment XML.
        """
        if self._active_summon is None:
            return format_error(
                "No active summon. Use summon_snapshot first.",
                code="NO_SUMMON",
            )

        # Record the message for the summoned conversation
        self._active_summon.conversation.append({
            "role": "user",
            "content": message,
        })

        # Return acknowledgment
        truncated = message[:100] + "..." if len(message) > 100 else message
        return format_success(f"Continuing summon conversation: {truncated}")

    def summon_dismiss(self) -> str:
        """
        Dismiss active summon and return to normal operation.

        Returns:
            Success XML.
        """
        if self._active_summon is None:
            return format_error(
                "No active summon to dismiss.",
                code="NO_SUMMON",
            )

        snapshot_id = self._active_summon.snapshot_id
        turns = len(self._active_summon.conversation)
        self._active_summon = None

        return format_success(
            f"Dismissed summon for snapshot {snapshot_id} ({turns} turns)"
        )

    def _format_snapshot_list(self, snapshots: list[Snapshot]) -> str:
        """Format snapshot list as XML."""
        lines = [f'<snapshots count="{len(snapshots)}">']
        for s in snapshots:
            lines.append(
                f'  <snapshot id="{s.id}" type="{escape(s.snapshot_type)}" '
                f'thread="{s.last_thread_id}" post="{s.last_post_id}" '
                f'entries="{s.glossary_entry_count}" created="{escape(s.created_at)}"/>'
            )
        lines.append("</snapshots>")
        return "\n".join(lines)

    def _format_summon_start(
        self,
        snapshot: Snapshot,
        entries: dict[int, SnapshotEntry],
    ) -> str:
        """Format summon activation as XML."""
        lines = [
            f'<summon_active snapshot_id="{snapshot.id}">',
            f'  <snapshot type="{escape(snapshot.snapshot_type)}" '
            f'thread="{snapshot.last_thread_id}" post="{snapshot.last_post_id}" '
            f'created="{escape(snapshot.created_at)}"/>',
            f'  <entries count="{len(entries)}">',
        ]

        # Show first 20 entries
        sorted_entries = sorted(entries.items())[:20]
        for entry_id, entry in sorted_entries:
            # Truncate long definitions
            definition = entry.definition_at_snapshot
            if len(definition) > 100:
                definition = definition[:100] + "..."
            lines.append(
                f'    <entry id="{entry_id}" status="{escape(entry.status_at_snapshot)}">'
                f"{escape(definition)}</entry>"
            )

        if len(entries) > 20:
            lines.append(f"    <note>...and {len(entries) - 20} more entries</note>")

        lines.append("  </entries>")
        lines.append(
            "  <instructions>This is a read-only view. "
            "Use summon_continue to ask questions about this state. "
            "Use summon_dismiss when done.</instructions>"
        )
        lines.append("</summon_active>")

        return "\n".join(lines)
