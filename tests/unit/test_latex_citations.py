import re

import pytest

from course_notes_rag.generation.citations import format_citation, parse_citations, verify_citations
from course_notes_rag.generation.latex import math_segments, validate_latex
from course_notes_rag.models import BBox, Block, RetrievedChunk


@pytest.mark.parametrize(
    "text",
    [
        "Inline $x^2 + y^2 = r^2$ works.",
        "Display:\n$$\n\\frac{1}{2}\\Vert w \\Vert^2\n$$",
        r"$\begin{aligned} a &= b \end{aligned}$",
        r"$\left( \frac{a}{b} \right)$",
        r"Costs \$5 and has no math.",
        "No math at all.",
    ],
)
def test_valid_latex(text):
    assert validate_latex(text) == []


@pytest.mark.parametrize(
    ("text", "fragment"),
    [
        ("Unclosed $x^2", "Unclosed $"),
        ("Unclosed $$x^2", "Unclosed $$"),
        ("Braces $\\frac{1}{2$", "Unbalanced braces"),
        ("Extra $a}$", "Unbalanced braces"),
        (r"$\begin{matrix} a \end{pmatrix}$", "Mismatched"),
        (r"$\begin{matrix} a$", "Unclosed \\begin"),
        (r"$\left( a$", "\\left"),
        ("Empty $$ $$ block", "Empty"),
    ],
)
def test_invalid_latex(text, fragment):
    errors = validate_latex(text)
    assert errors and any(fragment in e for e in errors), errors


def test_math_segments_extracts_inline_and_display():
    segs, errs = math_segments("a $x$ b $$y$$ c")
    assert segs == ["x", "y"] and errs == []


def _chunk(pdf="Lecture_04_SVMs.pdf", start=2, end=3):
    blocks = [Block("text", "kkt", p, BBox(p, 10, 10, 100, 30)) for p in range(start, end + 1)]
    return RetrievedChunk("p1", "d1", "c1", pdf, "Lecture 4", "KKT", blocks, start, end,
                          highlight_bboxes=[BBox(2, 1, 2, 3, 4)])


def test_parse_citation_formats():
    text = (
        "A [Source: Lecture_04.pdf, p. 12]. B [Source: L.pdf, pp. 3-4]. "
        "C [Source: A.pdf, p. 1; Source: B.pdf, p. 2]. D [source: X.pdf, page 7]. E [Source: broken]."
    )
    cits = parse_citations(text)
    assert [(c.pdf_name, c.page, c.page_end) for c in cits if c.reason is None] == [
        ("Lecture_04.pdf", 12, 12), ("L.pdf", 3, 4), ("A.pdf", 1, 1), ("B.pdf", 2, 2), ("X.pdf", 7, 7)
    ]
    assert cits[-1].reason == "malformed"
    assert format_citation("a.pdf", 3) == "[Source: a.pdf, p. 3]"


def test_verify_valid_and_invalid_citations():
    answer = (
        "The KKT conditions include complementary slackness for every constraint "
        "[Source: Lecture_04_SVMs.pdf, p. 2]. Slater's condition guarantees strong duality here "
        "[Source: lecture 04 svms, p. 3]. The margin is maximized by the optimal classifier "
        "[Source: Lecture_04_SVMs.pdf, p. 9]. This is cited to a file that was never retrieved "
        "[Source: Other.pdf, p. 2]."
    )
    report = verify_citations(answer, [_chunk()])
    assert [c.valid for c in report.citations] == [True, True, False, False]
    assert report.citations[2].reason == "page_not_in_context"
    assert report.citations[3].reason == "unknown_source"
    assert report.citations[0].bboxes == [BBox(2, 1, 2, 3, 4)]
    assert report.citations[1].bboxes[0].page == 3
    assert report.citations[0].document_id == "d1"
    assert not report.grounded and report.valid_count == 2 and report.invalid_count == 2


def test_uncited_supported_sentences_get_inferred_citations():
    blocks = [
        Block("text", "Complementary slackness forces inactive constraints to have zero multipliers.", 2,
              BBox(2, 10, 60, 500, 90)),
        Block("text", "Support vectors are the training points with functional margin exactly one.", 3,
              BBox(3, 10, 60, 500, 90)),
    ]
    chunk = RetrievedChunk("p", "d", "c", "L.pdf", "L", "KKT", blocks, 2, 3)
    answer = (
        "Inactive constraints have zero multipliers by complementary slackness [Source: L.pdf, p. 2]. "
        "Support vectors are the points whose functional margin is exactly one.\n\n"
        "Kernels were invented by aliens on the planet Zorg in ancient times."
    )
    report = verify_citations(answer, [chunk])
    assert report.annotated_answer == (
        "Inactive constraints have zero multipliers by complementary slackness [Source: L.pdf, p. 2]. "
        "Support vectors are the points whose functional margin is exactly one [Source: L.pdf, p. 3].\n\n"
        "Kernels were invented by aliens on the planet Zorg in ancient times."
    )
    assert [c.inferred for c in report.citations] == [False, True]
    assert report.citations[1].valid and report.citations[1].bboxes == [BBox(3, 10, 60, 500, 90)]
    assert report.uncited_sentences == ["Kernels were invented by aliens on the planet Zorg in ancient times."]
    assert report.inferred_count == 1 and not report.grounded
    plain = verify_citations(answer, [chunk], complete=False)
    assert plain.annotated_answer is None and len(plain.uncited_sentences) == 2


def test_uncited_claims_are_reported():
    answer = (
        "The support vectors are the points with functional margin exactly one "
        "[Source: Lecture_04_SVMs.pdf, p. 2].\n\n"
        "This sentence makes a long factual claim without any citation at all.\n\n"
        "$$\nx = 1\n$$\n\n## Heading only\n\nShort line."
    )
    report = verify_citations(answer, [_chunk()], complete=False)
    assert report.claim_count == 2
    assert report.uncited_sentences == [
        "This sentence makes a long factual claim without any citation at all."
    ]
    assert report.coverage == pytest.approx(0.5)


def test_fully_grounded_answer():
    answer = (
        "Complementary slackness forces inactive constraints to have zero multipliers. "
        "[Source: Lecture_04_SVMs.pdf, p. 2]"
    )
    report = verify_citations(answer, [_chunk()])
    assert report.grounded and report.coverage == 1.0
    assert report.to_dict()["grounded"] is True


def test_citation_highlights_the_supporting_paragraph():
    blocks = [
        Block("text", "Slater's condition gives strong duality for convex problems.", 2, BBox(2, 10, 10, 500, 40)),
        Block("text", "Complementary slackness forces inactive constraints to have zero multipliers.", 2,
              BBox(2, 10, 60, 500, 90)),
    ]
    chunk = RetrievedChunk("p", "d", "c", "L.pdf", "L", "KKT", blocks, 2, 2,
                           highlight_bboxes=[BBox(2, 0, 0, 600, 800)])
    answer = "Inactive constraints have zero multipliers by complementary slackness [Source: L.pdf, p. 2]."
    report = verify_citations(answer, [chunk])
    assert report.citations[0].bboxes == [BBox(2, 10, 60, 500, 90)]
    vague = verify_citations("Yes [Source: L.pdf, p. 2].", [chunk])
    assert vague.citations[0].bboxes == [BBox(2, 0, 0, 600, 800)]  # falls back to the matched region


def test_citation_to_the_wrong_page_is_flagged_with_the_right_page():
    blocks = [
        Block("text", "Exploding gradients are handled with gradient clipping before each update.", 1,
              BBox(1, 10, 10, 500, 40)),
        Block("text", "The forget gate decides what information to erase from the cell state.", 2,
              BBox(2, 10, 60, 500, 90)),
    ]
    chunk = RetrievedChunk("p", "d", "c", "R.pdf", "L", "RNN", blocks, 1, 2)
    answer = "Exploding gradients are handled with gradient clipping [Source: R.pdf, p. 2]."
    report = verify_citations(answer, [chunk])
    cit = report.citations[0]
    assert cit.valid and not cit.page_supported and cit.suggested_page == 1
    assert cit.bboxes == [BBox(1, 10, 10, 500, 40)]
    assert report.page_mismatch_count == 1 and not report.grounded
    right = verify_citations("Exploding gradients are handled with gradient clipping [Source: R.pdf, p. 1].", [chunk])
    assert right.citations[0].page_supported and right.citations[0].suggested_page is None
    assert right.to_dict()["page_mismatch_count"] == 0


def _display_blocks(text):
    return re.findall(r"\$\$.*?\$\$", text, re.S)


def test_completion_never_cites_inside_display_math():
    # Docling writes formulas with spaced tokens, so an equation line matches its own block well.
    formula = "\\alpha _ { i } { ^ { * } } \\, g _ { i } ( w { ^ { * } } ) \\, = \\, 0 , \\, i = 1 , \\, \\dots , \\, k"
    blocks = [
        Block("text", "Complementary slackness requires the product of multiplier and constraint to vanish.", 2,
              BBox(2, 10, 60, 500, 90)),
        Block("formula", formula, 2, BBox(2, 10, 100, 500, 130)),
    ]
    chunk = RetrievedChunk("p", "d", "c", "L.pdf", "L", "KKT", blocks, 2, 3)
    answer = (
        "Complementary slackness requires that the product of each multiplier and its constraint is zero:\n"
        f"$$\n{formula}\n$$\n"
        "This implies that $\\alpha_i^* > 0$ only when the constraint is active [Source: L.pdf, p. 2]."
    )
    report = verify_citations(answer, [chunk])
    text = report.annotated_answer or answer
    assert _display_blocks(text) == [f"$$\n{formula}\n$$"]
    assert report.uncited_sentences == [] and report.claim_count == 1
    assert [c.inferred for c in report.citations] == [False]


def test_citation_written_inside_display_math_is_moved_after_it():
    answer = (
        "The dual objective is a concave function of the multipliers:\n"
        "$$\nW(\\alpha) = \\sum_i \\alpha_i [Source: Lecture_04_SVMs.pdf, p. 2]\n$$\n"
        "It is maximized subject to the box constraints [Source: Lecture_04_SVMs.pdf, p. 3]."
    )
    report = verify_citations(answer, [_chunk()])
    assert report.annotated_answer == (
        "The dual objective is a concave function of the multipliers:\n"
        "$$\nW(\\alpha) = \\sum_i \\alpha_i\n$$\n[Source: Lecture_04_SVMs.pdf, p. 2]\n"
        "It is maximized subject to the box constraints [Source: Lecture_04_SVMs.pdf, p. 3]."
    )
    assert [c.valid for c in report.citations] == [True, True]
    one_line = verify_citations("We get $$a = b [Source: Lecture_04_SVMs.pdf, p. 2]$$", [_chunk()], complete=False)
    assert one_line.annotated_answer == "We get $$a = b$$\n[Source: Lecture_04_SVMs.pdf, p. 2]"


def test_display_math_without_citations_is_left_alone():
    answer = "The margin is defined below [Source: Lecture_04_SVMs.pdf, p. 2].\n$$\n\\gamma = 1 / \\Vert w \\Vert\n$$"
    report = verify_citations(answer, [_chunk()])
    assert report.annotated_answer is None and report.citations[0].valid


def test_no_citations_is_not_grounded():
    report = verify_citations("Some words that are a long uncited claim here.", [_chunk()])
    assert not report.grounded and report.citations == []
