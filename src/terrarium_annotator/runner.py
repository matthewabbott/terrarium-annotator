"""High-level orchestration for the annotator harness."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .agent_client import AgentClient, AgentClientError
from .codex import CodexStore, extract_updates
from .context import AnnotationContext
from .corpus import CorpusReader
from .state import ProgressTracker

LOGGER = logging.getLogger("terrarium_annotator.runner")


@dataclass
class RunnerConfig:
    db_path: Path
    agent_url: str = "http://localhost:8080"
    codex_path: Path = Path("data/codex.json")
    state_path: Path = Path("data/state.json")
    batch_size: int = 1
    temperature: float = 0.3
    max_tokens: int = 768
    resume: bool = True


class AnnotationRunner:
    """Iterates the corpus and drives the LLM-based annotator."""

    def __init__(self, config: RunnerConfig):
        self.config = config
        self.reader = CorpusReader(config.db_path, chunk_size=config.batch_size)
        self.codex = CodexStore(config.codex_path)
        self.context = AnnotationContext()
        self.state = ProgressTracker(config.state_path)
        self.agent = AgentClient(base_url=config.agent_url)

    def run(self, limit: Optional[int] = None) -> None:
        last_post_id: Optional[int] = self.state.load() if self.config.resume else None
        processed = 0
        LOGGER.info("Starting annotation run (resume=%s, last_post_id=%s)", self.config.resume, last_post_id)

        for batch in self.reader.iter_posts(start_after_id=last_post_id, limit=limit):
            batch_text = "\n".join(post.body or "" for post in batch)
            codex_entries = self.codex.matching_entries(batch_text)
            messages, user_payload = self.context.build_messages(batch, codex_entries)
            self.context.record_user_turn(user_payload)

            try:
                response = self.agent.chat(
                    messages=messages,
                    temperature=self.config.temperature,
                    max_tokens=self.config.max_tokens,
                )
            except AgentClientError as exc:
                LOGGER.error("Agent call failed: %s", exc)
                break

            assistant_message = response.message
            assistant_content = assistant_message.get("content") or ""
            self.context.record_assistant_turn(assistant_content)

            updates = extract_updates(assistant_content)
            if updates:
                for update in updates:
                    entry = self.codex.upsert(update)
                    LOGGER.info("Codex %s: %s", update.status, entry.term)
                self.codex.save()
            else:
                LOGGER.debug("No codex changes for posts %s-%s", batch[0].post_id, batch[-1].post_id)

            processed += len(batch)
            terminal_post_id = batch[-1].post_id
            if terminal_post_id:
                self.state.save(terminal_post_id)

        LOGGER.info("Annotation run complete; processed %s posts", processed)
