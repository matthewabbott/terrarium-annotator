"""Token counting with vLLM primary, heuristic fallback."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from terrarium_annotator.agent_client import AgentClient

LOGGER = logging.getLogger(__name__)

# Overhead per message for role/formatting
MESSAGE_OVERHEAD = 4

# Extra overhead for tool call structure
TOOL_CALL_OVERHEAD = 10


class TokenCounter:
    """Token counting with vLLM primary, heuristic fallback."""

    def __init__(
        self,
        agent_client: AgentClient | None = None,
        chars_per_token: float = 4.0,
    ) -> None:
        """
        Initialize token counter.

        Args:
            agent_client: Optional AgentClient for vLLM tokenization.
                         If None, always uses heuristic.
            chars_per_token: Fallback heuristic ratio.
        """
        self._client = agent_client
        self._chars_per_token = chars_per_token
        self._using_fallback = agent_client is None
        self._fallback_warned = False

    def count(self, text: str) -> int:
        """Count tokens. Falls back to heuristic on vLLM failure."""
        if self._using_fallback:
            return self._heuristic_count(text)

        try:
            tokens = self._client.tokenize(text)  # type: ignore[union-attr]
            return len(tokens)
        except Exception as exc:
            if not self._fallback_warned:
                LOGGER.warning(
                    "vLLM tokenize failed, falling back to heuristic: %s",
                    exc,
                )
                self._fallback_warned = True
            self._using_fallback = True
            return self._heuristic_count(text)

    def count_messages(self, messages: list[dict]) -> int:
        """Count tokens for message list using heuristic.

        Uses heuristic (chars/4) to avoid spamming the tokenize endpoint.
        This is accurate enough for budget tracking with 20% headroom.
        """
        total = 0

        for msg in messages:
            content = msg.get("content", "")
            if content:
                total += self._heuristic_count(content)
            total += MESSAGE_OVERHEAD

            # Tool calls add extra overhead
            if "tool_calls" in msg:
                for tool_call in msg["tool_calls"]:
                    func = tool_call.get("function", {})
                    name = func.get("name", "")
                    args = func.get("arguments", "")
                    if name:
                        total += self._heuristic_count(name)
                    if args:
                        total += self._heuristic_count(args)
                    total += TOOL_CALL_OVERHEAD

        return total

    @property
    def using_fallback(self) -> bool:
        """True if vLLM tokenize failed and we're using heuristic."""
        return self._using_fallback

    def _heuristic_count(self, text: str) -> int:
        """Estimate token count from character count."""
        return max(1, int(len(text) / self._chars_per_token))
