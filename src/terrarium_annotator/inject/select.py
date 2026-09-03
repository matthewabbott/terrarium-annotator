"""Glossary card injection: deterministic SillyTavern-style selection.

Design: docs/design/v2-architecture.md §3 (tier 1). Pure functions; the
token counter is injected so tests need no tokenizer.

Rules:
- Trigger: a key (term or alias) matches the scene text case-sensitively,
  whole-word. Scan depth 1: only the scene being read.
- Recursion <= 1: injected cards' glosses may trigger one more round of
  matches, then it stops.
- Hard token budget: overflow drops lowest-priority cards. Priority:
  round-0 (directly triggered) before round-1 (recursive), then
  recently-updated, then cheapest.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class CardView:
    """The slice of an entry the injector needs."""

    term: str
    keys: tuple[str, ...]  # aliases; the term itself is always a trigger
    gloss: str
    updated_at: str  # ISO8601; lexicographic order = recency


@dataclass(frozen=True)
class SelectedCard:
    term: str
    gloss: str
    depth: int  # 0 = triggered by scene text, 1 = triggered by a gloss
    tokens: int


def card_text(term: str, gloss: str) -> str:
    """What actually lands in the prompt for one card."""
    return f"{term}: {gloss}"


def matches(text: str, key: str) -> bool:
    """Case-sensitive whole-word match. Multi-word and unicode keys work."""
    if not key:
        return False
    return re.search(rf"(?<!\w){re.escape(key)}(?!\w)", text) is not None


def select_cards(
    scene_text: str,
    cards: list[CardView],
    budget_tokens: int,
    count_tokens: Callable[[str], int],
) -> list[SelectedCard]:
    """Pick the cards to inject for a scene, within the token budget.

    Returns selected cards in injection order (highest priority first).
    """
    if budget_tokens < 1:
        raise ValueError(f"budget_tokens must be >= 1, got {budget_tokens}")

    def triggers(card: CardView) -> tuple[str, ...]:
        return (card.term, *card.keys)

    # Round 0: direct scene matches. Round 1: matches against round-0
    # glosses. No round 2 (recursion cap).
    round0 = [c for c in cards if any(matches(scene_text, k) for k in triggers(c))]
    round0_text = "\n".join(c.gloss for c in round0)  # glosses only, not terms
    round1 = [
        c
        for c in cards
        if c not in round0 and any(matches(round0_text, k) for k in triggers(c))
    ]

    candidates = [*(c for c in round0), *(c for c in round1)]
    depth = {id(c): 0 for c in round0} | {id(c): 1 for c in round1}
    # Priority: round 0 first, then recently updated, then cheapest.
    candidates.sort(key=lambda c: len(c.gloss))  # stable: cheapest last key
    candidates.sort(key=lambda c: c.updated_at, reverse=True)
    candidates.sort(key=lambda c: depth[id(c)])

    selected: list[SelectedCard] = []
    spent = 0
    for c in candidates:
        cost = count_tokens(card_text(c.term, c.gloss))
        if spent + cost > budget_tokens:
            continue  # drop lowest-priority overflow; keep trying cheaper ones
        selected.append(
            SelectedCard(term=c.term, gloss=c.gloss, depth=depth[id(c)], tokens=cost)
        )
        spent += cost
    return selected
