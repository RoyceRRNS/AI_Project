"""Synthetic lecture material: two lecture-note PDFs, a Beamer-style slide deck and a scanned
page of handwritten notes. Used by the tests, the retrieval benchmark and the UI's
"Load sample course" button.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pymupdf

SAMPLE_COURSE = {"name": "Machine Learning Foundations", "code": "CS229-lite"}


def _font_paths() -> dict[str, Path]:
    import matplotlib

    base = Path(matplotlib.get_data_path()) / "fonts" / "ttf"
    return {
        "reg": base / "DejaVuSans.ttf",
        "bold": base / "DejaVuSans-Bold.ttf",
        "ital": base / "DejaVuSans-Oblique.ttf",
    }


def render_latex_png(latex: str, path: Path, fontsize: int = 15, dpi: int = 220) -> Path:
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib.figure import Figure

    fig = Figure(figsize=(0.01, 0.01))
    fig.text(0, 0, f"${latex}$", fontsize=fontsize)
    fig.savefig(path, dpi=dpi, bbox_inches="tight", pad_inches=0.06, facecolor="white")
    return path


class LectureWriter:
    """Tiny flowing-layout PDF writer: headings, wrapped paragraphs, bullets, equations, tables."""

    SIZES = {0: 22.0, 1: 17.0, 2: 14.0, 3: 12.0}

    def __init__(self, width: float = 595, height: float = 842, margin: float = 56,
                 body_size: float = 10.5) -> None:
        self.doc = pymupdf.open()
        self.w, self.h, self.m = width, height, margin
        self.body = body_size
        self.fonts = _font_paths()
        self._metrics = {k: pymupdf.Font(fontfile=str(v)) for k, v in self.fonts.items()}
        self._tmp = Path(tempfile.mkdtemp(prefix="cnr_eq_"))
        self.page = None
        self.y = 0.0
        self.new_page()

    def new_page(self) -> None:
        self.page = self.doc.new_page(width=self.w, height=self.h)
        for name, path in self.fonts.items():
            self.page.insert_font(fontname=name, fontfile=str(path))
        self.y = self.m

    def _ensure(self, needed: float) -> None:
        if self.y + needed > self.h - self.m:
            self.new_page()

    def _wrap(self, text: str, font: str, size: float, width: float) -> list[str]:
        metric = self._metrics[font]
        lines: list[str] = []
        current = ""
        for word in text.split():
            trial = f"{current} {word}".strip()
            if current and metric.text_length(trial, fontsize=size) > width:
                lines.append(current)
                current = word
            else:
                current = trial
        if current:
            lines.append(current)
        return lines

    def text(self, text: str, *, size: float | None = None, font: str = "reg", indent: float = 0,
             center: bool = False, space_after: float = 7, leading: float = 1.45) -> None:
        size = size or self.body
        width = self.w - 2 * self.m - indent
        for line in self._wrap(text, font, size, width):
            self._ensure(size * leading)
            x = self.m + indent
            if center:
                x = (self.w - self._metrics[font].text_length(line, fontsize=size)) / 2
            self.page.insert_text((x, self.y + size), line, fontsize=size, fontname=font)
            self.y += size * leading
        self.y += space_after

    def heading(self, text: str, level: int = 1) -> None:
        size = self.SIZES.get(level, 12.0)
        self._ensure(size * 3 + 30)
        self.y += 6 if level > 0 else 0
        self.text(text, size=size, font="bold", space_after=6 if level else 12)

    def bullets(self, items: list[str], size: float | None = None) -> None:
        size = size or self.body
        for item in items:
            lines = self._wrap(item, "reg", size, self.w - 2 * self.m - 16)
            for i, line in enumerate(lines):
                self._ensure(size * 1.45)
                if i == 0:
                    self.page.insert_text((self.m + 2, self.y + size), "•", fontsize=size, fontname="reg")
                self.page.insert_text((self.m + 16, self.y + size), line, fontsize=size, fontname="reg")
                self.y += size * 1.45
            self.y += 3
        self.y += 5

    def equation(self, text: str, size: float | None = None) -> None:
        self.text(text, size=(size or self.body) + 1.5, font="ital", center=True, space_after=10)

    def equation_image(self, latex: str, max_width: float | None = None) -> None:
        png = render_latex_png(latex, self._tmp / f"eq_{len(list(self._tmp.iterdir()))}.png")
        pix = pymupdf.Pixmap(str(png))
        w, h = pix.width * 72 / 220, pix.height * 72 / 220
        limit = max_width or (self.w - 2 * self.m)
        if w > limit:
            h, w = h * limit / w, limit
        self._ensure(h + 14)
        x0 = (self.w - w) / 2
        self.page.insert_image(pymupdf.Rect(x0, self.y + 4, x0 + w, self.y + 4 + h), filename=str(png))
        self.y += h + 14

    def table(self, rows: list[list[str]], col_fracs: list[float], size: float = 9.5) -> None:
        width = self.w - 2 * self.m
        cols = [width * f for f in col_fracs]
        pad = 4
        heights = []
        for r, row in enumerate(rows):
            font = "bold" if r == 0 else "reg"
            n_lines = max(len(self._wrap(cell, font, size, cols[c] - 2 * pad)) for c, cell in enumerate(row))
            heights.append(n_lines * size * 1.35 + 2 * pad)
        self._ensure(sum(heights) + 12)
        y = self.y
        for r, row in enumerate(rows):
            x = self.m
            font = "bold" if r == 0 else "reg"
            for c, cell in enumerate(row):
                rect = pymupdf.Rect(x, y, x + cols[c], y + heights[r])
                self.page.draw_rect(rect, color=(0.25, 0.25, 0.25), width=0.7,
                                    fill=(0.92, 0.94, 0.97) if r == 0 else None)
                ty = y + pad
                for line in self._wrap(cell, font, size, cols[c] - 2 * pad):
                    self.page.insert_text((x + pad, ty + size), line, fontsize=size, fontname=font)
                    ty += size * 1.35
                x += cols[c]
            y += heights[r]
        self.y = y + 12

    def save(self, path: Path, title: str) -> Path:
        self.doc.set_metadata({"title": title, "author": "Course staff", "subject": title})
        self.doc.save(path, garbage=3, deflate=True)
        self.doc.close()
        return path


def build_svm_lecture(path: Path) -> Path:
    w = LectureWriter()
    w.heading("Lecture 4: Support Vector Machines", 0)
    w.text("CS229-lite · Machine Learning Foundations · Lecture notes", font="ital", size=10)
    w.heading("1 Introduction", 1)
    w.text(
        "Support vector machines (SVMs) are among the best off-the-shelf supervised learning "
        "algorithms. The central idea is the margin: a classifier that separates the training data "
        "with a large gap between the classes is more confident and tends to generalize better. In "
        "this lecture we use labels y ∈ {−1, 1} and write the classifier as h(x) = g(wᵀx + b), where "
        "g(z) = 1 if z ≥ 0 and g(z) = −1 otherwise. The parameter w is the normal vector of the "
        "decision boundary and b is the intercept term, which we keep separate from w."
    )
    w.text(
        "We will first define two notions of margin, then derive the optimal margin classifier as a "
        "convex optimization problem, study Lagrange duality and the KKT conditions, and finally "
        "introduce kernels, which let SVMs learn non-linear decision boundaries efficiently in very "
        "high dimensional feature spaces."
    )
    w.heading("2 Margins", 1)
    w.heading("2.1 Functional Margin", 2)
    w.text(
        "Given a training example (x⁽ⁱ⁾, y⁽ⁱ⁾), the functional margin of (w, b) with respect to the "
        "example is defined as follows."
    )
    w.equation("γ̂⁽ⁱ⁾ = y⁽ⁱ⁾ (wᵀx⁽ⁱ⁾ + b)")
    w.text(
        "If y⁽ⁱ⁾ = 1, a large functional margin requires wᵀx⁽ⁱ⁾ + b to be a large positive number; if "
        "y⁽ⁱ⁾ = −1 it must be a large negative number. A positive functional margin means the example "
        "is classified correctly. However, the functional margin alone is not a meaningful measure of "
        "confidence: replacing (w, b) with (2w, 2b) doubles the functional margin without changing the "
        "classifier at all. This motivates imposing a normalization condition such as ‖w‖ = 1."
    )
    w.heading("2.2 Geometric Margin", 2)
    w.text(
        "The geometric margin of (w, b) with respect to a training example is the signed distance "
        "from the point x⁽ⁱ⁾ to the decision boundary wᵀx + b = 0. It is invariant to rescaling of the "
        "parameters, and it equals the functional margin divided by the norm of w, so γ = γ̂ / ‖w‖."
    )
    w.equation("γ⁽ⁱ⁾ = y⁽ⁱ⁾ ( (w / ‖w‖)ᵀ x⁽ⁱ⁾ + b / ‖w‖ )")
    w.text(
        "The geometric margin of the whole training set is the smallest geometric margin over the "
        "individual examples, γ = minᵢ γ⁽ⁱ⁾. When ‖w‖ = 1 the functional and geometric margins are equal."
    )
    w.heading("3 The Optimal Margin Classifier", 1)
    w.text(
        "Assuming the training set is linearly separable, the optimal margin classifier chooses the "
        "hyperplane that maximizes the geometric margin of the training set. Fixing the scale so that "
        "the functional margin of the training set is 1, maximizing γ = 1/‖w‖ is equivalent to "
        "minimizing ‖w‖². This gives a convex quadratic objective with linear constraints, which is a "
        "quadratic program that standard solvers handle efficiently:"
    )
    w.equation_image(r"\min_{w,b}\ \frac{1}{2}\Vert w\Vert^2 \quad \mathrm{s.t.}\quad y^{(i)}(w^T x^{(i)}+b)\geq 1,\ i=1,\ldots,n")
    w.heading("3.1 Lagrange Duality", 2)
    w.text(
        "Consider the primal problem of minimizing f(w) subject to gᵢ(w) ≤ 0 for i = 1, …, k and "
        "hᵢ(w) = 0 for i = 1, …, l. The generalized Lagrangian is L(w, α, β) = f(w) + Σᵢ αᵢ gᵢ(w) + "
        "Σᵢ βᵢ hᵢ(w), where the αᵢ ≥ 0 and βᵢ are the Lagrange multipliers. The primal optimal value "
        "is p* = min_w max_{α ≥ 0, β} L(w, α, β) and the dual optimal value is d* = max_{α ≥ 0, β} "
        "min_w L(w, α, β). Weak duality always holds: d* ≤ p*."
    )
    w.text(
        "Lemma 3.2 (Strong duality). Suppose f and the gᵢ are convex, the hᵢ are affine, and the "
        "constraints gᵢ are strictly feasible, meaning there exists some w with gᵢ(w) < 0 for all i "
        "(Slater's condition). Then there exist w*, α*, β* such that w* solves the primal problem, "
        "α* and β* solve the dual problem, and p* = d* = L(w*, α*, β*)."
    )
    w.heading("3.2 KKT Conditions", 2)
    w.text(
        "Under the assumptions of Lemma 3.2, the optimal w*, α* and β* satisfy the Karush-Kuhn-Tucker "
        "(KKT) conditions. Stationarity requires that the gradient of the Lagrangian with respect to w "
        "vanishes at w*. Primal feasibility requires gᵢ(w*) ≤ 0, and dual feasibility requires αᵢ* ≥ 0. "
        "Complementary slackness requires that the product of each multiplier and its constraint is zero:"
    )
    w.equation("αᵢ* gᵢ(w*) = 0,   i = 1, …, k")
    w.text(
        "Complementary slackness implies that αᵢ* > 0 only when gᵢ(w*) = 0, that is, only when the "
        "constraint is active. Conversely, if any w*, α*, β* satisfy the KKT conditions then they are a "
        "solution to both the primal and the dual problems. For the optimal margin classifier the "
        "active constraints belong to the training points whose functional margin is exactly 1. These "
        "points are called the support vectors, and only they have non-zero multipliers αᵢ."
    )
    w.heading("4 Kernels", 1)
    w.text(
        "The dual form of the SVM depends on the training data only through inner products ⟨x⁽ⁱ⁾, x⁽ʲ⁾⟩. "
        "We can therefore replace every inner product with a kernel K(x, z) = φ(x)ᵀφ(z) for some "
        "feature mapping φ. Computing K directly is often much cheaper than computing φ(x) explicitly, "
        "even when φ maps into a very high or infinite dimensional space. This is the kernel trick."
    )
    w.text(
        "Mercer's theorem characterizes valid kernels: a function K is a valid kernel if and only if, "
        "for any finite set of points, the corresponding kernel matrix is symmetric positive "
        "semi-definite. The table below summarizes the most common kernels."
    )
    w.table(
        [
            ["Kernel", "Formula", "Feature space"],
            ["Linear", "K(x, z) = xᵀz", "The original input space"],
            ["Polynomial", "K(x, z) = (xᵀz + c)ᵈ", "All monomials up to degree d"],
            ["Gaussian (RBF)", "K(x, z) = exp(−‖x − z‖² / (2σ²))", "Infinite-dimensional"],
            ["Sigmoid", "K(x, z) = tanh(κ xᵀz + θ)", "Not always positive semi-definite"],
        ],
        [0.22, 0.43, 0.35],
    )
    w.heading("5 Soft Margin and Regularization", 1)
    w.text(
        "When the data are not linearly separable, or when outliers would force a very small margin, "
        "we add slack variables ξᵢ ≥ 0 and penalize them with ℓ1 regularization. The objective becomes "
        "½‖w‖² + C Σᵢ ξᵢ subject to y⁽ⁱ⁾(wᵀx⁽ⁱ⁾ + b) ≥ 1 − ξᵢ. The parameter C trades off a large margin "
        "against misclassified training points: a larger C penalizes violations more heavily, which "
        "gives fewer margin violations but a smaller margin. In the dual the only change is the box "
        "constraint 0 ≤ αᵢ ≤ C. The SMO algorithm solves the dual by coordinate ascent, updating two "
        "multipliers at a time so that the equality constraint Σᵢ αᵢ y⁽ⁱ⁾ = 0 stays satisfied."
    )
    return w.save(path, "Lecture 4: Support Vector Machines")


def build_rnn_lecture(path: Path) -> Path:
    w = LectureWriter()
    w.heading("Lecture 7: Recurrent Neural Networks", 0)
    w.text("CS229-lite · Machine Learning Foundations · Lecture notes", font="ital", size=10)
    w.heading("1 Recurrent Networks", 1)
    w.text(
        "Recurrent neural networks (RNNs) process sequences by maintaining a hidden state that is "
        "updated at every time step. At time t the hidden state is computed from the previous hidden "
        "state and the current input, and the same weight matrices are shared across all time steps. "
        "Weight sharing lets one model handle sequences of any length."
    )
    w.equation("hₜ = tanh(W_hh hₜ₋₁ + W_xh xₜ + b_h),    yₜ = W_hy hₜ")
    w.text(
        "The hidden state acts as a summary of everything the network has seen so far. Because the "
        "same function is applied at every step, an RNN unrolled over T steps behaves like a very deep "
        "feed-forward network whose layers all share parameters."
    )
    w.heading("2 Backpropagation Through Time", 1)
    w.text(
        "RNNs are trained with backpropagation through time (BPTT): the network is unrolled over the "
        "sequence, the loss is summed over time steps, and ordinary backpropagation is applied to the "
        "unrolled graph. The gradient of the loss at time t with respect to an earlier hidden state hₖ "
        "contains a product of Jacobian matrices, one for every step between k and t:"
    )
    w.equation_image(r"\frac{\partial \mathcal{L}_t}{\partial h_k}=\frac{\partial \mathcal{L}_t}{\partial h_t}\prod_{j=k+1}^{t} W_{hh}^{T}\,\mathrm{diag}(\tanh^{\prime}(a_j))")
    w.text(
        "Truncated BPTT limits the number of steps the gradient is propagated backwards, which reduces "
        "memory and computation at the cost of ignoring very long-range dependencies."
    )
    w.heading("3 Vanishing and Exploding Gradients", 1)
    w.text(
        "The vanishing gradient problem happens because the gradient with respect to early hidden "
        "states is a product of many Jacobian factors. If the largest singular value of W_hh is less "
        "than 1, and since the derivative of tanh is at most 1, the norm of this product shrinks "
        "exponentially with the time gap t − k. Early inputs then receive almost no learning signal, so "
        "a plain RNN cannot learn long-range dependencies."
    )
    w.text(
        "Conversely, if the largest singular value of W_hh is greater than 1, the product can grow "
        "exponentially and the gradients explode, causing unstable updates. Exploding gradients are "
        "handled with gradient clipping: if the gradient norm ‖g‖ exceeds a threshold τ, the gradient "
        "is rescaled to g ← τ g / ‖g‖ before the update. Clipping does not help with vanishing "
        "gradients, which require a change of architecture."
    )
    w.heading("4 Long Short-Term Memory", 1)
    w.text(
        "Long short-term memory (LSTM) networks fix the vanishing gradient problem by adding a memory "
        "cell cₜ that is updated additively and controlled by gates. Each gate is a sigmoid layer whose "
        "output between 0 and 1 scales how much information passes through."
    )
    w.bullets([
        "The forget gate fₜ = σ(W_f [hₜ₋₁, xₜ] + b_f) decides what information to erase from the cell state.",
        "The input gate iₜ = σ(W_i [hₜ₋₁, xₜ] + b_i) decides which new values to write to the cell.",
        "The candidate values c̃ₜ = tanh(W_c [hₜ₋₁, xₜ] + b_c) are the new content that may be written.",
        "The output gate oₜ = σ(W_o [hₜ₋₁, xₜ] + b_o) decides which parts of the cell are exposed as hₜ = oₜ ⊙ tanh(cₜ).",
    ])
    w.equation("cₜ = fₜ ⊙ cₜ₋₁ + iₜ ⊙ c̃ₜ")
    w.text(
        "Because the cell state is updated additively, the derivative of cₜ with respect to cₜ₋₁ is "
        "simply the forget gate fₜ. When the forget gate stays close to 1, the gradient flows across "
        "many time steps without vanishing. This path is often called the constant error carousel, and "
        "it is the reason LSTMs can learn dependencies that span hundreds of time steps."
    )
    w.heading("5 Gated Recurrent Units", 1)
    w.text(
        "The gated recurrent unit (GRU) simplifies the LSTM by merging the forget and input gates into "
        "a single update gate zₜ and adding a reset gate rₜ. The GRU has no separate memory cell, so it "
        "has fewer parameters than an LSTM while often performing similarly."
    )
    w.table(
        [
            ["Model", "Gates", "Memory cell", "Parameters per layer"],
            ["Vanilla RNN", "None", "No", "m(n + m + 1)"],
            ["LSTM", "Forget, input, output", "Yes", "4m(n + m + 1)"],
            ["GRU", "Update, reset", "No (merged into hₜ)", "3m(n + m + 1)"],
        ],
        [0.2, 0.3, 0.25, 0.25],
    )
    w.text("Here n is the input size and m is the hidden size.", size=9.5)
    w.heading("6 Practical Tips", 1)
    w.text(
        "Initialize the forget gate bias to 1 so that the cell remembers by default early in training. "
        "Use gradient clipping for every recurrent model. Teacher forcing feeds the ground-truth "
        "previous token during training, which speeds up convergence but creates a mismatch with "
        "inference time. Layer normalization inside the recurrence stabilizes the hidden state dynamics."
    )
    return w.save(path, "Lecture 7: Recurrent Neural Networks")


def build_probability_slides(path: Path) -> Path:
    w = LectureWriter(width=720, height=540, margin=48, body_size=16)
    slides: list[tuple[str, list[str], str | None, str | None]] = [
        ("Lecture 2: Probability Review", ["CS229-lite · Week 1", "Sample spaces, conditional probability, Bayes' theorem, independence, expectation"], None, None),
        ("Sample Spaces and Events", [
            "The sample space Ω is the set of all possible outcomes of an experiment.",
            "An event is a subset of the sample space, and a probability measure assigns each event a number in [0, 1].",
            "The axioms require P(Ω) = 1 and additivity over disjoint events.",
        ], None, None),
        ("Conditional Probability", [
            "The conditional probability of A given B is the probability of A once we know that B occurred.",
            "It is only defined when P(B) > 0.",
        ], "P(A | B) = P(A ∩ B) / P(B)", None),
        ("Bayes' Theorem", [
            "Bayes' theorem inverts a conditional probability using the prior P(A) and the likelihood P(B | A).",
            "The posterior is proportional to the likelihood times the prior.",
        ], None, r"P(A\mid B)=\frac{P(B\mid A)\,P(A)}{P(B)}"),
        ("Independence", [
            "Events A and B are independent if and only if P(A ∩ B) = P(A) P(B).",
            "Random variables X and Y are independent if their joint distribution factorizes: p(x, y) = p(x) p(y).",
            "Independence implies zero covariance, but zero covariance does not imply independence.",
        ], None, None),
        ("Expectation and Variance", [
            "The expectation of a discrete random variable is E[X] = Σₓ x p(x).",
            "Expectation is linear: E[aX + bY] = a E[X] + b E[Y], even when X and Y are dependent.",
            "The variance measures spread around the mean.",
        ], "Var(X) = E[X²] − (E[X])²", None),
    ]
    first = True
    for title, bullets, eq_text, eq_latex in slides:
        if not first:
            w.new_page()
        first = False
        w.heading(title, 1 if title.startswith("Lecture") is False else 0)
        w.bullets(bullets, size=15)
        if eq_text:
            w.equation(eq_text, size=17)
        if eq_latex:
            w.equation_image(eq_latex, max_width=360)
    w.new_page()
    w.heading("Common Distributions", 1)
    w.table(
        [
            ["Distribution", "Parameters", "Mean", "Variance"],
            ["Bernoulli", "p", "p", "p(1 − p)"],
            ["Binomial", "n, p", "np", "np(1 − p)"],
            ["Gaussian", "μ, σ²", "μ", "σ²"],
            ["Poisson", "λ", "λ", "λ"],
        ],
        [0.3, 0.25, 0.2, 0.25], size=14,
    )
    return w.save(path, "Lecture 2: Probability Review")


HANDWRITTEN_LINES = [
    "Gradient Descent - my notes",
    "Goal: minimize the cost function J(theta).",
    "Update rule: theta := theta - alpha * grad J(theta)",
    "alpha is the learning rate (step size).",
    "If alpha is too large, gradient descent can overshoot",
    "the minimum and diverge.",
    "If alpha is too small, convergence is very slow.",
    "Stochastic gradient descent uses one example",
    "per update, so each step is cheap but noisy.",
]


def build_handwritten_scan(path: Path) -> Path:
    """An image-only page (no text layer), as produced by a phone scan of handwritten notes."""
    fonts = [Path(r"C:\Windows\Fonts\Inkfree.ttf"), Path(r"C:\Windows\Fonts\segoesc.ttf")]
    hand = next((f for f in fonts if f.exists()), _font_paths()["ital"])
    src = pymupdf.open()
    page = src.new_page(width=595, height=842)
    page.draw_rect(page.rect, color=None, fill=(0.99, 0.98, 0.94))
    page.insert_font(fontname="hand", fontfile=str(hand))
    y = 90.0
    for i, line in enumerate(HANDWRITTEN_LINES):
        size = 24 if i == 0 else 19
        page.insert_text((60 + (i % 3) * 4, y), line, fontsize=size, fontname="hand",
                         color=(0.1, 0.12, 0.35))
        y += 44 if i == 0 else 36
    pix = page.get_pixmap(dpi=200)
    src.close()
    out = pymupdf.open()
    scan = out.new_page(width=595, height=842)
    scan.insert_image(scan.rect, pixmap=pix)
    out.set_metadata({"title": "Handwritten notes: gradient descent"})
    out.save(path, garbage=3, deflate=True)
    out.close()
    return path


SAMPLE_FILES = {
    "Lecture_04_SVMs.pdf": build_svm_lecture,
    "Lecture_07_RNNs.pdf": build_rnn_lecture,
    "Lecture_02_Probability_Slides.pdf": build_probability_slides,
    "Handwritten_Gradient_Descent.pdf": build_handwritten_scan,
}


def build_samples(out_dir: Path, names: list[str] | None = None) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    built = {}
    for name, builder in SAMPLE_FILES.items():
        if names and name not in names:
            continue
        target = out_dir / name
        if not target.exists():
            builder(target)
        built[name] = target
    return built


def build_invalid_files(out_dir: Path) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    files = {}
    files["not_a_pdf.pdf"] = out_dir / "not_a_pdf.pdf"
    files["not_a_pdf.pdf"].write_text("This is plain text pretending to be a PDF.", encoding="utf-8")
    files["empty.pdf"] = out_dir / "empty.pdf"
    files["empty.pdf"].write_bytes(b"")
    blank = pymupdf.open()
    blank.new_page()
    files["blank.pdf"] = out_dir / "blank.pdf"
    blank.save(files["blank.pdf"])
    blank.close()
    enc = pymupdf.open()
    enc.new_page().insert_text((72, 72), "secret lecture", fontsize=12)
    files["encrypted.pdf"] = out_dir / "encrypted.pdf"
    enc.save(files["encrypted.pdf"], encryption=pymupdf.PDF_ENCRYPT_AES_256,
             user_pw="student", owner_pw="teacher")
    enc.close()
    files["truncated.pdf"] = out_dir / "truncated.pdf"
    files["truncated.pdf"].write_bytes(b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog")
    return files


if __name__ == "__main__":  # pragma: no cover
    import sys

    target = Path(sys.argv[1] if len(sys.argv) > 1 else "storage/samples")
    for name, p in {**build_samples(target), **build_invalid_files(target / "invalid")}.items():
        print(f"{name}: {p}")
