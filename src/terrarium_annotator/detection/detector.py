"""Novel term detection using NLTK words corpus."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

try:
    import nltk
    from nltk.corpus import words

    NLTK_AVAILABLE = True
except ImportError:
    NLTK_AVAILABLE = False

if TYPE_CHECKING:
    from terrarium_annotator.storage import GlossaryStore

LOGGER = logging.getLogger(__name__)

# Common words to always ignore (not worth flagging)
COMMON_IGNORE = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "must", "shall", "can", "need", "dare",
    "i", "you", "he", "she", "it", "we", "they", "me", "him", "her", "us",
    "them", "my", "your", "his", "its", "our", "their", "mine", "yours",
    "hers", "ours", "theirs", "this", "that", "these", "those", "who",
    "whom", "which", "what", "whose", "where", "when", "why", "how",
    "all", "each", "every", "both", "few", "more", "most", "other", "some",
    "such", "no", "nor", "not", "only", "own", "same", "so", "than", "too",
    "very", "just", "but", "and", "or", "if", "then", "else", "for", "to",
    "from", "by", "on", "at", "in", "of", "up", "out", "off", "over",
    "under", "again", "further", "once", "here", "there", "now", "also",
}


@dataclass
class DetectedTerms:
    """Container for categorized detected terms."""

    novel: list[str] = field(default_factory=list)
    """Terms not in English dictionary (likely made-up words)."""

    capitalized_common: list[str] = field(default_factory=list)
    """Common English words capitalized mid-sentence (semantic jargon candidates)."""

    existing_glossary: list[str] = field(default_factory=list)
    """Terms already in glossary (may need updates)."""

    def is_empty(self) -> bool:
        """True if no terms detected in any category."""
        return not (self.novel or self.capitalized_common or self.existing_glossary)


class NovelTermDetector:
    """Detect novel and semantically significant terms in text."""

    def __init__(self, glossary: GlossaryStore | None = None) -> None:
        """Initialize detector.

        Args:
            glossary: Optional glossary store for existing term lookup.
        """
        self._glossary = glossary
        self._english_words: set[str] | None = None
        self._load_corpus()

    def _load_corpus(self) -> None:
        """Load NLTK words corpus."""
        if not NLTK_AVAILABLE:
            LOGGER.warning("NLTK not available, novel term detection disabled")
            return

        try:
            # Download words corpus if not present
            nltk.download("words", quiet=True)
            self._english_words = set(w.lower() for w in words.words())
            LOGGER.info("Loaded %d English words", len(self._english_words))
        except Exception as e:
            LOGGER.warning("Failed to load NLTK words corpus: %s", e)
            self._english_words = None

    def detect(self, text: str) -> DetectedTerms:
        """Detect novel and significant terms in text.

        Args:
            text: Text to analyze.

        Returns:
            DetectedTerms with categorized terms.
        """
        result = DetectedTerms()

        if self._english_words is None:
            return result

        # Track seen terms to avoid duplicates
        seen: set[str] = set()

        # Find sentence boundaries for mid-sentence detection
        sentences = re.split(r"[.!?]+", text)

        for sentence in sentences:
            sentence_tokens = re.findall(
                r"\b[A-Za-z][A-Za-z'-]*[A-Za-z]\b|\b[A-Za-z]\b", sentence
            )
            for i, token in enumerate(sentence_tokens):
                lower = token.lower()

                # Skip if already seen or common
                if lower in seen or lower in COMMON_IGNORE:
                    continue

                # Skip very short tokens
                if len(token) < 2:
                    continue

                # Check if already in glossary
                if self._glossary is not None:
                    entries = self._glossary.search(token, limit=1)
                    if entries and entries[0].term.lower() == lower:
                        if lower not in seen:
                            result.existing_glossary.append(token)
                            seen.add(lower)
                        continue

                # Check if in English dictionary
                is_english = lower in self._english_words

                if not is_english:
                    # Novel term (not in dictionary)
                    result.novel.append(token)
                    seen.add(lower)
                elif i > 0 and token[0].isupper():
                    # Capitalized mid-sentence AND in dictionary = semantic jargon candidate
                    result.capitalized_common.append(token)
                    seen.add(lower)

        # Sort and deduplicate
        result.novel = sorted(set(result.novel), key=str.lower)
        result.capitalized_common = sorted(
            set(result.capitalized_common), key=str.lower
        )
        result.existing_glossary = sorted(set(result.existing_glossary), key=str.lower)

        return result


def format_detected_terms_xml(terms: DetectedTerms) -> str:
    """Format detected terms as XML for agent context.

    Args:
        terms: DetectedTerms to format.

    Returns:
        XML string for inclusion in context.
    """
    if terms.is_empty():
        return ""

    lines = ["<detected_terms>"]

    if terms.novel:
        lines.append(f"  <novel>{', '.join(terms.novel)}</novel>")

    if terms.capitalized_common:
        lines.append(
            f"  <capitalized_common>{', '.join(terms.capitalized_common)}</capitalized_common>"
        )

    if terms.existing_glossary:
        lines.append(
            f"  <existing_glossary>{', '.join(terms.existing_glossary)}</existing_glossary>"
        )

    lines.append("</detected_terms>")
    lines.append("")
    lines.append("<guidance>")
    lines.append(
        "Pay special attention to common English words used with specific in-universe meanings."
    )
    lines.append(
        'Terms like "soul", "husk", "shell" may have domain-specific definitions worth capturing.'
    )
    lines.append("</guidance>")

    return "\n".join(lines)
