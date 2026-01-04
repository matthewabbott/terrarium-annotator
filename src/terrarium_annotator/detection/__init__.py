"""Novel term detection for auto-glossary suggestions."""

from terrarium_annotator.detection.detector import (
    DetectedTerms,
    NovelTermDetector,
    format_detected_terms_xml,
)

__all__ = [
    "DetectedTerms",
    "NovelTermDetector",
    "format_detected_terms_xml",
]
