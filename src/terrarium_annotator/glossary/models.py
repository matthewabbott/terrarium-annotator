"""Data models for the glossary layer.

Design: docs/design/v2-architecture.md §3-4. Entries have a card gloss
(always the latest revision) plus an append-only revision history; every
write carries evidence quotes verified against the corpus.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Evidence:
    """A quote grounding a definition in a specific corpus post."""

    post_id: int
    quote: str


@dataclass(frozen=True)
class Provenance:
    """Where in the story a write happened (blame/rehydration, §4)."""

    thread_id: int
    pass_id: str
    batch_lo: int | None = None
    batch_hi: int | None = None
    log_seq: int | None = None
    tree_version: int | None = None


@dataclass(frozen=True)
class Entry:
    """Current state of a glossary entry (the card)."""

    id: int
    term: str
    gloss: str
    status: str  # "tentative" | "confirmed"
    tags: tuple[str, ...]
    aliases: tuple[str, ...]
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class Revision:
    """One definition version. Append-only; never mutated."""

    id: int
    entry_id: int
    gloss: str
    provenance: Provenance
    created_at: str
