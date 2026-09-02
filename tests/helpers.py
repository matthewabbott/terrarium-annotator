"""Shared test utilities."""

from __future__ import annotations

from terrarium_annotator.storage import GlossaryEntry


def make_entry(
    entry_id: int,
    term: str,
    *,
    status: str = "confirmed",
    first_thread: int = 1,
    last_thread: int | None = None,
    post_id: int = 1,
) -> GlossaryEntry:
    """Create a GlossaryEntry for testing."""
    return GlossaryEntry(
        id=entry_id,
        term=term,
        term_normalized=term.lower(),
        definition=f"Definition of {term}",
        status=status,
        tags=["character"],
        first_seen_post_id=post_id,
        first_seen_thread_id=first_thread,
        last_updated_post_id=post_id,
        last_updated_thread_id=last_thread if last_thread is not None else first_thread,
        created_at="2024-01-01",
        updated_at="2024-01-01",
    )
