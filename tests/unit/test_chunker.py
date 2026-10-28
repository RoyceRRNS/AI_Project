import re

import pytest

from course_notes_rag.ingestion.chunker import HierarchicalChunker, contextual_prefix
from course_notes_rag.ingestion.parser import PyMuPDFParser
from course_notes_rag.text_utils import approx_token_count as count
from tests.conftest import RNN, SLIDES, SVM, block, make_doc

SENT = "The kernel trick replaces inner products with kernel evaluations in feature space. "


def chunk(doc, **kw):
    return HierarchicalChunker(**kw).chunk(doc, document_id="d1", course_id="c1", course_name="CS229")


def paragraphs(n, sentence=SENT):
    return sentence * n


def unique_paragraphs(n):
    return " ".join(f"Sentence {i} explains property {i} of the kernel trick in feature space." for i in range(n))


def test_parents_split_on_headings_up_to_level_three():
    doc = make_doc([
        block("title", "Lecture 1: Test", level=1),
        block("heading", "Intro", level=2), block("text", paragraphs(30)),
        block("heading", "Details", level=3), block("text", paragraphs(30)),
        block("heading", "Minor", level=4), block("text", paragraphs(5)),
        block("heading", "Next", level=2), block("text", paragraphs(30)),
    ])
    result = chunk(doc, parent_min_tokens=10, parent_target_tokens=10)
    titles = [p.section_title for p in result.parents]
    assert titles == ["Intro", "Details", "Next"]  # the H4 does not split; the title merges forward
    assert result.parents[1].section_path == ["Lecture 1: Test", "Intro", "Details"]
    assert "#### Minor" in result.parents[1].text


def test_small_sections_merge_up_to_target():
    doc = make_doc([
        block("title", "Lecture 1: Test", level=1),
        block("heading", "A", level=2), block("text", paragraphs(10)),
        block("heading", "B", level=2), block("text", paragraphs(10)),
        block("heading", "C", level=2), block("text", paragraphs(10)),
    ])
    result = chunk(doc)
    assert len(result.parents) == 1
    assert result.parents[0].section_title == "A – C"
    assert result.parents[0].section_path == ["Lecture 1: Test", "A – C"]
    assert result.parents[0].token_count <= 1200


def test_flat_level_one_headings_still_merge_but_not_across_lectures():
    # Layout models often label every heading as level 1.
    doc = make_doc([
        block("heading", "Lecture 3: Kernels", level=1), block("text", paragraphs(10)),
        block("heading", "Mercer's theorem", level=1), block("text", paragraphs(10)),
        block("heading", "Examples", level=1), block("text", paragraphs(10)),
        block("heading", "Lecture 4: Trees", level=1), block("text", paragraphs(20)),
    ], title=None)
    result = chunk(doc)
    assert len(result.parents) == 2
    assert "Mercer" in result.parents[0].text and "Examples" in result.parents[0].text
    assert result.parents[1].text.startswith("# Lecture 4: Trees")


def test_oversized_sections_split_at_block_boundaries():
    doc = make_doc([block("heading", "Big", level=2)] + [block("text", paragraphs(12)) for _ in range(12)])
    result = chunk(doc)
    assert len(result.parents) >= 2
    for p in result.parents:
        assert p.token_count <= 1200
    assert result.parents[1].section_title == "Big (part 2)"


def test_single_huge_paragraph_is_split_by_sentences():
    doc = make_doc([block("heading", "Big", level=2), block("text", paragraphs(150))])
    result = chunk(doc)
    assert len(result.parents) >= 2
    assert all(p.token_count <= 1200 for p in result.parents)


def test_atomic_blocks_are_never_split():
    table = "|a|b|\n|---|---|\n" + "\n".join(f"|row {i}|value {i}|" for i in range(120))
    formula = r"\frac{\partial L}{\partial w} = \sum_i \alpha_i y_i x_i"
    doc = make_doc([
        block("heading", "Stuff", level=2), block("text", paragraphs(20)),
        block("formula", formula), block("text", paragraphs(20)),
        block("table", table), block("text", paragraphs(5)),
    ])
    result = chunk(doc)
    holders = [c for c in result.children if "| row 0 |" in c.raw_text or "|row 0|" in c.raw_text]
    assert holders and all(table in c.raw_text for c in holders)
    f_holders = [c for c in result.children if r"\frac{\partial L}" in c.raw_text]
    assert f_holders and all(f"$$\n{formula}\n$$" in c.raw_text for c in f_holders)
    for p in result.parents:
        assert p.text.count("|row 0|") <= 1


def test_children_sizes_and_overlap():
    doc = make_doc([block("heading", "Long", level=2), block("text", unique_paragraphs(70))])
    result = chunk(doc)
    children = result.children
    assert len(children) >= 3
    for c in children:
        assert c.token_count <= 300 + 5
    for c in children[:-1]:
        assert c.token_count >= 200
    for prev, nxt in zip(children, children[1:]):
        overlap = " ".join(prev.raw_text.split()[-8:])
        assert nxt.raw_text.startswith(overlap) or overlap in nxt.raw_text[:400]
        shared = _shared_prefix_tokens(prev.raw_text, nxt.raw_text)
        assert 35 <= shared <= 50, shared


def _shared_prefix_tokens(prev: str, nxt: str) -> int:
    words = nxt.split()
    best = 0
    for n in range(1, min(len(words), 80)):
        if prev.endswith(" ".join(words[:n])):
            best = n
    return count(" ".join(words[:best]))


def test_contextual_prefix_and_metadata():
    doc = make_doc([
        block("title", "Lecture 4: SVMs", level=1),
        block("heading", "KKT Conditions", level=2),
        block("text", paragraphs(10), page=2),
    ], title=None)
    result = chunk(doc)
    child = result.children[0]
    assert child.contextualized_text.startswith(
        "[Course: CS229 | Lecture 4: SVMs | Section: KKT Conditions] "
    )
    assert child.contextualized_text.endswith(child.raw_text)
    assert child.parent_id == result.parents[0].parent_id
    assert (child.document_id, child.course_id, child.pdf_name) == ("d1", "c1", "Lecture_01.pdf")
    # Headings ride along with the first content unit; pages come from the content.
    assert child.page_num == 2 and child.page_end == 2
    assert child.bboxes and all(b.page in (1, 2) for b in child.bboxes)
    assert [c.ord for c in result.children] == list(range(len(result.children)))


def test_contextual_prefix_format():
    assert contextual_prefix("CS229", "Lecture 4: SVMs", "KKT Conditions") == (
        "[Course: CS229 | Lecture 4: SVMs | Section: KKT Conditions] "
    )
    assert contextual_prefix("CS229", "Support Vector Machines", "KKT") == (
        "[Course: CS229 | Lecture: Support Vector Machines | Section: KKT] "
    )


def test_child_section_titles_follow_nested_headings():
    doc = make_doc([
        block("title", "Lecture 1: Test", level=1),
        block("heading", "Parent", level=2), block("text", paragraphs(8)),
        block("heading", "Child", level=3), block("text", paragraphs(8)),
    ])
    result = chunk(doc)
    assert len(result.parents) == 1
    titles = {c.section_title for c in result.children}
    assert titles <= {"Lecture 1: Test", "Parent", "Child"}
    assert any("Child" in c.contextualized_text.split("]")[0] for c in result.children) or len(result.children) == 1


def test_slides_make_one_parent_per_slide():
    doc = make_doc(
        [block("heading", f"Slide {i}", page=i, level=2) for i in range(1, 4)]
        + [block("list_item", "A point on the slide", page=i, y=200) for i in range(1, 4)]
        + [block("text", "Continuation without a heading", page=4)],
        is_slides=True,
    )
    doc.blocks.sort(key=lambda b: (b.page, b.bbox.y0))
    result = chunk(doc)
    assert [p.page_start for p in result.parents] == [1, 2, 3, 4]
    assert all(p.page_start == p.page_end for p in result.parents)
    assert result.parents[3].section_title == "Slide 3"


def test_lecture_title_falls_back_to_file_name():
    doc = make_doc([block("text", paragraphs(3))], title=None, pdf_name="Week_3_Notes.pdf")
    result = chunk(doc)
    assert result.parents[0].lecture_title == "Week 3 Notes"


def test_empty_document():
    result = chunk(make_doc([block("text", "   ")]))
    assert result.parents == [] and result.children == []


@pytest.mark.parametrize(
    "kw",
    [
        {"parent_min_tokens": 0},
        {"parent_min_tokens": 900, "parent_target_tokens": 800},
        {"child_min_tokens": 400, "child_max_tokens": 300},
        {"child_overlap_tokens": 250},
    ],
)
def test_invalid_parameters(kw):
    with pytest.raises(ValueError):
        HierarchicalChunker(**kw)


@pytest.mark.parametrize("name", [SVM, RNN, SLIDES])
def test_real_lectures_keep_equations_and_tables_whole(sample_pdfs, name):
    doc = PyMuPDFParser().parse(sample_pdfs[name])
    result = chunk(doc)
    assert result.parents and result.children
    atomic = [b.to_markdown() for b in doc.blocks if b.is_atomic]
    for md in atomic:
        assert any(md in c.raw_text for c in result.children), md[:40]
    for c in result.children:
        assert c.raw_text.count("$$") % 2 == 0
        assert re.match(r"^\[Course: CS229 \| .+ \| Section: .+\] ", c.contextualized_text)
    for p in result.parents:
        assert p.token_count <= 1200
