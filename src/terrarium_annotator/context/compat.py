"""Backwards compatibility wrapper for legacy AnnotationContext API."""

from __future__ import annotations

from typing import TYPE_CHECKING, List, Optional

from terrarium_annotator.context.annotation import AnnotationContext
from terrarium_annotator.context.prompts import SYSTEM_PROMPT

if TYPE_CHECKING:
    from terrarium_annotator.codex import CodexEntry
    from terrarium_annotator.corpus import StoryPost


class LegacyAnnotationContext(AnnotationContext):
    """Backwards-compatible wrapper around new AnnotationContext.

    Provides the old API used by runner.py:
    - No required system_prompt argument
    - build_messages(posts, codex_entries) -> tuple[messages, payload]
    - record_user_turn() / record_assistant_turn() methods
    - summary property
    """

    def __init__(
        self,
        max_turns: int = 12,
        conversation_history: Optional[List[dict]] = None,
        summary: Optional[str] = None,
    ) -> None:
        """
        Initialize with legacy signature.

        Args:
            max_turns: Maximum conversation turns to keep.
            conversation_history: Optional initial history.
            summary: Optional cumulative summary.
        """
        super().__init__(system_prompt=SYSTEM_PROMPT, max_turns=max_turns)
        if conversation_history:
            self.conversation_history = list(conversation_history)
        self._legacy_summary = summary

    @property
    def summary(self) -> Optional[str]:
        """Legacy summary accessor."""
        return self._legacy_summary

    @summary.setter
    def summary(self, value: Optional[str]) -> None:
        """Legacy summary setter."""
        self._legacy_summary = value

    def build_messages(  # type: ignore[override]
        self,
        posts: Optional[List[StoryPost]] = None,
        codex_entries: Optional[List[CodexEntry]] = None,
        **kwargs,
    ) -> tuple[List[dict], str]:
        """
        Legacy signature: accepts posts + codex_entries.

        Returns tuple of (messages, user_payload) for backwards compatibility.
        """
        # Detect new-style call
        if "current_scene" in kwargs:
            messages = super().build_messages(**kwargs)
            user_payload = messages[-1]["content"] if messages else ""
            return messages, user_payload

        # Legacy mode - convert posts to Scene
        from terrarium_annotator.corpus import Scene
        from terrarium_annotator.storage import GlossaryEntry

        scene = Scene(
            thread_id=posts[0].thread_id if posts else 0,
            posts=posts or [],
            is_thread_start=True,
            is_thread_end=True,
            scene_index=0,
        )

        # Convert CodexEntry to GlossaryEntry
        entries: list[GlossaryEntry] = []
        if codex_entries:
            for ce in codex_entries:
                entries.append(
                    GlossaryEntry(
                        id=None,
                        term=ce.term,
                        term_normalized=ce.term.lower(),
                        definition=ce.definition,
                        status="confirmed",
                        tags=[],
                        first_seen_post_id=0,
                        first_seen_thread_id=0,
                        last_updated_post_id=0,
                        last_updated_thread_id=0,
                        created_at="",
                        updated_at="",
                    )
                )

        messages = super().build_messages(
            cumulative_summary=self._legacy_summary,
            thread_summaries=[],
            current_scene=scene,
            relevant_entries=entries,
            tools=[],
        )

        user_payload = messages[-1]["content"] if messages else ""
        return messages, user_payload

    def record_user_turn(self, content: str) -> None:
        """Legacy method - delegates to record_turn."""
        self.record_turn("user", content)

    def record_assistant_turn(self, content: str) -> None:
        """Legacy method - delegates to record_turn."""
        self.record_turn("assistant", content)
