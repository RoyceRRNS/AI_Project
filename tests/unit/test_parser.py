import pytest

from course_notes_rag.ingestion.parser import (
    AutoParser, ParseError, PyMuPDFParser, build_parser, detect_slides, group_ocr_lines,
    infer_heading_level, looks_like_formula, unicode_math_to_latex,
)
from course_notes_rag.models import BBox, ParsedDocument
from tests.conftest import RNN, SCAN, SLIDES, SVM


@pytest.fixture(scope="module")
def parser():
    return PyMuPDFParser()


@pytest.fixture(scope="module")
def svm(parser, sample_pdfs):
    return parser.parse(sample_pdfs[SVM])


def test_headings_and_levels(svm):
    headings = {b.text: b.level for b in svm.blocks if b.is_heading}
    assert headings["Lecture 4: Support Vector Machines"] == 1
    assert headings["2 Margins"] == 2
    assert headings["2.1 Functional Margin"] == 3
    assert headings["3.2 KKT Conditions"] == 3
    assert svm.title == "Lecture 4: Support Vector Machines"


def test_formulas_become_latex(svm):
    formulas = [b.text for b in svm.blocks if b.type == "formula"]
    assert any(r"\gamma" in f for f in formulas)
    assert any(r"\alpha" in f and "= 0" in f for f in formulas)
    for b in svm.blocks:
        if b.type == "formula":
            assert b.to_markdown().startswith("$$") and b.to_markdown().endswith("$$")


def test_tables_are_markdown_with_rows_and_columns(svm):
    tables = [b for b in svm.blocks if b.type == "table"]
    assert len(tables) == 1
    lines = tables[0].text.splitlines()
    assert lines[0].count("|") == 4 and "Kernel" in lines[0] and "Feature space" in lines[0]
    assert set(lines[1].replace("|", "")) <= {"-", ":"}
    assert len(lines) == 6
    assert any("Gaussian (RBF)" in ln and "Infinite-dimensional" in ln for ln in lines)
    # Text inside the table must not be duplicated as separate prose blocks.
    assert not any(b.type == "text" and b.text.startswith("Polynomial") for b in svm.blocks)


def test_every_block_has_page_and_bbox_within_page(svm):
    assert svm.num_pages == len(svm.page_sizes) >= 2
    for b in svm.blocks:
        w, h = svm.page_sizes[b.page - 1]
        assert b.bbox is not None and b.bbox.page == b.page
        assert 0 <= b.bbox.x0 < b.bbox.x1 <= w + 1 and 0 <= b.bbox.y0 < b.bbox.y1 <= h + 1
    pages = [b.page for b in svm.blocks]
    assert pages == sorted(pages)


def test_markdown_export(svm):
    md = svm.to_markdown()
    assert "# Lecture 4: Support Vector Machines" in md
    assert "### 3.2 KKT Conditions" in md
    assert "$$" in md and "|---|" in md


def test_lists_are_detected(parser, sample_pdfs):
    rnn = parser.parse(sample_pdfs[RNN])
    items = [b.text for b in rnn.blocks if b.type == "list_item"]
    assert len(items) == 4 and items[0].startswith("The forget gate")


def test_slides_are_detected(parser, sample_pdfs):
    slides = parser.parse(sample_pdfs[SLIDES])
    assert slides.is_slides
    titles = [b.text for b in slides.blocks if b.is_heading]
    assert "Bayes' Theorem" in titles and "Independence" in titles
    assert len({b.page for b in slides.blocks}) == slides.num_pages == 7


def test_scanned_handwritten_page_is_ocred(parser, sample_pdfs):
    scan = parser.parse(sample_pdfs[SCAN])
    text = " ".join(b.text for b in scan.blocks).lower()
    assert "learning rate" in text
    assert "too large" in text and "diverge" in text
    assert all(b.bbox is not None and b.page == 1 for b in scan.blocks)


def test_ocr_can_be_disabled(sample_pdfs):
    assert PyMuPDFParser(ocr=False).parse(sample_pdfs[SCAN]).blocks == []


def test_pdf_name_override(parser, sample_pdfs):
    assert parser.parse(sample_pdfs[SVM], pdf_name="Custom.pdf").pdf_name == "Custom.pdf"


@pytest.mark.parametrize(
    ("name", "message"),
    [
        ("not_a_pdf.pdf", "not a"),
        ("empty.pdf", "not a"),
        ("encrypted.pdf", "password"),
        ("truncated.pdf", ""),
    ],
)
def test_invalid_files_raise_parse_error(parser, invalid_pdfs, name, message):
    try:
        doc = parser.parse(invalid_pdfs[name])
    except ParseError as exc:
        assert message in str(exc)
    else:  # a truncated file may open but must yield nothing usable
        assert name == "truncated.pdf" and doc.blocks == []


def test_missing_file(parser, tmp_path):
    with pytest.raises(ParseError, match="not found"):
        parser.parse(tmp_path / "nope.pdf")


def test_blank_pdf_has_no_blocks(parser, invalid_pdfs):
    assert parser.parse(invalid_pdfs["blank.pdf"]).blocks == []


def test_unicode_math_to_latex():
    assert unicode_math_to_latex("α ≤ β") == r"\alpha \leq \beta"
    assert unicode_math_to_latex("∇f = 0") == r"\nabla f = 0"
    assert unicode_math_to_latex("plain") == "plain"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("γ̂ = y (wᵀx + b)", True),
        ("P(A | B) = P(A ∩ B) / P(B)", True),
        ("x = 1", True),
        ("CS229-lite · Machine Learning Foundations · Lecture notes", False),
        ("The margin is the distance between the hyperplane and the closest point = large", False),
        ("Plain sentence without math.", False),
        ("", False),
    ],
)
def test_looks_like_formula(text, expected):
    assert looks_like_formula(text, set()) is expected


def test_math_font_alone_is_not_enough_without_relation():
    assert looks_like_formula("x + y", {"CMMI10"}) is False
    assert looks_like_formula("x + y = z", {"CMMI10"}) is True


@pytest.mark.parametrize(
    ("text", "fallback", "expected"),
    [("Lecture 4: SVMs", 3, 1), ("2 Margins", 1, 2), ("2.1 Functional", 1, 3),
     ("1.2.3 Deep", 1, 4), ("Overview", 2, 2), ("Overview", 9, 6), ("Overview", 0, 1)],
)
def test_infer_heading_level(text, fallback, expected):
    assert infer_heading_level(text, fallback) == expected


def test_detect_slides():
    assert detect_slides([(720, 540)] * 5)
    assert not detect_slides([(595, 842)] * 5)
    assert not detect_slides([])


def test_group_ocr_lines_builds_paragraphs():
    lines = [
        (BBox(1, 10, 10, 300, 40), "My Notes"),
        (BBox(1, 10, 50, 300, 70), "If alpha is too large, it can"),
        (BBox(1, 10, 80, 300, 100), "diverge."),
        (BBox(1, 10, 110, 300, 130), "New idea here."),
    ]
    blocks = group_ocr_lines(lines, 1)
    assert [b.type for b in blocks] == ["heading", "text", "text"]
    assert blocks[1].text == "If alpha is too large, it can diverge."
    assert blocks[1].bbox == BBox(1, 10, 50, 300, 100)
    assert group_ocr_lines([], 1) == []


class _Boom:
    name = "boom"

    def __init__(self, exc):
        self.exc = exc

    def parse(self, path, *, pdf_name=None):
        raise self.exc


def test_auto_parser_falls_back(sample_pdfs):
    for exc in (ImportError("no docling"), ParseError("Docling could not convert x")):
        doc = AutoParser(_Boom(exc), PyMuPDFParser()).parse(sample_pdfs[SVM])
        assert isinstance(doc, ParsedDocument) and doc.engine == "pymupdf (fallback)"


def test_auto_parser_does_not_hide_invalid_pdfs(invalid_pdfs):
    auto = AutoParser(_Boom(ParseError("x is not a readable PDF")), PyMuPDFParser())
    with pytest.raises(ParseError):
        auto.parse(invalid_pdfs["not_a_pdf.pdf"])


def test_build_parser_engines():
    assert build_parser("pymupdf").name == "pymupdf"
    assert build_parser("docling").name == "docling"
    assert build_parser("auto").name == "docling+pymupdf"
