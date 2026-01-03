"""Conversation state and message building."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from terrarium_annotator.context.models import ThreadSummary
    from terrarium_annotator.corpus import Scene
    from terrarium_annotator.storage import GlossaryEntry


@dataclass
class AnnotationContext:
    """Conversation state and message building."""

    system_prompt: str
    conversation_history: list[dict] = field(default_factory=list)

    def build_messages(
        self,
        *,
        cumulative_summary: str | None = None,
        thread_summaries: list[ThreadSummary] | None = None,
        current_scene: Scene | None = None,
        relevant_entries: list[GlossaryEntry] | None = None,
        tools: list[dict] | None = None,
    ) -> list[dict]:
        """Build OpenAI-compatible message list for annotation request.

        Constructs messages in order:
        1. System prompt
        2. Cumulative summary (if provided)
        3. Thread summaries (if provided)
        4. Full conversation history (compaction handles size limits)
        5. User message with current scene and relevant glossary entries

        Args:
            cumulative_summary: Running summary of all completed threads.
            thread_summaries: Summaries of recently completed threads.
            current_scene: Scene being annotated.
            relevant_entries: Glossary entries to include for context.
            tools: Tool definitions (currently unused, for future tool_choice).

        Returns:
            List of message dicts with 'role' and 'content' keys,
            ready for OpenAI chat completion API.
        """
        messages: list[dict] = [{"role": "system", "content": self.system_prompt}]

        # Add cumulative summary if present
        if cumulative_summary:
            messages.append(
                {
                    "role": "system",
                    "content": f"<cumulative_summary>{cumulative_summary}</cumulative_summary>",
                }
            )

        # Add thread summaries
        if thread_summaries:
            summaries_xml = self._format_thread_summaries(thread_summaries)
            messages.append(
                {
                    "role": "system",
                    "content": summaries_xml,
                }
            )

        # Add full conversation history (compaction manages size)
        messages.extend(self.conversation_history)

        # Build and add user payload with scene + entries
        if current_scene is not None:
            user_content = self._format_user_payload(
                current_scene,
                relevant_entries or [],
            )
            messages.append({"role": "user", "content": user_content})

        return messages

    def record_turn(
        self,
        role: Literal["user", "assistant", "tool"],
        content: str,
        *,
        tool_call_id: str | None = None,
        thread_id: int | None = None,
    ) -> None:
        """Record a conversation turn in history.

        Args:
            role: Message role - 'user', 'assistant', or 'tool'.
            content: Message content.
            tool_call_id: Required when role='tool', the ID of the tool call
                being responded to.
            thread_id: Thread ID to tag this turn for later filtering during
                compaction. Turns without thread_id are preserved during
                thread-based compaction.
        """
        turn: dict = {"role": role, "content": content}
        if tool_call_id is not None:
            turn["tool_call_id"] = tool_call_id
        if thread_id is not None:
            turn["thread_id"] = thread_id
        self.conversation_history.append(turn)

    def remove_thread_turns(self, thread_id: int) -> int:
        """Remove all turns belonging to a specific thread.

        Used by compaction to remove old thread's conversation turns
        after they've been summarized.

        Args:
            thread_id: The thread ID whose turns should be removed.

        Returns:
            Number of turns removed.
        """
        original_len = len(self.conversation_history)
        self.conversation_history = [
            turn
            for turn in self.conversation_history
            if turn.get("thread_id") != thread_id
        ]
        return original_len - len(self.conversation_history)

    def get_history(self) -> list[dict]:
        """Get conversation history (for serialization)."""
        return list(self.conversation_history)

    def clone(self) -> AnnotationContext:
        """Deep copy for forking (curator, summon)."""
        return AnnotationContext(
            system_prompt=self.system_prompt,
            conversation_history=copy.deepcopy(self.conversation_history),
        )

    def to_dict(self) -> dict:
        """Serialize to dict for snapshot storage."""
        return {
            "system_prompt": self.system_prompt,
            "conversation_history": self.conversation_history,
        }

    @classmethod
    def from_dict(cls, data: dict) -> AnnotationContext:
        """Reconstruct from snapshot data."""
        ctx = cls(system_prompt=data["system_prompt"])
        ctx.conversation_history = data.get("conversation_history", [])
        return ctx

    def _format_user_payload(
        self,
        scene: Scene,
        entries: list[GlossaryEntry],
    ) -> str:
        """Format scene posts and glossary entries for user message."""
        lines: list[str] = ["<story_passages>"]

        for post in scene.posts:
            meta = [f'id="{post.post_id}"']
            if post.created_at:
                meta.append(f'ts="{post.created_at.isoformat()}"')
            if post.author:
                meta.append(f'author="{post.author}"')
            attr = " ".join(meta)
            body = (post.body or "").strip()
            lines.append(f"<post {attr}>{body}</post>")

        lines.append("</story_passages>")

        if entries:
            lines.append("<known_glossary>")
            for entry in entries:
                tags_attr = f' tags="{",".join(entry.tags)}"' if entry.tags else ""
                lines.append(
                    f'<term name="{entry.term}"{tags_attr}>{entry.definition}</term>'
                )
            lines.append("</known_glossary>")

        lines.append(
            "<instructions>Emit glossary updates using tools as specified.</instructions>"
        )
        return "\n".join(lines)

    def _format_thread_summaries(self, summaries: list[ThreadSummary]) -> str:
        """Format thread summaries as XML block with entry IDs.

        Entry IDs help the agent know which glossary entries it created/updated
        in each thread, enabling proper updates (e.g., renaming 'weird sphere'
        to 'archeota' when the proper name is discovered).
        """
        lines = ["<thread_summaries>"]
        for ts in summaries:
            # Include entry IDs if any were created/updated
            entries_attr = ""
            if ts.entries_created or ts.entries_updated:
                all_ids = ts.entries_created + ts.entries_updated
                entries_attr = f' entries="{",".join(map(str, all_ids))}"'
            lines.append(
                f'<thread id="{ts.thread_id}" position="{ts.position}"{entries_attr}>'
                f"{ts.summary_text}</thread>"
            )
        lines.append("</thread_summaries>")
        return "\n".join(lines)
