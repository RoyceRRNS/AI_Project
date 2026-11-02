"""Synthetic past papers for the sample course, one per layout real papers come in: numbered text
with Part A/B and either/or choices, a Q.No / Question / Marks table, and an image-only scan.
Used by the tests and the Papers tab's "Load sample papers" button.
"""

from __future__ import annotations

from pathlib import Path

import pymupdf

from course_notes_rag.samples import LectureWriter, _font_paths


def _paper_head(w: LectureWriter, title: str, session: str, time: str, marks: int) -> None:
    w.text("CS229-lite Machine Learning Foundations", size=13, font="bold", center=True, space_after=2)
    w.text(f"{title}, {session}", size=11, center=True, space_after=2)
    w.text(f"Time: {time}    Maximum Marks: {marks}", size=10, center=True, space_after=12)


def _questions(w: LectureWriter, lines: list[str]) -> None:
    for line in lines:
        if line == "OR":
            w.text("OR", font="bold", center=True, space_after=4)
        elif line.startswith("("):
            w.text(line, indent=18, space_after=5)
        else:
            w.text(line, space_after=5)


def build_end_sem_2023(path: Path) -> Path:
    w = LectureWriter()
    _paper_head(w, "End Semester Examination", "Nov/Dec 2023", "3 Hours", 40)
    w.text("PART A (5 x 2 = 10 Marks)", font="bold", space_after=2)
    w.text("Answer ALL questions", font="ital", space_after=6)
    _questions(w, [
        "1. Define the functional margin of a training example. (2)",
        "2. State Bayes' theorem. (2)",
        "3. What is the kernel trick? (2)",
        "4. List the gates of an LSTM cell. (2)",
        "5. What is the learning rate in gradient descent? (2)",
    ])
    w.text("PART B (3 x 10 = 30 Marks)", font="bold", space_after=2)
    w.text("Answer ALL questions, choosing one option from each", font="ital", space_after=6)
    _questions(w, [
        "6. (a) Derive the dual form of the optimal margin classifier using the generalized "
        "Lagrangian. (10)",
        "OR",
        "(b) Explain the KKT conditions and show how complementary slackness identifies the "
        "support vectors. (10)",
        "7. (a) Given P(D) = 0.01, P(T | D) = 0.9 and P(T | not D) = 0.05, compute P(D | T) using "
        "Bayes' theorem. (10)",
        "OR",
        "(b) Explain with a neat diagram the structure of an LSTM cell. (10)",
        "8. (a) Explain the vanishing gradient problem in recurrent neural networks and compute "
        "the clipped gradient norm when the threshold is 5 and the gradient norm is 10. (10)",
        "OR",
        "(b) Apply two steps of the gradient descent algorithm to J(theta) = theta^2 starting "
        "from theta = 2 with learning rate 0.1. (10)",
    ])
    return w.save(path, "End Semester Examination Nov/Dec 2023")


def build_end_sem_2024(path: Path) -> Path:
    w = LectureWriter()
    _paper_head(w, "End Semester Examination", "Nov/Dec 2024", "3 Hours", 40)
    w.text("PART A (5 x 2 = 10 Marks)", font="bold", space_after=2)
    w.text("Answer ALL questions", font="ital", space_after=6)
    _questions(w, [
        "1. Define the geometric margin of a training set. (2)",
        "2. State Bayes' theorem. (2)",
        "3. What is meant by the kernel trick? (2)",
        "4. What is a gated recurrent unit? (2)",
        "5. Define stochastic gradient descent. (2)",
    ])
    w.text("PART B (3 x 10 = 30 Marks)", font="bold", space_after=2)
    w.text("Answer ALL questions, choosing one option from each", font="ital", space_after=6)
    _questions(w, [
        "6. (a) Derive the dual of the optimal margin classifier from the generalized "
        "Lagrangian. (10)",
        "OR",
        "(b) Differentiate between the hard margin and the soft margin classifier and explain "
        "the role of the parameter C. (10)",
        "7. (a) A disease affects 2% of people. A test has P(T | D) = 0.95 and P(T | not D) = 0.1. "
        "Calculate P(D | T) using Bayes' theorem. (10)",
        "OR",
        "(b) Draw the block diagram of an LSTM cell and explain each gate. (10)",
        "8. (a) Trace backpropagation through time on a two step recurrent network and show why "
        "the gradient is a product of Jacobian matrices. (10)",
        "OR",
        "(b) A hospital collects patient records that are not linearly separable and wants a "
        "classifier that tolerates a few outliers. Recommend a suitable support vector machine "
        "and justify the choice of kernel and regularization. (10)",
    ])
    return w.save(path, "End Semester Examination Nov/Dec 2024")


def build_cat_2025(path: Path) -> Path:
    w = LectureWriter()
    _paper_head(w, "Continuous Assessment Test - I", "Fall 2025", "90 Minutes", 30)
    w.table(
        [
            ["Q.No", "Sub", "Question", "Marks", "CO", "BL"],
            ["1", "a", "State Mercer's theorem for valid kernels.", "4", "1", "1"],
            ["1", "b", "Compute the Gaussian kernel value for two points at distance 2 when "
             "sigma is 1.", "6", "2", "3"],
            ["2", "", "Prove that the functional margin doubles when (w, b) is replaced by "
             "(2w, 2b).", "10", "1", "3"],
            ["3", "", "Compare LSTM and GRU networks in terms of gates and parameters.", "10", "3", "4"],
        ],
        [0.09, 0.07, 0.62, 0.08, 0.07, 0.07],
    )
    return w.save(path, "Continuous Assessment Test I Fall 2025")


SCAN_LINES = [
    "End Semester Examination, May 2022",
    "Time: 3 Hours    Maximum Marks: 20",
    "Answer ALL questions",
    "1. Define the geometric margin of a training example. (5)",
    "2. Explain the role of the learning rate in gradient descent. (5)",
    "3. Calculate the variance of a Bernoulli variable when p is 0.25. (10)",
]


def build_scanned_paper(path: Path) -> Path:
    """An image-only paper, as produced by photographing or scanning a printed copy."""
    src = pymupdf.open()
    page = src.new_page(width=595, height=842)
    font = _font_paths()["reg"]
    page.insert_font(fontname="print", fontfile=str(font))
    y = 90.0
    for line in SCAN_LINES:
        page.insert_text((56, y), line, fontsize=15, fontname="print")
        y += 40
    pix = page.get_pixmap(dpi=200)
    src.close()
    out = pymupdf.open()
    scan = out.new_page(width=595, height=842)
    scan.insert_image(scan.rect, pixmap=pix)
    out.save(path, garbage=3, deflate=True)
    out.close()
    return path


SAMPLE_PAPERS = {
    "End_Semester_2023.pdf": build_end_sem_2023,
    "End_Semester_2024.pdf": build_end_sem_2024,
    "CAT1_Fall_2025.pdf": build_cat_2025,
    "End_Semester_May_2022_Scan.pdf": build_scanned_paper,
}


def build_sample_papers(out_dir: Path, names: list[str] | None = None) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    built = {}
    for name, builder in SAMPLE_PAPERS.items():
        if names and name not in names:
            continue
        target = out_dir / name
        if not target.exists():
            builder(target)
        built[name] = target
    return built


if __name__ == "__main__":  # pragma: no cover
    import sys

    target_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "storage/samples/papers")
    for name, p in build_sample_papers(target_dir).items():
        print(f"{name}: {p}")
