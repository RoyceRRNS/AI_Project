"""The labels every part of the past-papers feature agrees on."""

from __future__ import annotations

from typing import Literal

QuestionType = Literal[
    "definition", "explain_compare", "derivation", "numerical", "algorithm", "diagram", "case_based"
]
BloomLevel = Literal["Remember", "Understand", "Apply", "Analyze", "Evaluate", "Create"]

QUESTION_TYPES: tuple[str, ...] = (
    "definition", "explain_compare", "derivation", "numerical", "algorithm", "diagram", "case_based",
)
BLOOM_LEVELS: tuple[str, ...] = ("Remember", "Understand", "Apply", "Analyze", "Evaluate", "Create")

TYPE_LABELS: dict[str, str] = {
    "definition": "Definition / short theory",
    "explain_compare": "Explain / compare",
    "derivation": "Derivation / proof",
    "numerical": "Numerical / problem solving",
    "algorithm": "Apply / trace an algorithm",
    "diagram": "Diagram",
    "case_based": "Case-based",
}

# What a student should do at the desk for each question type.
PRACTICE_MODES: dict[str, str] = {
    "definition": "theory",
    "explain_compare": "theory",
    "derivation": "derivations",
    "numerical": "numericals",
    "algorithm": "algorithm_tracing",
    "diagram": "diagrams",
    "case_based": "case_studies",
}
PRACTICE_LABELS: dict[str, str] = {
    "theory": "Theory",
    "derivations": "Derivations",
    "numericals": "Numericals",
    "algorithm_tracing": "Algorithm tracing",
    "diagrams": "Diagrams",
    "case_studies": "Case studies",
}

UNMAPPED_MODULE = "Not in your notes"
