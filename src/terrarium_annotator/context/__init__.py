"""Context layer for conversation state and token management."""

# Export legacy-compatible AnnotationContext as default for backwards compatibility
# with existing runner.py code. New code should use NewAnnotationContext.
from terrarium_annotator.context.annotation import (
    AnnotationContext as NewAnnotationContext,
)
from terrarium_annotator.context.compat import (
    LegacyAnnotationContext as AnnotationContext,
)
from terrarium_annotator.context.models import ThreadSummary
from terrarium_annotator.context.prompts import SYSTEM_PROMPT
from terrarium_annotator.context.token_counter import TokenCounter

__all__ = [
    "AnnotationContext",  # Legacy-compatible wrapper
    "NewAnnotationContext",  # New API
    "SYSTEM_PROMPT",
    "ThreadSummary",
    "TokenCounter",
]
