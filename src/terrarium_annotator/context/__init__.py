"""Context layer for conversation state and token management."""

from terrarium_annotator.context.annotation import AnnotationContext
from terrarium_annotator.context.models import ThreadSummary
from terrarium_annotator.context.prompts import SYSTEM_PROMPT, TOOL_SYSTEM_PROMPT
from terrarium_annotator.context.token_counter import TokenCounter

# Alias for backwards compatibility with tests
NewAnnotationContext = AnnotationContext

__all__ = [
    "AnnotationContext",
    "NewAnnotationContext",  # Alias for backwards compatibility
    "SYSTEM_PROMPT",
    "TOOL_SYSTEM_PROMPT",
    "ThreadSummary",
    "TokenCounter",
]
