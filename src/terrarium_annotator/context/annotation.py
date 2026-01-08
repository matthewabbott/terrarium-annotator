"""Conversation state and message building."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from terrarium_annotator.context.models import ChunkSummary, ThreadSummary
    from terrarium_annotator.corpus import Scene, StoryPost, ThreadContent
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
        chunk_summaries: list[ChunkSummary] | None = None,
        current_scene: Scene | None = None,
        relevant_entries: list[GlossaryEntry] | None = None,
        detected_terms_xml: str | None = None,
        tools: list[dict] | None = None,
    ) -> list[dict]:
        """Build OpenAI-compatible message list for annotation request.

        Constructs messages in order:
        1. System prompt
        2. Cumulative summary (if provided)
        3. Thread summaries (if provided, legacy - prefer cumulative)
        4. Chunk summaries (if provided, for current thread's old chunks)
        5. Full conversation history (compaction handles size limits)
        6. User message with current scene and relevant glossary entries

        Args:
            cumulative_summary: Running summary of all completed threads.
            thread_summaries: Summaries of recently completed threads (legacy).
            chunk_summaries: Summaries of old scene chunks in current thread.
            current_scene: Scene being annotated.
            relevant_entries: Glossary entries to include for context.
            detected_terms_xml: XML block with detected novel/semantic terms.
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

        # Add thread summaries (legacy - prefer merging into cumulative)
        if thread_summaries:
            summaries_xml = self._format_thread_summaries(thread_summaries)
            messages.append(
                {
                    "role": "system",
                    "content": summaries_xml,
                }
            )

        # Add chunk summaries for current thread
        if chunk_summaries:
            chunks_xml = self._format_chunk_summaries(chunk_summaries)
            messages.append(
                {
                    "role": "system",
                    "content": chunks_xml,
                }
            )

        # Add full conversation history (compaction manages size)
        messages.extend(self.conversation_history)

        # Build and add user payload with scene + entries + detected terms
        if current_scene is not None:
            user_content = self._format_user_payload(
                current_scene,
                relevant_entries or [],
                detected_terms_xml=detected_terms_xml,
            )
            messages.append({"role": "user", "content": user_content})

        return messages

    def build_thread_messages(
        self,
        *,
        current_thread: ThreadContent,
        previous_thread_posts: list[StoryPost] | None = None,
        cumulative_summary: str | None = None,
        relevant_entries: list[GlossaryEntry] | None = None,
        detected_terms_xml: str | None = None,
    ) -> list[dict]:
        """Build messages for thread-based processing (F11).

        Constructs messages in order:
        1. System prompt
        2. Cumulative summary (all completed threads)
        3. Previous thread QM posts (sliding window context)
        4. Conversation history (accumulates within thread)
        5. Current thread QM posts as user message

        Args:
            current_thread: ThreadContent being processed.
            previous_thread_posts: QM posts from previous thread (context window).
            cumulative_summary: Summary of all completed threads.
            relevant_entries: Glossary entries to include for context.
            detected_terms_xml: XML block with detected novel/semantic terms.

        Returns:
            List of message dicts ready for OpenAI chat completion API.
        """
        messages: list[dict] = [{"role": "system", "content": self.system_prompt}]

        # Add cumulative summary
        if cumulative_summary:
            messages.append(
                {
                    "role": "system",
                    "content": f"<cumulative_summary>{cumulative_summary}</cumulative_summary>",
                }
            )

        # Add previous thread's QM posts as context
        if previous_thread_posts:
            prev_xml = self._format_previous_thread(previous_thread_posts)
            messages.append({"role": "system", "content": prev_xml})

        # Add conversation history (accumulates within thread)
        messages.extend(self.conversation_history)

        # Build user payload with current thread's QM posts
        user_content = self._format_thread_payload(
            current_thread,
            relevant_entries or [],
            detected_terms_xml=detected_terms_xml,
        )
        messages.append({"role": "user", "content": user_content})

        return messages

    def reset_for_new_thread(self) -> None:
        """Clear conversation history for new thread (F11).

        Called between threads to reset accumulated history while
        preserving cumulative summary in compaction state.
        """
        self.conversation_history = []

    def reset_for_new_scene(self) -> None:
        """Clear conversation history for new scene (reader mode).

        Called between scenes in reader mode. The glossary IS the memory,
        so we don't accumulate conversation history across scenes.
        """
        self.conversation_history = []

    def build_reader_messages(
        self,
        *,
        current_scene: Scene,
        story_summary: str | None = None,
        glossary_context: list[GlossaryEntry] | None = None,
    ) -> list[dict]:
        """Build messages for reader mode (near-stateless scene processing).

        In reader mode, each scene is processed with minimal context:
        - Story summary (cumulative)
        - Current scene content
        - Glossary entries for terms appearing in this scene
        - Conversation history (accumulates within scene, resets after)

        The glossary IS the agent's persistent memory, not conversation history.

        Args:
            current_scene: Scene being processed.
            story_summary: Cumulative summary of story so far.
            glossary_context: Glossary entries for terms in this scene.

        Returns:
            List of message dicts ready for OpenAI chat completion API.
        """
        messages: list[dict] = [{"role": "system", "content": self.system_prompt}]

        # Add story summary if present
        if story_summary:
            messages.append(
                {
                    "role": "system",
                    "content": f"<story_summary>{story_summary}</story_summary>",
                }
            )

        # Add conversation history (accumulates within scene)
        messages.extend(self.conversation_history)

        # Build user payload with scene + glossary context
        user_content = self._format_reader_payload(
            current_scene,
            glossary_context or [],
        )
        messages.append({"role": "user", "content": user_content})

        return messages

    def _format_reader_payload(
        self,
        scene: Scene,
        entries: list[GlossaryEntry],
    ) -> str:
        """Format scene and glossary context for reader mode."""
        lines: list[str] = []

        # Current scene content
        lines.append(
            f'<current_scene thread="{scene.thread_id}" index="{scene.scene_index}">'
        )
        for post in scene.posts:
            meta = [f'id="{post.post_id}"']
            if post.created_at:
                meta.append(f'ts="{post.created_at.isoformat()}"')
            if post.author:
                meta.append(f'author="{post.author}"')
            attr = " ".join(meta)
            body = (post.body or "").strip()
            lines.append(f"<post {attr}>{body}</post>")
        lines.append("</current_scene>")

        # Glossary context - definitions for terms appearing in this scene
        if entries:
            lines.append("")
            lines.append("<glossary_context>")
            for entry in entries:
                tags_attr = f' tags="{",".join(entry.tags)}"' if entry.tags else ""
                status_attr = f' status="{entry.status}"'
                lines.append(
                    f'<entry id="{entry.id}" term="{entry.term}"{status_attr}{tags_attr}>'
                    f"{entry.definition}</entry>"
                )
            lines.append("</glossary_context>")

        lines.append("")
        lines.append(
            "<instructions>Read this scene. Notice terms with special in-story meanings. "
            "Add or update glossary entries as needed using glossary_upsert. "
            "Check glossary_context for existing definitions before adding.</instructions>"
        )
        return "\n".join(lines)

    def _format_previous_thread(self, posts: list[StoryPost]) -> str:
        """Format previous thread's QM posts as context window."""
        if not posts:
            return ""

        thread_id = posts[0].thread_id if posts else 0
        lines = [f'<previous_thread id="{thread_id}">']

        for post in posts:
            meta = [f'id="{post.post_id}"']
            if post.created_at:
                meta.append(f'ts="{post.created_at.isoformat()}"')
            if post.author:
                meta.append(f'author="{post.author}"')
            attr = " ".join(meta)
            body = (post.body or "").strip()
            lines.append(f"<post {attr}>{body}</post>")

        lines.append("</previous_thread>")
        return "\n".join(lines)

    def _format_thread_payload(
        self,
        thread: ThreadContent,
        entries: list[GlossaryEntry],
        detected_terms_xml: str | None = None,
    ) -> str:
        """Format all QM posts from a thread for user message."""
        title = thread.thread_title or "Untitled"
        lines = [f'<current_thread id="{thread.thread_id}" title="{title}">']

        for post in thread.qm_posts:
            meta = [f'id="{post.post_id}"']
            if post.created_at:
                meta.append(f'ts="{post.created_at.isoformat()}"')
            if post.author:
                meta.append(f'author="{post.author}"')
            attr = " ".join(meta)
            body = (post.body or "").strip()
            lines.append(f"<post {attr}>{body}</post>")

        lines.append("</current_thread>")

        # Add detected terms
        if detected_terms_xml:
            lines.append("")
            lines.append(detected_terms_xml)

        # Add relevant glossary entries
        if entries:
            lines.append("<known_glossary>")
            for entry in entries:
                tags_attr = f' tags="{",".join(entry.tags)}"' if entry.tags else ""
                lines.append(
                    f'<term name="{entry.term}"{tags_attr}>{entry.definition}</term>'
                )
            lines.append("</known_glossary>")

        lines.append(
            "<instructions>Annotate this thread. Create or update glossary/codex "
            "entries for significant terms and entities. Use tools as specified.</instructions>"
        )
        return "\n".join(lines)

    def record_turn(
        self,
        role: Literal["user", "assistant", "tool"],
        content: str,
        *,
        tool_call_id: str | None = None,
        thread_id: int | None = None,
        scene_index: int | None = None,
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
            scene_index: Scene index within thread for chunk-based compaction.
                Enables removal of old chunks while preserving recent ones.
        """
        turn: dict = {"role": role, "content": content}
        if tool_call_id is not None:
            turn["tool_call_id"] = tool_call_id
        if thread_id is not None:
            turn["thread_id"] = thread_id
        if scene_index is not None:
            turn["scene_index"] = scene_index
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

    def remove_chunk_turns(
        self,
        thread_id: int,
        first_scene_index: int,
        last_scene_index: int,
    ) -> int:
        """Remove turns belonging to a specific scene chunk within a thread.

        Used by chunk compaction (Tier 0.5) to remove old chunks' turns
        after they've been summarized, while preserving recent chunks.

        Args:
            thread_id: The thread ID containing the chunk.
            first_scene_index: First scene index in the chunk (inclusive).
            last_scene_index: Last scene index in the chunk (inclusive).

        Returns:
            Number of turns removed.
        """
        original_len = len(self.conversation_history)

        def should_keep(turn: dict) -> bool:
            # Keep turns from other threads
            if turn.get("thread_id") != thread_id:
                return True
            # Keep turns without scene_index (tool calls, etc.)
            scene_idx = turn.get("scene_index")
            if scene_idx is None:
                return True
            # Remove turns within the chunk's scene range
            return not (first_scene_index <= scene_idx <= last_scene_index)

        self.conversation_history = [
            turn for turn in self.conversation_history if should_keep(turn)
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
        detected_terms_xml: str | None = None,
    ) -> str:
        """Format scene posts, detected terms, and glossary entries for user message."""
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

        # Add detected terms section (novel words, semantic jargon candidates)
        if detected_terms_xml:
            lines.append("")
            lines.append(detected_terms_xml)

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

    def _format_chunk_summaries(self, summaries: list[ChunkSummary]) -> str:
        """Format chunk summaries as XML block with entry IDs.

        Chunk summaries represent groups of consecutive scenes within the
        current thread that have been compacted. Entry IDs help the agent
        track which glossary entries were created/updated in each chunk.
        """
        lines = ["<chunk_summaries>"]
        for cs in summaries:
            # Include entry IDs if any were created/updated
            entries_attr = ""
            if cs.entries_created or cs.entries_updated:
                all_ids = cs.entries_created + cs.entries_updated
                entries_attr = f' entries="{",".join(map(str, all_ids))}"'
            lines.append(
                f'<chunk thread="{cs.thread_id}" index="{cs.chunk_index}" '
                f'scenes="{cs.first_scene_index}-{cs.last_scene_index}"{entries_attr}>'
                f"{cs.summary_text}</chunk>"
            )
        lines.append("</chunk_summaries>")
        return "\n".join(lines)
