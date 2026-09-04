"""Specificity gate for entry admission (design: critic-salience-epistemics §4).

SHADOW MODE: the gate never blocks a write. It classifies proposed terms
and logs would-be deferrals to `deferred_candidate` for calibration against
the gold set. Enforcement comes only after the calibration report.

Heuristic: a term is *generic* (deferral candidate) when it has no
uppercase letter, no digit, and no non-ASCII character — i.e. it reads as a
bare common-noun descriptor ("strange language", "old fort") rather than a
coined/proper term ("Vys", "खुनी", "Rikāmā Rahivāsī"). Deliberately simple
and deterministic; the calibration pass decides whether it earns trust.
"""

from __future__ import annotations


def is_generic_term(term: str) -> bool:
    """True when the term reads as a bare common-noun descriptor."""
    text = term.strip()
    if not text:
        return False
    if any(c.isupper() for c in text):
        return False
    if any(c.isdigit() for c in text):
        return False
    return text.isascii()
