"""Reading question papers: positive and negative cases for every layout and marks format."""

import json

import pytest

from course_notes_rag.generation.llm import FakeLLM, LLMError
from course_notes_rag.ingestion.parser import AutoParser, PyMuPDFParser, group_ocr_lines
from course_notes_rag.models import BBox, ParsedDocument
from course_notes_rag.papers.extract import (
    apply_instruction, extract_paper, extract_with_llm, parse_metadata, PaperPart, read_paper,
    source_coverage, split_inline, take_trailing_tags,
)
from course_notes_rag.papers.samples import build_sample_papers
from tests.conftest import block


def paper(*texts, pdf_name="Paper.pdf"):
    blocks = [block("text", t, page=1, y=100.0 + i) for i, t in enumerate(texts)]
    return ParsedDocument(pdf_name, 1, [(595.0, 842.0)], blocks, engine="test")


def labels(extracted):
    return [q.label for q in extracted.questions]


# --- Inline splitting ----------------------------------------------------------------------


def test_split_inline_separates_questions_a_parser_joined():
    assert split_inline("1. Define entropy. (2) 2. State Bayes' theorem. (2)") == [
        "1. Define entropy. (2)", "2. State Bayes' theorem. (2)",
    ]


def test_split_inline_separates_sub_questions_and_or():
    assert split_inline("11. (a) Explain X. (8) OR (b) Compute Y. (8)") == [
        "11.", "(a) Explain X. (8)", "OR", "(b) Compute Y. (8)",
    ]


def test_split_inline_leaves_ordinary_text_alone():
    text = "Compare (a) Bagging (b) Boosting for the given data set"
    assert split_inline(text) == [text]
    assert split_inline("Evaluate A OR B for each row") == ["Evaluate A OR B for each row"]
    assert split_inline("The points are (1, 2) and (3, 4).") == ["The points are (1, 2) and (3, 4)."]


# --- Marks and tags ----------------------------------------------------------------------------


@pytest.mark.parametrize("text, marks", [
    ("Explain X. (8)", 8), ("Explain X. [10]", 10), ("Explain X. (5 Marks)", 5),
    ("Explain X. [8 M]", 8), ("Explain X - 6 marks", 6), ("Explain X. (2.5)", 2.5),
    ("Explain X and Y. (5+5)", 10), ("Explain X. [4 + 4 + 2 marks]", 10),
])
def test_marks_formats(text, marks):
    body, found, _co, _bl = take_trailing_tags(text)
    assert found == marks and body.startswith("Explain X") and "(" not in body


def test_course_outcome_and_bloom_tags_are_taken_off():
    assert take_trailing_tags("Explain X. [CO2] [K3] (8)") == ("Explain X.", 8, "2", "3")
    assert take_trailing_tags("Explain X. (8) CO1 BL4") == ("Explain X.", 8, "1", "4")
    assert take_trailing_tags("Explain X. 10 CO2 K2") == ("Explain X.", 10, "2", "2")


@pytest.mark.parametrize("text", [
    "Evaluate f(2)", "Find the L2", "Compute the sum of 3", "Name the 2 gates", "State Rule (0)",
    "Take the value 500 marks",
])
def test_text_that_only_looks_like_marks_stays(text):
    body, marks, co, bl = take_trailing_tags(text)
    assert (body, marks, co, bl) == (text, None, "", "")


@pytest.mark.parametrize("instruction, each, count, total", [
    ("(10 x 2 = 20 Marks)", 2, 10, 20), ("10 × 2 = 20", 2, 10, 20),
    ("Answer any FIVE questions. Each question carries 16 marks", 16, 5, None),
    ("Answer any 3", None, 3, None), ("(40 marks)", None, None, 40),
])
def test_part_instructions(instruction, each, count, total):
    part = PaperPart("A")
    apply_instruction(part, instruction)
    assert (part.each_marks, part.answer_count, part.stated_total) == (each, count, total)


# --- Text layout -----------------------------------------------------------------------------


def test_parts_numbers_and_marks():
    extracted = extract_paper(paper(
        "PART A (2 x 2 = 4 Marks)", "Answer ALL questions", "1. Define entropy.", "2. State Bayes' rule.",
        "PART B", "3. Explain the kernel trick in detail. (8)",
    ))
    assert labels(extracted) == ["1", "2", "3"]
    assert [q.part for q in extracted.questions] == ["A", "A", "B"]
    assert [q.marks for q in extracted.questions] == [2, 2, 8]
    assert extracted.parts[0].instructions.endswith("Answer ALL questions")
    assert extracted.warnings == []


def test_numbering_may_restart_in_each_part():
    extracted = extract_paper(paper(
        "PART A", "1. Define entropy. (2)", "2. Define gain. (2)",
        "PART B", "1. Explain ID3 in detail. (8)", "2. Explain C4.5 in detail. (8)",
    ))
    assert [(q.part, q.label) for q in extracted.questions] == [
        ("A", "1"), ("A", "2"), ("B", "1"), ("B", "2"),
    ]


def test_a_question_wrapping_over_lines_stays_one_question():
    extracted = extract_paper(paper("1. Explain the role of the learning rate", "in gradient descent. (5)"))
    assert labels(extracted) == ["1"]
    assert extracted.questions[0].text == "Explain the role of the learning rate in gradient descent."
    assert extracted.questions[0].marks == 5


def test_stem_becomes_context_for_sub_questions():
    extracted = extract_paper(paper(
        "1. Consider the training data below.", "(a) Compute the mean. (5)", "(b) Compute the variance. (5)",
    ))
    assert labels(extracted) == ["1(a)", "1(b)"]
    assert {q.context for q in extracted.questions} == {"Consider the training data below."}


def test_roman_parts_nest_under_letters_with_their_own_stem():
    extracted = extract_paper(paper(
        "1. Consider a network.", "(a) For the first layer:", "(i) Find the output. (3)",
        "(ii) Find the gradient. (3)", "(b) Explain why it trains. (4)",
    ))
    assert labels(extracted) == ["1(a)(i)", "1(a)(ii)", "1(b)"]
    assert extracted.questions[0].context == "Consider a network. For the first layer:"
    assert extracted.questions[2].context == "Consider a network.", "(b) must not inherit (a)'s stem"


def test_either_or_between_sub_questions():
    extracted = extract_paper(paper(
        "11. (a) Explain X in detail. (16)", "OR", "(b) Explain Y in detail. (16)",
        "12. (a) Explain Z in detail. (16)",
    ))
    assert [q.choice_group for q in extracted.questions] == ["11", "11", ""]


def test_either_or_between_whole_questions():
    extracted = extract_paper(paper("4. Explain X in detail. (10)", "OR", "5. Explain Y in detail. (10)"))
    assert [q.choice_group for q in extracted.questions] == ["4", "4"]
    assert [q.alternative for q in extracted.questions] == ["4", "5"]


def test_either_or_with_several_parts_on_each_side():
    extracted = extract_paper(paper(
        "6. (a) (i) Explain X now. (8)", "(ii) Explain Y now. (8)", "OR",
        "(b) (i) Explain Z now. (8)", "(ii) Explain W now. (8)",
    ))
    assert labels(extracted) == ["6(a)(i)", "6(a)(ii)", "6(b)(i)", "6(b)(ii)"]
    assert {q.choice_group for q in extracted.questions} == {"6"}
    assert [q.alternative for q in extracted.questions] == ["a", "a", "b", "b"]


def test_stem_marks_are_shared_and_flagged_as_estimated():
    extracted = extract_paper(paper(
        "1. Consider the data below. (10)", "(a) Find the mean value.", "(b) Find the median value.",
    ))
    assert [q.marks for q in extracted.questions] == [5, 5]
    assert all(q.marks_estimated for q in extracted.questions)


def test_sides_of_an_or_each_get_the_part_marks():
    extracted = extract_paper(paper(
        "PART B (1 x 10 = 10 Marks)", "1. (a) Explain X in detail.", "OR", "(b) Explain Y in detail.",
    ))
    assert [q.marks for q in extracted.questions] == [10, 10]
    assert not any(q.marks_estimated for q in extracted.questions)


def test_a_paper_wide_marks_rule_applies_without_parts():
    extracted = extract_paper(paper(
        "Each question carries 10 marks", "1. Explain X in detail.", "2. Explain Y in detail.",
    ))
    assert [q.marks for q in extracted.questions] == [10, 10]


def test_missing_marks_are_reported_not_invented():
    extracted = extract_paper(paper("1. Explain X in detail.", "2. Explain Y in detail. (5)"))
    assert [q.marks for q in extracted.questions] == [None, 5]
    assert extracted.warnings == ["1 question had no marks printed beside them."]


@pytest.mark.parametrize("noise", ["Page 1 of 2", "2 / 3", "*****", "All the best", "*** End of Question Paper ***"])
def test_page_furniture_is_not_part_of_a_question(noise):
    extracted = extract_paper(paper("1. Explain X in detail.", noise, "2. Explain Y in detail."))
    assert [q.text for q in extracted.questions] == ["Explain X in detail.", "Explain Y in detail."]


def test_a_lone_page_number_does_not_become_a_question():
    extracted = extract_paper(paper("1. Explain X in detail. (5)", "2", "2. Explain Y in detail. (5)"))
    assert labels(extracted) == ["1", "2"]


def test_numbers_in_text_are_not_mistaken_for_questions():
    extracted = extract_paper(paper("1. Sort the following numbers.", "3.2 and 4.5 are the values.", "2. Explain X fully."))
    assert labels(extracted) == ["1", "2"]
    assert "3.2 and 4.5" in extracted.questions[0].text


def test_a_note_inside_a_question_belongs_to_it():
    extracted = extract_paper(paper("1. Solve the system.", "Note: assume a unit step.", "2. Explain X fully."))
    assert "assume a unit step" in extracted.questions[0].text


def test_document_without_questions_gives_nothing():
    extracted = extract_paper(paper("Course Code: CS229", "Duration: 3 hours"))
    assert extracted.questions == []


# --- Table layout -------------------------------------------------------------------------------


def table_doc(markdown):
    blocks = [block("table", markdown, page=2)]
    return ParsedDocument("Table.pdf", 2, [(595.0, 842.0)] * 2, blocks, engine="test")


def test_question_table_with_every_column():
    extracted = extract_paper(table_doc(
        "|Q No .|Sub|Question|Marks|CO|BL|\n|---|---|---|---|---|---|\n"
        "|1|a|State the theorem.|4|1|1|\n|1|b|Compute the value.|6|2|3|\n|2||Explain X fully.|10|1|2|"
    ))
    assert labels(extracted) == ["1(a)", "1(b)", "2"]
    first = extracted.questions[0]
    assert (first.marks, first.course_outcome, first.bloom_hint, first.page) == (4, "1", "1", 2)


def test_table_with_or_rows_and_part_rows():
    extracted = extract_paper(table_doc(
        "|Q.No|Question|Marks|\n|---|---|---|\n|PART B|||\n|1 (a)|Explain X fully.|10|\n||OR||\n"
        "|1 (b)|Explain Y fully.|10|"
    ))
    assert labels(extracted) == ["1(a)", "1(b)"]
    assert {q.part for q in extracted.questions} == {"B"}
    assert {q.choice_group for q in extracted.questions} == {"1"}


def test_marks_inside_the_question_cell_are_found():
    extracted = extract_paper(table_doc("|Q.No|Question|\n|---|---|\n|1|Explain X fully. (10)|"))
    assert extracted.questions[0].marks == 10


def test_table_without_a_usable_header_is_inferred():
    extracted = extract_paper(table_doc(
        "|1|Explain the kernel trick in detail for this course|8|\n|---|---|---|\n"
        "|2|Explain the margin in full detail for this course|8|\n"
        "|3|Compute the gradient norm for this small network|4|"
    ))
    assert labels(extracted) == ["1", "2", "3"]
    assert [q.marks for q in extracted.questions] == [8, 8, 4]


def test_data_table_inside_a_question_stays_in_that_question():
    doc = ParsedDocument("P.pdf", 1, [(595.0, 842.0)], [
        block("text", "1. Compute the mean of the data below. (5)", y=100.0),
        block("table", "|Kernel|Score|\n|---|---|\n|Linear|0.9|", y=120.0),
    ], engine="test")
    extracted = extract_paper(doc)
    assert labels(extracted) == ["1"]
    assert "Kernel: Linear; Score: 0.9." in extracted.questions[0].text


def test_exam_header_box_is_not_read_as_questions():
    doc = ParsedDocument("P.pdf", 1, [(595.0, 842.0)], [
        block("table", "|Course Code|Max Marks|\n|---|---|\n|CS229|Maximum Marks: 100|", y=50.0),
        block("text", "1. Explain X in detail. (5)", y=120.0),
    ], engine="test")
    extracted = extract_paper(doc)
    assert labels(extracted) == ["1"] and extracted.max_marks == 100


# --- Metadata --------------------------------------------------------------------------------------


@pytest.mark.parametrize("header, exam_type", [
    ("Final Assessment Test", "FAT"), ("Winter 2024-25 FAT", "FAT"),
    ("Continuous Assessment Test - II", "CAT-2"), ("CAT-1", "CAT-1"), ("CAT 2", "CAT-2"),
    ("End Semester Examination", "End-Semester"), ("Mid-Term Test", "Mid-Semester"),
    ("Model Question Paper", "Model paper"), ("Supplementary Examination", "Supplementary"),
])
def test_exam_type(header, exam_type):
    assert parse_metadata(header, "paper.pdf")["exam_type"] == exam_type


@pytest.mark.parametrize("header, year, term", [
    ("Winter Semester 2024-25", 2024, "Winter Semester 2024-25"), ("Nov/Dec 2023", 2023, "Nov/Dec 2023"),
    ("Date: 12.05.2025", 2025, None), ("Fall 2023", 2023, "Fall 2023"), ("no date here", None, None),
])
def test_year_and_term(header, year, term):
    meta = parse_metadata(header, "paper.pdf")
    assert (meta["year"], meta["term"]) == (year, term)


def test_max_marks_and_duration():
    meta = parse_metadata("Time: 3 Hours   Max. Marks: 100", "paper.pdf")
    assert (meta["max_marks"], meta["duration"]) == (100, "3 Hours")
    assert parse_metadata("Duration: 90 minutes", "paper.pdf")["duration"] == "90 minutes"
    empty = parse_metadata("", "paper.pdf")
    assert (empty["max_marks"], empty["duration"]) == (None, None)


def test_file_name_fills_in_what_the_header_leaves_out():
    meta = parse_metadata("", "FAT_Winter_2023.pdf")
    assert (meta["exam_type"], meta["year"], meta["title"]) == ("FAT", 2023, "FAT Winter 2023")


def test_title_falls_back_to_the_file_name():
    assert parse_metadata("", "my_paper.pdf")["title"] == "my paper"


# --- The language-model fallback ---------------------------------------------------------------


FREE_FORM = "Kernels and margins. What is a kernel. Why does the margin matter."


def llm_reply(*questions):
    return json.dumps({"questions": [
        {"number": str(i), "sub": "", "part": "", "marks": None, "text": q}
        for i, q in enumerate(questions, start=1)
    ]})


def test_llm_extraction_keeps_only_what_the_paper_contains():
    doc = paper(FREE_FORM)
    fake = FakeLLM([llm_reply("What is a kernel?", "Why does the margin matter?", "Describe quantum entanglement fully.")])
    extracted = read_paper(doc, fake)
    assert [q.text for q in extracted.questions] == ["What is a kernel?", "Why does the margin matter?"]
    assert any("dropped" in w for w in extracted.warnings)


def test_rules_are_enough_for_a_well_formed_paper():
    fake = FakeLLM([])
    extracted = read_paper(paper("1. Explain X fully.", "2. Explain Y fully.", "3. Explain Z fully."), fake)
    assert labels(extracted) == ["1", "2", "3"] and fake.calls == []


def test_llm_garbage_leaves_the_rule_result():
    extracted = read_paper(paper(FREE_FORM), FakeLLM(["this is not json"]))
    assert extracted.questions == []


def test_llm_failure_leaves_the_rule_result():
    class Broken:
        def chat(self, *_a, **_k):
            raise LLMError("down")

    assert read_paper(paper(FREE_FORM), Broken()).questions == []


def test_llm_never_replaces_a_better_rule_result():
    doc = paper("1. Explain X fully.", "2. Explain Y fully.")
    current = extract_paper(doc)
    fake = FakeLLM([llm_reply("Explain X fully.")])
    assert labels(extract_with_llm(doc, current, fake)) == ["1", "2"]


def test_llm_marks_outside_the_valid_range_are_ignored():
    fake = FakeLLM([json.dumps({"questions": [
        {"number": "Q7", "sub": "(a)", "part": "Part B", "marks": 900, "text": "What is a kernel?"},
    ]})])
    extracted = extract_with_llm(paper(FREE_FORM), extract_paper(paper(FREE_FORM)), fake)
    q = extracted.questions[0]
    assert (q.number, q.sub, q.part, q.marks) == ("7", "a", "B", None)


def test_source_coverage():
    assert source_coverage("kernel margin", {"kernel", "margin"}) == 1.0
    assert source_coverage("kernel quantum", {"kernel"}) == 0.5
    assert source_coverage("", {"kernel"}) == 0.0


# --- Real PDFs through the shared parser ------------------------------------------------------------


@pytest.fixture(scope="module")
def sample_papers(tmp_path_factory):
    return build_sample_papers(tmp_path_factory.mktemp("papers"))


@pytest.fixture(scope="module")
def parser():
    return PyMuPDFParser()


def test_digital_paper_end_to_end(sample_papers, parser):
    doc = parser.parse(sample_papers["End_Semester_2023.pdf"], keep_markers=True)
    extracted = extract_paper(doc)
    assert len(extracted.questions) == 11
    assert (extracted.exam_type, extracted.year, extracted.max_marks, extracted.duration) == (
        "End-Semester", 2023, 40, "3 Hours",
    )
    assert extracted.title == "End-Semester Nov/Dec 2023"
    assert sum(q.marks for q in extracted.questions) == 70, "both sides of each OR are kept"


def test_table_paper_end_to_end(sample_papers, parser):
    doc = parser.parse(sample_papers["CAT1_Fall_2025.pdf"], keep_markers=True)
    assert labels(extract_paper(doc)) == ["1(a)", "1(b)", "2", "3"]


def test_scanned_paper_end_to_end(sample_papers, parser):
    doc = parser.parse(sample_papers["End_Semester_May_2022_Scan.pdf"], keep_markers=True)
    extracted = extract_paper(doc)
    assert labels(extracted) == ["1", "2", "3"]
    assert [q.marks for q in extracted.questions] == [5, 5, 10]


def test_without_keep_markers_the_notes_path_is_unchanged(sample_papers, parser):
    doc = parser.parse(sample_papers["End_Semester_2023.pdf"])
    items = [b.text for b in doc.blocks if b.type == "list_item"]
    assert any(t.startswith("Define the functional margin") for t in items), "markers are stripped"


def test_keep_markers_keeps_numbers_and_line_breaks(sample_papers, parser):
    doc = parser.parse(sample_papers["End_Semester_2023.pdf"], keep_markers=True)
    text = "\n".join(b.text for b in doc.blocks)
    assert "1. Define the functional margin" in text and "(b) Explain with a neat diagram" in text


def test_keep_markers_turns_off_formula_detection(tmp_path, parser):
    import pymupdf

    pdf = pymupdf.open()
    pdf.new_page().insert_text((72, 72), "4. Find P(A) = 0.3 + 0.2 (5)", fontsize=11)
    path = tmp_path / "formula.pdf"
    pdf.save(path)
    pdf.close()
    assert {b.type for b in parser.parse(path, keep_markers=True).blocks} == {"text"}
    assert {b.type for b in parser.parse(path).blocks} == {"formula"}


def test_ocr_grouping_can_keep_lines():
    lines = [(BBox(1, 0, i * 20.0, 100, i * 20.0 + 10), f"line {i}") for i in range(3)]
    assert group_ocr_lines(lines, 1)[0].text == "line 0 line 1 line 2"
    assert group_ocr_lines(lines, 1, keep_lines=True)[0].text == "line 0\nline 1\nline 2"


class _Strict:
    """A parser that predates ``keep_markers``."""

    name = "strict"

    def __init__(self):
        self.calls = []

    def parse(self, path, *, pdf_name=None, **options):
        self.calls.append(options)
        return ParsedDocument("x.pdf", 1, [], [], engine="strict")


def test_auto_parser_passes_the_option_only_when_asked():
    primary = _Strict()
    auto = AutoParser(primary, _Strict())
    auto.parse("x.pdf")
    auto.parse("x.pdf", keep_markers=True)
    assert primary.calls == [{}, {"keep_markers": True}]
