"""Tests for novel term detection."""

import pytest

from terrarium_annotator.detection import (
    DetectedTerms,
    NovelTermDetector,
    format_detected_terms_xml,
)


class TestDetectedTerms:
    def test_empty_by_default(self):
        terms = DetectedTerms()
        assert terms.is_empty()
        assert terms.novel == []
        assert terms.capitalized_common == []
        assert terms.existing_glossary == []

    def test_not_empty_with_novel(self):
        terms = DetectedTerms(novel=["Zaahir"])
        assert not terms.is_empty()

    def test_not_empty_with_capitalized_common(self):
        terms = DetectedTerms(capitalized_common=["Soul"])
        assert not terms.is_empty()


class TestNovelTermDetector:
    @pytest.fixture
    def detector(self):
        """Create detector without glossary."""
        return NovelTermDetector(glossary=None)

    def test_detects_novel_terms(self, detector):
        """Made-up words should be detected as novel."""
        text = "The Zaahir practiced Vys manipulation."
        terms = detector.detect(text)

        # These should be flagged as novel (not in dictionary)
        assert "Zaahir" in terms.novel or "Vys" in terms.novel

    def test_detects_capitalized_common_mid_sentence(self, detector):
        """Common words capitalized mid-sentence should be flagged."""
        text = "He found the Soul Shard in the ancient temple."
        terms = detector.detect(text)

        # "Soul" is capitalized mid-sentence and is a common word
        # Should be flagged as semantic jargon candidate
        assert "Soul" in terms.capitalized_common or "Shard" in terms.capitalized_common

    def test_ignores_sentence_start_capitals(self, detector):
        """Words at sentence start should not be flagged as capitalized_common."""
        text = "Soul is important. The temple was old."
        terms = detector.detect(text)

        # "Soul" at sentence start should not be flagged
        # (it's at position 0 in its sentence)
        assert "Soul" not in terms.capitalized_common

    def test_ignores_common_words(self, detector):
        """Very common words should be ignored entirely."""
        text = "The quick brown fox jumps over the lazy dog."
        terms = detector.detect(text)

        # "the", "over" etc. should not appear anywhere
        assert "the" not in str(terms.novel).lower()
        assert "over" not in str(terms.novel).lower()

    def test_deduplicates_results(self, detector):
        """Same term appearing multiple times should only appear once."""
        text = "Vys is powerful. The Vys flows through everything. More Vys."
        terms = detector.detect(text)

        # Count occurrences
        if "Vys" in terms.novel:
            assert terms.novel.count("Vys") == 1

    def test_empty_text_returns_empty_terms(self, detector):
        """Empty text should return empty terms."""
        terms = detector.detect("")
        assert terms.is_empty()

    def test_handles_punctuation(self, detector):
        """Should handle punctuation properly."""
        text = "The Zaahir's power (known as Vys) is dangerous!"
        terms = detector.detect(text)

        # Should detect these despite surrounding punctuation
        novel_lower = [t.lower() for t in terms.novel]
        assert "zaahir" in novel_lower or "vys" in novel_lower


class TestFormatDetectedTermsXml:
    def test_empty_terms_returns_empty_string(self):
        terms = DetectedTerms()
        xml = format_detected_terms_xml(terms)
        assert xml == ""

    def test_formats_novel_terms(self):
        terms = DetectedTerms(novel=["Zaahir", "Vys"])
        xml = format_detected_terms_xml(terms)

        assert "<detected_terms>" in xml
        assert "<novel>Zaahir, Vys</novel>" in xml
        assert "</detected_terms>" in xml
        assert "<guidance>" in xml

    def test_formats_capitalized_common(self):
        terms = DetectedTerms(capitalized_common=["Soul", "Husk"])
        xml = format_detected_terms_xml(terms)

        # Terms are joined in provided order
        assert "<capitalized_common>Soul, Husk</capitalized_common>" in xml

    def test_formats_existing_glossary(self):
        terms = DetectedTerms(existing_glossary=["Soma"])
        xml = format_detected_terms_xml(terms)

        assert "<existing_glossary>Soma</existing_glossary>" in xml

    def test_includes_guidance(self):
        terms = DetectedTerms(novel=["Zaahir"])
        xml = format_detected_terms_xml(terms)

        assert "Pay special attention" in xml
        assert "domain-specific definitions" in xml
