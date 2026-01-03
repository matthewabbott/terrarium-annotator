"""Context layer for conversation state and token management."""

from terrarium_annotator.context.annotation import AnnotationContext
from terrarium_annotator.context.compactor import (
    CompactionResult,
    CompactionState,
    ContextCompactor,
)
from terrarium_annotator.context.metrics import CompactionStats, ContextMetrics
from terrarium_annotator.context.models import ThreadSummary
from terrarium_annotator.context.prompts import (
    CUMULATIVE_SUMMARY_PROMPT,
    SYSTEM_PROMPT,
    THREAD_SUMMARY_PROMPT,
    TOOL_SYSTEM_PROMPT,
)
from terrarium_annotator.context.summarizer import SummaryResult, ThreadSummarizer
from terrarium_annotator.context.token_counter import TokenCounter

# Alias for backwards compatibility with tests
NewAnnotationContext = AnnotationContext

__all__ = [
    "AnnotationContext",
    "CompactionResult",
    "CompactionState",
    "CompactionStats",
    "ContextCompactor",
    "ContextMetrics",
    "CUMULATIVE_SUMMARY_PROMPT",
    "NewAnnotationContext",  # Alias for backwards compatibility
    "SummaryResult",
    "SYSTEM_PROMPT",
    "THREAD_SUMMARY_PROMPT",
    "ThreadSummarizer",
    "ThreadSummary",
    "TokenCounter",
    "TOOL_SYSTEM_PROMPT",
]
