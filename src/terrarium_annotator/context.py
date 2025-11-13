"""Conversation context management for the annotator harness."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from .codex import CodexEntry
from .corpus import StoryPost

SYSTEM_PROMPT = """You are Terra-annotator, a focused LLM agent tasked with building and maintaining a codex for the Banished Quest corpus.

Instructions:
1. Read the supplied <story_passages> in chronological order.
2. When a term, name, place, faction, or mechanic needs definition, propose or refine a codex entry.
3. Always return codex changes as JSON inside <codex_updates>...</codex_updates>. Each object requires term, definition, status (new|update|skip), and source_post_id.
4. Keep optional reasoning inside <analysis>...</analysis>. Do not include prose outside those tags.
5. If no changes are required, emit an empty list: <codex_updates>[]</codex_updates>.
6. Respect provided codex entries—they mirror the current ground truth.
"""


@dataclass
class AnnotationContext:
    """Tracks rolling conversation state for the annotator."""

    max_turns: int = 12
    conversation_history: List[dict] = field(default_factory=list)
    summary: Optional[str] = None

    def build_messages(
        self,
        posts: List[StoryPost],
        codex_entries: List[CodexEntry],
    ) -> tuple[List[dict], str]:
        messages: List[dict] = [
            {"role": "system", "content": SYSTEM_PROMPT}
        ]

        if self.summary:
            messages.append({
                "role": "system",
                "content": f"<conversation_summary>{self.summary}</conversation_summary>",
            })

        recent_turns = self.conversation_history[-self.max_turns :]
        messages.extend(recent_turns)

        user_payload = self.format_user_payload(posts, codex_entries)
        messages.append({"role": "user", "content": user_payload})
        return messages, user_payload

    def record_user_turn(self, content: str) -> None:
        self.conversation_history.append({"role": "user", "content": content})

    def record_assistant_turn(self, content: str) -> None:
        self.conversation_history.append({"role": "assistant", "content": content})

    @staticmethod
    def format_user_payload(posts: List[StoryPost], codex_entries: List[CodexEntry]) -> str:
        lines: List[str] = ["<story_passages>"]
        for post in posts:
            meta = []
            if post.post_id is not None:
                meta.append(f"id=\"{post.post_id}\"")
            if post.created_at:
                meta.append(f"ts=\"{post.created_at.isoformat()}\"")
            if post.author:
                meta.append(f"author=\"{post.author}\"")
            attr = " ".join(meta)
            body = (post.body or "").strip()
            lines.append(f"<post {attr}>{body}</post>")
        lines.append("</story_passages>")

        if codex_entries:
            lines.append("<known_codex>")
            for entry in codex_entries:
                lines.append(f"<term name=\"{entry.term}\">{entry.definition}</term>")
            lines.append("</known_codex>")

        lines.append("<instructions>Emit codex updates plus optional analysis as specified.</instructions>")
        return "\n".join(lines)
