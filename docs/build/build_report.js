// Builds docs/report/Course_Notes_RAG_IEEE_Report.docx (IEEE conference style, two columns)
//   NODE_PATH=<dir with docx> node docs/build/build_report.js
const fs = require("fs");
const path = require("path");
const {
  AlignmentType, BorderStyle, Document, Footer, ImageRun, Math: OMath, MathFraction, MathRun,
  MathSubScript, MathSum, Packer, PageNumber, Paragraph, SectionType, Table, TableCell,
  TableLayoutType, TableRow, TabStopType, TextRun, WidthType, ShadingType, VerticalAlign, HeadingLevel,
} = require("docx");
const data = require("./results.json");

const ROOT = path.resolve(__dirname, "..", "..");
const FIG = (f) => path.join(ROOT, "docs", "figures", f);
const OUT = path.join(ROOT, "docs", "report", "Course_Notes_RAG_IEEE_Report.docx");

// ---- geometry (DXA: 1440 = 1 inch) ----
const PAGE = { width: 12240, height: 15840 };
const MARGIN = { top: 1080, bottom: 1440, left: 900, right: 900 };
const GAP = 360;
const FULL = PAGE.width - MARGIN.left - MARGIN.right; // 10440 = 7.25 in
const COL = (FULL - GAP) / 2;                          // 5040 = 3.5 in
const PX = (dxa) => Math.round((dxa / 1440) * 96);     // ImageRun sizes are in pixels at 96 dpi
const FONT = "Times New Roman";

// ---- inline markup: *italic*, **bold**, `code` ----
function runs(str, base = {}) {
  const out = [];
  const re = /(\*\*[^*]+\*\*|\*[^*]+\*|`[^`]+`|_\{[^}]+\})/g;
  let last = 0;
  for (const m of str.matchAll(re)) {
    if (m.index > last) out.push(new TextRun({ text: str.slice(last, m.index), ...base }));
    const t = m[0];
    if (t.startsWith("**")) out.push(new TextRun({ text: t.slice(2, -2), bold: true, ...base }));
    else if (t.startsWith("`")) out.push(new TextRun({ text: t.slice(1, -1), font: "Courier New", size: (base.size ?? 20) - 2, ...base }));
    else if (t.startsWith("_{")) out.push(new TextRun({ text: t.slice(2, -1), subScript: true, ...base }));
    else out.push(new TextRun({ text: t.slice(1, -1), italics: true, ...base }));
    last = m.index + t.length;
  }
  if (last < str.length) out.push(new TextRun({ text: str.slice(last), ...base }));
  return out;
}

const P = (str, opts = {}) => new Paragraph({
  children: runs(str, opts.run), alignment: opts.align ?? AlignmentType.JUSTIFIED,
  indent: opts.noIndent ? undefined : { firstLine: 202 }, spacing: { after: opts.after ?? 0, before: opts.before ?? 0, line: 240 },
  keepNext: opts.keepNext,
});
let h1n = 0, h2n = 0;
const ROMAN = ["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI", "XII"];
const H1 = (t) => {
  h1n += 1; h2n = 0;
  return new Paragraph({
    heading: HeadingLevel.HEADING_1, alignment: AlignmentType.CENTER, keepNext: true,
    spacing: { before: 200, after: 100 },
    children: [new TextRun({ text: `${ROMAN[h1n - 1]}. ${t}`, smallCaps: true, font: FONT, size: 20 })],
  });
};
const H1u = (t) => new Paragraph({ // unnumbered (references, acknowledgment)
  heading: HeadingLevel.HEADING_1, alignment: AlignmentType.CENTER, keepNext: true, spacing: { before: 200, after: 100 },
  children: [new TextRun({ text: t, smallCaps: true, font: FONT, size: 20 })],
});
const H2 = (t) => {
  h2n += 1;
  return new Paragraph({
    heading: HeadingLevel.HEADING_2, keepNext: true, spacing: { before: 120, after: 60 },
    children: [new TextRun({ text: `${String.fromCharCode(64 + h2n)}. ${t}`, italics: true, font: FONT, size: 20 })],
  });
};
const bullets = (items) => items.map((t) => new Paragraph({
  children: runs(t), alignment: AlignmentType.JUSTIFIED, numbering: { reference: "bullets", level: 0 },
  spacing: { after: 20, line: 240 },
}));
const numbered = (items) => items.map((t) => new Paragraph({
  children: runs(t), alignment: AlignmentType.JUSTIFIED, numbering: { reference: "numbers", level: 0 },
  spacing: { after: 20, line: 240 },
}));

// ---- equations with right-aligned numbers ----
let eqn = 0;
function EQ(mathChildren, width = COL) {
  eqn += 1;
  return new Paragraph({
    tabStops: [{ type: TabStopType.CENTER, position: Math.round(width / 2) }, { type: TabStopType.RIGHT, position: width }],
    spacing: { before: 80, after: 80 },
    children: [new TextRun("\t"), new OMath({ children: mathChildren }), new TextRun({ text: `\t(${eqn})`, font: FONT, size: 20 })],
  });
}
const M = (t) => new MathRun(t);

// ---- figures and tables ----
let fign = 0, tabn = 0;
function FIGURE(file, widthDxa, caption, aspect) {
  fign += 1;
  const w = PX(widthDxa);
  return [
    new Paragraph({ alignment: AlignmentType.CENTER, spacing: { before: 120, after: 40 }, keepNext: true,
      children: [new ImageRun({ type: "png", data: fs.readFileSync(FIG(file)), transformation: { width: w, height: Math.round(w * aspect) },
        altText: { title: `Fig. ${fign}`, description: caption, name: file } })] }),
    new Paragraph({ alignment: AlignmentType.JUSTIFIED, spacing: { after: 120 },
      children: [new TextRun({ text: `Fig. ${fign}. `, font: FONT, size: 16 }), ...runs(caption, { font: FONT, size: 16 })] }),
  ];
}
const thin = { style: BorderStyle.SINGLE, size: 4, color: "000000" };
const none = { style: BorderStyle.NONE, size: 0, color: "FFFFFF" };
function TABLE(title, header, rows, widths, opts = {}) {
  tabn += 1;
  const total = widths.reduce((a, b) => a + b, 0);
  const cell = (t, i, head) => new TableCell({
    width: { size: widths[i], type: WidthType.DXA },
    margins: { top: 30, bottom: 30, left: 70, right: 70 },
    verticalAlign: VerticalAlign.CENTER,
    shading: head ? { type: ShadingType.CLEAR, color: "auto", fill: "E7E9EE" } : undefined,
    borders: { top: thin, bottom: thin, left: none, right: none },
    children: [new Paragraph({ alignment: (opts.align?.[i]) ?? AlignmentType.LEFT, children: runs(String(t), { font: FONT, size: 16, bold: head || undefined }) })],
  });
  return [
    new Paragraph({ alignment: AlignmentType.CENTER, spacing: { before: 140 }, keepNext: true,
      children: [new TextRun({ text: `TABLE ${ROMAN[tabn - 1]}`, font: FONT, size: 16, smallCaps: true })] }),
    new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 60 }, keepNext: true,
      children: [new TextRun({ text: title, font: FONT, size: 16, smallCaps: true })] }),
    new Table({
      width: { size: total, type: WidthType.DXA }, columnWidths: widths, layout: TableLayoutType.FIXED,
      alignment: AlignmentType.CENTER,
      rows: [new TableRow({ tableHeader: true, children: header.map((h, i) => cell(h, i, true)) }),
        ...rows.map((r) => new TableRow({ cantSplit: true, children: r.map((c, i) => cell(c, i, false)) }))],
    }),
    new Paragraph({ spacing: { after: 100 }, children: [] }),
  ];
}
const pct = (x) => `${Math.round(x * 100)}%`;
// Image aspect ratio (height / width) read from the PNG header.
const aspect = (file) => { const b = fs.readFileSync(FIG(file)); return b.readUInt32BE(20) / b.readUInt32BE(16); };

// ================================ CONTENT ================================
const title = [
  new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 200 },
    children: [new TextRun({ text: "Course Notes RAG and Adaptive Quiz Engine: Grounded Question Answering and Bloom-Calibrated Practice from Lecture PDFs", font: FONT, size: 44 })] }),
];
const authors = [
  ["Royce B Selvaraj", "24BCB0078"], ["Srujan Buddharaju", "24BDS0347"], ["Pranali Kamisetty", "[Registration Number]"],
];
const authorTable = new Table({
  width: { size: FULL, type: WidthType.DXA }, columnWidths: [FULL / 3, FULL / 3, FULL / 3], layout: TableLayoutType.FIXED,
  borders: { top: none, bottom: none, left: none, right: none, insideHorizontal: none, insideVertical: none },
  rows: [new TableRow({ children: authors.map(([name, reg]) => new TableCell({
    width: { size: FULL / 3, type: WidthType.DXA }, borders: { top: none, bottom: none, left: none, right: none },
    children: [
      new Paragraph({ alignment: AlignmentType.CENTER, children: [new TextRun({ text: name, font: FONT, size: 22 })] }),
      ...[`Reg. No. ${reg}`, "School of Computer Science and Engineering", "Vellore Institute of Technology", "Vellore, India"].map((l, i) =>
        new Paragraph({ alignment: AlignmentType.CENTER, children: [new TextRun({ text: l, font: FONT, size: 18, italics: i > 0 && i < 3 })] })),
    ],
  })) })],
});

const abstract = new Paragraph({ alignment: AlignmentType.JUSTIFIED, spacing: { after: 120 }, children: [
  new TextRun({ text: "Abstract", bold: true, italics: true, font: FONT, size: 18 }),
  new TextRun({ text: "—", bold: true, font: FONT, size: 18 }),
  new TextRun({ font: FONT, size: 18, bold: true, text:
    "Students increasingly ask general-purpose chatbots about their coursework, but the answers are not grounded in the course's own notes and cannot be checked against them. We present a study assistant that answers only from a student's lecture PDFs, cites the exact page of every claim, and generates practice quizzes that adapt to the sections the student knows least. The system parses lecture notes, Beamer slides and scanned handwriting with a layout-aware pipeline that preserves equations as LaTeX, tables and page coordinates; indexes parent–child chunks in a hybrid dense (BGE-M3) and sparse (BM25) index fused by Reciprocal Rank Fusion; reranks parent sections with a cross-encoder and abstains below a relevance threshold; and generates answers with a locally hosted 7-billion-parameter model (Qwen 2.5). A verification layer checks each citation against the retrieved pages, detects citations to the wrong page, highlights the supporting paragraph and completes missing citations. Quiz questions are produced under a JSON schema, calibrated to Bloom's taxonomy, and repaired and validated before display, and a mastery model steers future quizzes toward weak sections. On a ten-question benchmark the system retrieves the correct lecture section for all questions (MRR 1.00); citation coverage of generated answers rose from 29% to 100% of sentences and valid quiz yield from 33% to 100% through the techniques described. The whole system runs offline on one laptop GPU." }),
] });
const keywords = new Paragraph({ alignment: AlignmentType.JUSTIFIED, spacing: { after: 160 }, children: [
  new TextRun({ text: "Index Terms", bold: true, italics: true, font: FONT, size: 18 }),
  new TextRun({ text: "—retrieval-augmented generation, hybrid retrieval, document layout analysis, citation verification, automatic question generation, adaptive learning, large language models.", bold: true, font: FONT, size: 18 }),
] });

const archFigure = FIGURE("architecture.png", FULL, "System architecture. Ingestion (top) runs as a background job; grounded chat (middle) and quiz generation (bottom) run in real time. Dashed arrows show data flowing from the stores and the feedback from mastery to section selection.", 660 / 1200);

const body1 = [
  abstract, keywords,
  H1("Introduction"),
  P("Large language models (LLMs) have become a default study aid, yet they answer from what they absorbed during training rather than from the material a course actually teaches. Their answers can be fluent and wrong, a failure mode known as hallucination [1], and they rarely point to a page the student can verify. For coursework this is a serious limitation: the lecture notes define the notation, the assumptions and the level of detail that will be examined."),
  P("Retrieval-augmented generation (RAG) addresses the problem by retrieving passages from a document collection and conditioning the model on them [2]. Lecture material, however, is hostile to the simple RAG pipelines found in tutorials. Splitting text by character count cuts derivations in half, scrambles the columns of tables, and ignores equations that exist only as images in slides or as handwriting in scanned notes. Pure vector search also blurs exactly the tokens students search for: theorem numbers (“Lemma 3.2”), variable names and Greek letters."),
  P("Answering questions is also only half of studying. Practice testing is one of the few learning techniques with consistently high measured utility [3], [4], but writing good multiple-choice questions, with plausible distractors and an explanation for each option, is laborious. A useful assistant should therefore also generate practice questions from the same notes and focus them on what the student has not yet mastered."),
  P("This paper describes Course Notes Studio, a system that combines both functions. Its contributions are:", { keepNext: true }),
  ...bullets([
    "a layout-aware ingestion pipeline that turns lecture PDFs, slide decks and scanned handwriting into structured blocks carrying LaTeX, tables, page numbers and bounding boxes, and a parent–child chunker that never splits a formula or table;",
    "a hybrid retrieval funnel (dense + BM25 with a math-aware tokenizer, Reciprocal Rank Fusion, parent promotion and cross-encoder reranking) that abstains with “Not found in course notes” instead of guessing;",
    "a citation verification layer that checks the source and the page of every citation, highlights the supporting paragraph in the PDF, and completes or flags uncited sentences;",
    "a structured quiz generator calibrated to Bloom's taxonomy [5], in which the model cites numbered evidence sentences instead of retyping quotes, and drafts are repaired and validated before use;",
    "an adaptive mastery model that samples weak sections more often, and an evaluation showing which of these techniques made a 7-billion-parameter local model reliable enough for the task.",
  ]),
  H1("Related Work"),
  H2("Retrieval for Question Answering"),
  P("Dense passage retrieval encodes questions and passages into a shared vector space [6]; BGE-M3 extends this to long, multilingual inputs with a single model [7]. Approximate nearest-neighbour search over such vectors is typically performed with HNSW graphs [8]. Lexical retrieval with BM25 derives from the probabilistic relevance framework [9] and remains strong on exact-match queries; bm25s provides a fast implementation [10]. Reciprocal Rank Fusion (RRF) combines rankings from different systems without score calibration and often outperforms the individual rankers [11]. Query expansion with hypothetical documents (HyDE) can further improve dense recall [12]."),
  H2("Reranking and Grounded Generation"),
  P("Cross-encoders that read the query and passage together give more accurate relevance estimates than bi-encoders and are widely used as a second stage [13]. RAG systems condition generation on retrieved passages [2], [14], and recent work evaluates whether generated citations actually support the claims they are attached to [15]. Our verification layer is a lightweight, lexical form of this check that runs without an additional model and adds page-level and paragraph-level localization."),
  H2("Document Parsing"),
  P("Docling combines a page-layout model, the TableFormer table-structure model, OCR and optional formula recognition into a single conversion pipeline [16]. We use it as the primary parser and fall back to PyMuPDF with RapidOCR when it is unavailable or fails on a file."),
  H2("Question Generation and Adaptive Practice"),
  P("Automatic question generation for education has been surveyed extensively [17]; LLMs make it far easier but raise concerns about correctness and grounding [18]. Bloom's revised taxonomy distinguishes remembering, understanding and applying knowledge [5], which we map to Recall, Conceptual and Application questions. Student modelling ranges from Bayesian knowledge tracing [19] to Elo-style rating updates [20]; our mastery model is an exponential moving average in the spirit of the latter."),
];

const body2 = [
  H1("System Overview"),
  P("Fig. 1 shows the architecture. It has two workflows. *Ingestion* runs as a background job when a PDF is uploaded: the file is parsed, chunked and indexed, and its status (queued, parsing, chunking, indexing, ready or failed) is visible in the user interface. *Question answering* and *quiz generation* run in real time; answers are streamed to the browser over server-sent events. All components run locally: the language model, Qwen 2.5 7B Instruct [22], is served by Ollama, embeddings and reranking run on the same GPU, and data is stored in SQLite and an embedded Qdrant vector store."),
  H1("Methodology"),
  H2("Layout-Aware Ingestion"),
  P("Docling's layout model labels each region of a page as a title, section header, paragraph, list item, table, formula or picture. Tables are exported as GitHub-flavoured Markdown, formula regions are converted to LaTeX by a formula-recognition model, and scanned regions are read by OCR. Each resulting block keeps its page number and a bounding box converted to top-left coordinates, which later lets the interface highlight a paragraph in the original PDF. Because the layout model often labels every heading as level 1, heading depth is inferred from numbering (“2.1”, “Lecture 4”) where available. The fallback parser detects headings from font sizes, recognises formula lines by their density of mathematical symbols and converts Unicode math (Greek letters, super- and subscripts, combining accents) to LaTeX that KaTeX can render; pages with no text layer are passed to RapidOCR."),
  H2("Parent–Child Chunking"),
  P("Retrieval needs small units for precise matching, while generation needs enough surrounding context to state a derivation completely. We therefore build two levels. *Parent* chunks follow the document structure: a new parent starts at every heading of level 1–3, or at every slide in a slide deck. Small neighbouring sections are merged until a parent reaches about 800 tokens, never beyond 1200 tokens, and never across a heading that begins a new lecture or chapter. *Child* chunks of 200–300 tokens with a 50-token overlap are cut from each parent at sentence boundaries. Formula, table and code blocks are atomic: no chunk boundary may fall inside one. Each child is embedded with a contextual prefix of the form “[Course: CS229-lite | Lecture 4: Support Vector Machines | Section: KKT Conditions]”, and tables are rewritten as one “header: value” sentence per row for embedding, while the Markdown table is kept for display and for the language model."),
  H2("Hybrid Retrieval"),
  P("A question, rewritten into a standalone question when it follows earlier turns, is searched in two ways. Dense retrieval returns the 25 children with the highest cosine similarity of BGE-M3 embeddings. Sparse retrieval returns the top 25 children under BM25, using a tokenizer that maps Unicode Greek letters and LaTeX commands to the same token (α and `\\alpha` both become “alpha”), keeps dotted theorem numbers such as 3.2 and sub- or superscripted variables such as `x_i`, and removes stop words. Optionally, the question is expanded into paraphrases (multi-query) and a hypothetical answer passage (HyDE) [12]. All rankings are fused by RRF [11]:", { keepNext: true }),
  EQ([M("RRF(d)="), new MathSum({ children: [new MathFraction({ numerator: [M("1")], denominator: [M("k+"), new MathSubScript({ children: [M("r")], subScript: [M("m")] }), M("(d)")] })], subScript: [M("m∈R")] }), M(",  k=60")]),
  P("where *R* is the set of rankings and *r*_{m}(*d*) the 1-based rank of child *d* in ranking *m*. The 15 best children are mapped back to their parents (deduplicated), so that the next stages see whole sections.", { noIndent: true }),
  H2("Reranking and Abstention"),
  P("Each candidate parent is scored together with the question by the bge-reranker-v2-m3 cross-encoder, whose sigmoid output is read as the probability that the passage is relevant. The five highest-scoring parents with a score above τ = 0.35 form the context. If no candidate passes the threshold, the system replies “Not found in course notes” without calling the language model, which removes the most common source of ungrounded answers."),
  H2("Grounded Generation and Citation Verification"),
  P("The language model receives the parent sections with explicit page markers ([p. 12]) and is instructed to answer only from them, to end every sentence that states a fact with a citation of the form [Source: Lecture_04_SVMs.pdf, p. 2], and to write mathematics in LaTeX. Because a 7B model follows formatting rules more reliably when they are repeated next to the question, the rule is restated at the end of the user turn. After generation, every answer passes four checks. For a claim c and a block b, let T(·) be the set of lexical tokens; the support of b for c is", { keepNext: true }),
  EQ([M("s(c,b)="), new MathFraction({ numerator: [M("|T(c)∩T(b)|")], denominator: [M("|T(c)|")] })]),
  ...numbered([
    "*Source check:* the cited file must be among the retrieved sections and the page inside the section's page range; otherwise the citation is marked unverifiable.",
    "*Page check:* if another page of the section supports the claim strongly (s ≥ 0.5) and clearly better than the cited page (by at least 0.25), the citation is flagged as pointing to the wrong page and linked to the better page.",
    "*Highlight:* the block with the highest support on the cited page (s ≥ 0.2) provides the bounding box that the PDF viewer highlights.",
    "*Completion:* sentences without a citation are matched against all retrieved blocks; when one block supports a sentence with s ≥ 0.5, a citation marked “auto” is inserted, and the remaining sentences are reported as unsupported claims.",
  ]),
  ...FIGURE("fig_chat.png", COL, "Grounded chat with the real models (top). Solid green chips are verified citations, dashed “auto” chips were added by completion, and the amber chip “p.2 → 1” was caught citing the wrong page. Clicking a chip opens the PDF with the supporting paragraph highlighted (bottom).", aspect("fig_chat.png")),
  H2("Structured Quiz Generation"),
  P("A quiz request specifies an optional topic, the number of questions (1–10) and the Bloom levels to include. For each question slot one parent section is chosen (Section IV-G) and the model is asked for one question under a JSON schema enforced by the inference server: a stem, four options each with a correctness flag, a *trap type* and a misconception explanation, a hint, and the evidence for the correct answer. Distractors are built from three pedagogical traps: sign or formula inversion, violation of a theorem's preconditions, and confusion of related terms."),
  P("Asking the model to copy an exact quote from the notes proved unreliable: it paraphrased the quote, which then failed verification. Instead, the notes are shown as numbered sentences ([S1], [S2], …) with formulas and tables as single units, and the model returns the numbers of one to three evidence sentences. The quote and its page are assembled from those sentences, so the quote is verbatim by construction. Each draft is then *repaired* and *validated*. Repair fixes labelling slips without changing what the question asks: it labels the single correct option as correct, assigns an unused trap type to a mislabelled distractor, fills an empty distractor rationale from its trap type, and, when the cited evidence does not support the correct option, re-selects the evidence sentence that supports it best if one clears the threshold. Validation then requires exactly one correct option, four distinct options, at least two different trap types, balanced LaTeX, and evidence that shares at least 25% of the correct option's terms. Invalid drafts are sent back with the list of problems, up to two retries, with the sampling temperature raised on each retry (0.4, 0.65, 0.9) so that the model does not repeat the same draft. Options are shuffled before display, and correctness is revealed only after the student answers."),
  H2("Adaptive Mastery"),
  P("Each parent section *c* has a mastery score *M*_{c} ∈ [0, 1], initialised to 0.5. After the first attempt at a question drawn from *c*, with outcome *S* = 1 if correct and 0 otherwise,", { keepNext: true }),
  EQ([new MathSubScript({ children: [M("M")], subScript: [M("c")] }), M("←clip("), new MathSubScript({ children: [M("M")], subScript: [M("c")] }), M("+α(S−"), new MathSubScript({ children: [M("M")], subScript: [M("c")] }), M("), 0, 1),  α=0.25")]),
  P("Retries are allowed but do not change mastery. When a quiz is generated, 70% of the slots are filled by sampling sections without replacement with probability", { noIndent: true, keepNext: true }),
  EQ([M("P(c)∝"), new MathSubScript({ children: [M("(1.05−M")], subScript: [M("c")] }), M(")²")]),
  P("so that a section at 10% mastery is about 40 times as likely to be chosen as one at 90%. The remaining 30% are the most relevant sections for a topic quiz, or uniformly random sections otherwise, which keeps coverage of the whole course. Bloom levels are assigned to slots in rotation.", { noIndent: true }),
];

const stack = [
  ["Parsing", "Docling 2.143 (layout, TableFormer, formula model); PyMuPDF 2.0 + RapidOCR fallback"],
  ["Embeddings", "BAAI/bge-m3 (1024-d, FP16 on GPU) in an embedded Qdrant collection"],
  ["Sparse index", "bm25s with a math-aware tokenizer, rebuilt per course from SQLite"],
  ["Reranker", "BAAI/bge-reranker-v2-m3 cross-encoder, max length 1024"],
  ["LLM", "Qwen 2.5 7B Instruct (Q4) via Ollama; any OpenAI-compatible server"],
  ["Backend", "Python 3.12, FastAPI, SQLite, server-sent events"],
  ["Frontend", "React 19, Vite, react-pdf (pdf.js), KaTeX, react-markdown"],
  ["Testing", "pytest, Vitest + Testing Library, Playwright"],
];
const body3a = [
  H1("Implementation"),
  P("The system is implemented in about 5,700 lines of Python and 3,700 lines of TypeScript, with 3,000 lines of tests. Table I lists the main components. The backend exposes a REST interface for courses, documents (upload, status, parsed Markdown, file, re-processing and deletion), retrieval, chat, quizzes, answers and mastery; chat answers and quiz questions are streamed as server-sent events so that the interface can show progress. All thresholds and model names are configuration settings that can be overridden by environment variables. A *test profile* replaces the neural models with deterministic stand-ins (a hashing embedder, a lexical reranker and an extractive answer generator), so that the complete product can be exercised end to end without a GPU."),
];
const body3b = [
  P("The interface (Fig. 2) has three tabs. The *Library* accepts PDFs by drag and drop, shows each document's progress through the pipeline and previews the extracted Markdown with rendered mathematics. *Chat* streams answers with citation chips coloured by their verification status; clicking a chip opens a split-screen PDF viewer on the cited page with the supporting paragraph highlighted, and an unverifiable citation is struck through. *Quiz* generates questions progressively, accepts answers by mouse or keyboard, explains every option with its trap type, links the source quote to its page, and shows a per-section mastery panel from which a weak section can be practised directly."),
  ...TABLE("Technology Stack", ["Component", "Implementation"], stack, [1150, COL - 1150]),
];

const qShort = [
  "Vanishing gradient & LSTM fix", "KKT & complementary slackness", "What Lemma 3.2 states", "Bayes' theorem", "Zero covariance vs. independence",
  "Geometric vs. functional margin", "Infinite-dimensional kernel", "Gradient clipping", "Bernoulli variance (table)", "Large learning rate (scan)",
];
const qDoc = ["RNN", "SVM", "SVM", "Slides", "Slides", "SVM", "SVM", "RNN", "Slides", "Scan"];
const v1 = [[1, 0.998], [1, 0.991], [1, 0.670], [1, 0.980], [1, 0.999], [1, 0.948], [1, 0.612], [1, 0.959], [null, null], [1, 0.999]];
const fin = [[1, 0.999], [1, 0.979], [1, 0.656], [1, 0.979], [1, 0.998], [1, 0.924], [1, 0.859], [1, 0.959], [1, 0.383], [1, 0.997]];
const fmt = ([r, sc]) => (r ? `${r} (${sc.toFixed(2)})` : "not found");
const perQ = qShort.map((q, i) => [String(i + 1), q, qDoc[i], fmt(v1[i]), fmt(fin[i])]);

const body4 = [
  H1("Evaluation"),
  H2("Setup"),
  P(`All measurements were taken on a laptop with an Intel Core i9-14900HX, 32 GB of RAM and an NVIDIA GeForce RTX 4070 Laptop GPU with 8 GB of memory. The test corpus is a synthetic course of four PDFs (${data.ingest.pages} pages): lecture notes on support vector machines and on recurrent neural networks containing equations rendered as text and as images and two tables, a seven-slide Beamer-style probability deck with an image-only equation, and a scanned page of handwritten notes on gradient descent. Ingestion produced ${data.ingest.parents} parent sections and ${data.ingest.children} child chunks. The retrieval benchmark consists of ten hand-written questions that require paraphrase matching, exact theorem numbers, a table lookup and OCR. For each question we record the rank of the first retrieved section from the expected document (Hit@1, Hit@5, mean reciprocal rank) and whether the expected page lies inside that section (page accuracy).`),
  H2("Retrieval"),
  P("Table II lists the per-question results with the real models. In the first run the only miss was the question about the variance of a Bernoulli variable, whose answer is a row of a table; no section passed the reranking threshold, so the system correctly abstained rather than answering from the wrong section. Rewriting table rows as sentences for embedding and reranking (Section IV-B) lifted that question above the threshold (0.38 > 0.35) and also raised the score of the kernel-table question from 0.61 to 0.86. Table III summarises the three configurations. The offline stand-ins test the pipeline rather than the models and are reported for comparison only. With warm models a query takes 268 ms on average; the first query of a session takes about 10.7 s because the models are loaded onto the GPU."),
  ...TABLE("Per-Question Retrieval (rank and reranker score)", ["#", "Question", "Doc", "First run", "Final"], perQ, [300, 2040, 640, 1030, 1030], { align: [AlignmentType.CENTER, AlignmentType.LEFT, AlignmentType.LEFT, AlignmentType.CENTER, AlignmentType.CENTER] }),
  ...TABLE("Retrieval Benchmark Summary", ["Configuration", "Hit@1", "Hit@5", "MRR", "Page acc."], [
    ["Offline stand-ins", "1.00", "1.00", "1.00", "0.90"],
    ["Real models, first run", "0.90", "0.90", "0.90", "0.90"],
    ["Real models, final", "1.00", "1.00", "1.00", "1.00"],
  ], [1800, 810, 810, 810, 810], { align: [AlignmentType.LEFT, AlignmentType.CENTER, AlignmentType.CENTER, AlignmentType.CENTER, AlignmentType.CENTER] }),
  H2("Grounding of Generated Answers"),
  P(`For the question “Why does the vanishing gradient problem happen in RNNs and how do LSTMs fix it?”, Qwen 2.5 7B initially placed a single citation at the end of each paragraph: 2 citations, both valid, but only ${pct(data.grounding.coverage_before)} of factual sentences carried one. Restating the citation rule in the user turn produced ${data.grounding.valid_after} valid citations, no invalid ones, and citations on ${pct(data.grounding.coverage_after)} of sentences; the page check flagged one of them, which cited page 1 for a passage on page 2. The model's behaviour varies between runs, which is why completion matters: in the run shown in Fig. 2 it cited only 2 of 8 sentences, one of them to the wrong page, and completion supplied the other 6, so every factual sentence still ended with a verified citation (8 verified, 1 corrected page).`),
  H2("Quiz Generation Reliability"),
  P(`Table IV shows the fraction of requested questions that passed validation for a three-question quiz on KKT conditions and support vectors. With free-text quotes, two of three slots failed because the model paraphrased the quote. Numbered evidence sentences raised the yield to ${pct(data.quiz.evidence_ids_no_repair)} over four runs; the remaining failures were labelling slips that the model repeated on every retry, such as a correct option without the “correct” trap label, an empty rationale or evidence about a neighbouring fact. With repair and a rising retry temperature, ${data.quiz.after_n} of ${data.quiz.after_n} questions passed across three seeds, with ${data.quiz.repairs.evidence_reselected} evidence re-selections and ${data.quiz.repairs.rationale_filled} filled rationales; generation took about ${Math.round(data.quiz.after_seconds / data.quiz.after_n)} s per question.`),
  ...TABLE("Valid Quiz Questions per Request", ["Configuration", "Valid / requested"], [
    ["Free-text exact quote", "1 / 3 (33%)"],
    ["Numbered evidence sentences", "8 / 12 (67%)"],
    ["+ repair and rising retry temperature", `${data.quiz.after_n} / ${data.quiz.after_n} (100%)`],
  ], [3240, 1800], { align: [AlignmentType.LEFT, AlignmentType.CENTER] }),
  H2("Ingestion Performance and Software Testing"),
  P(`Ingesting the four documents with Docling, embedding and indexing took ${data.ingest.seconds} s on the GPU. Docling recovered the image-only Bayes equation on the slides as LaTeX (\`P(A|B)=\\frac{P(B|A)P(A)}{P(B)}\`), kept both tables, detected the slide deck and read the handwritten page by OCR; it misread one symbol (γ̂ as Ŷ) in a text equation. The software is covered by ${data.tests.backend} backend unit and API end-to-end tests, ${data.tests.frontend} frontend unit tests, ${data.tests.browser} browser end-to-end tests on desktop and mobile viewports, and ${data.tests.live} live tests that run the complete stack with the real models. Every component is tested with positive and negative cases, including empty, non-PDF, encrypted, truncated and duplicate uploads, off-topic questions, and malformed model output.`),
];

const syllabus = [
  ["1. Introduction, Intelligent Agents", "Learning agent: perceives notes and questions, acts by answering and quizzing, keeps a student model; partially observable, sequential environment.", "Strong"],
  ["2. Search", "Retrieval as search over passages: cosine and BM25 ranking, RRF fusion, best-first top-k pruning.", "Strong"],
  ["3. Local and Adversarial Search", "Generate-and-test quiz drafting with repair and restarts at rising temperature; distractors as the most tempting wrong answers.", "Partial"],
  ["4. Logic and Reasoning", "Rule-based verification of citations and quiz drafts; “exactly one correct option” as a propositional constraint.", "Strong"],
  ["5. Uncertain Knowledge", "Probabilistic relevance (BM25), reranker probability and abstention threshold, mastery as an updated belief.", "Strong"],
  ["6. Planning", "Hierarchical quiz assembly (goal → slots → section → level → generate → validate); tracked ingestion plan with fallback.", "Partial"],
  ["7. Communicating, Perceiving, Acting", "Information retrieval, information extraction (layout, tables, formulas), probabilistic language processing, OCR and layout detection.", "Strong"],
  ["8. Contemporary Issues", "Hallucination and grounding, verifiable citations, private on-device inference.", "Strong"],
];
const body5 = [
  H1("Relation to the BCSE306L Syllabus"),
  P("The project was developed for BCSE306L Artificial Intelligence, whose textbook is Russell and Norvig [21]. Table V maps each syllabus module to the part of the system that applies it. Module 7 (communicating, perceiving and acting) is the core of the project: information retrieval, information extraction and probabilistic language processing are its main subsystems, and layout detection and OCR are perception tasks on page images. Module 5 appears in the probabilistic ranking models, the relevance threshold that turns uncertainty into an explicit abstention, and the mastery score as a belief about the student. Searching (Module 2) underlies retrieval, and rule-based reasoning (Module 4) underlies both verification layers. Local search and planning are used in a looser sense, in the generate–repair–retry loop and the hierarchical assembly of a quiz."),
  ...TABLE("Mapping to Syllabus Modules", ["Module", "Where it appears", "Link"], syllabus, [1250, 3150, 640], { align: [AlignmentType.LEFT, AlignmentType.LEFT, AlignmentType.CENTER] }),
  P("The project also addresses the four course outcomes: evaluating AI methods (CO1) through the retrieval benchmark and the before/after measurements of each technique; applying AI to inference, perception, knowledge representation and learning (CO2); reasoning under uncertainty with a knowledge representation of parent and child chunks (CO3); and showing how search drives problem solving (CO4), since retrieval decides what the language model is allowed to say."),
  H1("Discussion and Limitations"),
  P("The measurements show that a small local model becomes usable for grounded study support when the surrounding system carries most of the burden: retrieval decides what may be said, the abstention threshold decides when nothing should be said, and verification and repair catch the formatting slips that a 7B model makes. Several limitations remain. The evaluation is small and uses synthetic lecture notes written for the purpose; the results need confirming on real course material with independently written questions and with student and faculty ratings of quiz quality. The verification layer is lexical: it can confirm that a claim's terms appear on a page, but not that a paraphrased claim is entailed by it. Generation remains stochastic, so a quiz slot can still be skipped after all retries, and an answer can omit part of a question. Distinct options are checked on their text, so two options can still look almost the same once rendered (options A and C in Fig. 3). Formula recognition can misread symbols, and parsing is the slowest step when no GPU is available."),
  H1("Conclusion and Future Work"),
  P("We presented a study assistant that answers questions from a student's own lecture PDFs with verified page-level citations and generates adaptive, Bloom-calibrated quizzes from the same material, entirely on one laptop. Layout-aware parsing, hybrid retrieval with reranking and abstention, citation verification and completion, numbered evidence for quiz generation, and draft repair together raised retrieval accuracy to 10 of 10 questions, citation coverage to 100% of sentences and quiz yield to 100% in our tests. Future work includes a larger evaluation on real courses, an entailment model in the verification layer, and replacing the moving-average mastery score with Bayesian knowledge tracing [19] and spaced-repetition scheduling."),
];

const refs = [
  "Z. Ji *et al.*, “Survey of hallucination in natural language generation,” *ACM Comput. Surv.*, vol. 55, no. 12, pp. 1–38, 2023.",
  "P. Lewis *et al.*, “Retrieval-augmented generation for knowledge-intensive NLP tasks,” in *Proc. Adv. Neural Inf. Process. Syst. (NeurIPS)*, 2020, pp. 9459–9474.",
  "J. Dunlosky, K. A. Rawson, E. J. Marsh, M. J. Nathan, and D. T. Willingham, “Improving students' learning with effective learning techniques,” *Psychol. Sci. Public Interest*, vol. 14, no. 1, pp. 4–58, 2013.",
  "H. L. Roediger and J. D. Karpicke, “Test-enhanced learning: Taking memory tests improves long-term retention,” *Psychol. Sci.*, vol. 17, no. 3, pp. 249–255, 2006.",
  "L. W. Anderson and D. R. Krathwohl, Eds., *A Taxonomy for Learning, Teaching, and Assessing: A Revision of Bloom's Taxonomy of Educational Objectives*. New York, NY, USA: Longman, 2001.",
  "V. Karpukhin *et al.*, “Dense passage retrieval for open-domain question answering,” in *Proc. Conf. Empirical Methods Natural Lang. Process. (EMNLP)*, 2020, pp. 6769–6781.",
  "J. Chen, S. Xiao, P. Zhang, K. Luo, D. Lian, and Z. Liu, “BGE M3-Embedding: Multi-lingual, multi-functionality, multi-granularity text embeddings through self-knowledge distillation,” 2024, *arXiv:2402.03216*.",
  "Y. A. Malkov and D. A. Yashunin, “Efficient and robust approximate nearest neighbor search using hierarchical navigable small world graphs,” *IEEE Trans. Pattern Anal. Mach. Intell.*, vol. 42, no. 4, pp. 824–836, Apr. 2020.",
  "S. Robertson and H. Zaragoza, “The probabilistic relevance framework: BM25 and beyond,” *Found. Trends Inf. Retr.*, vol. 3, no. 4, pp. 333–389, 2009.",
  "X. H. Lù, “BM25S: Orders of magnitude faster lexical search via eager sparse scoring,” 2024, *arXiv:2407.03618*.",
  "G. V. Cormack, C. L. A. Clarke, and S. Büttcher, “Reciprocal rank fusion outperforms Condorcet and individual rank learning methods,” in *Proc. 32nd Int. ACM SIGIR Conf. Res. Develop. Inf. Retr.*, 2009, pp. 758–759.",
  "L. Gao, X. Ma, J. Lin, and J. Callan, “Precise zero-shot dense retrieval without relevance labels,” in *Proc. 61st Annu. Meeting Assoc. Comput. Linguistics (ACL)*, 2023, pp. 1762–1777.",
  "R. Nogueira and K. Cho, “Passage re-ranking with BERT,” 2019, *arXiv:1901.04085*.",
  "Y. Gao *et al.*, “Retrieval-augmented generation for large language models: A survey,” 2023, *arXiv:2312.10997*.",
  "T. Gao, H. Yen, J. Yu, and D. Chen, “Enabling large language models to generate text with citations,” in *Proc. Conf. Empirical Methods Natural Lang. Process. (EMNLP)*, 2023, pp. 6465–6488.",
  "C. Auer *et al.*, “Docling technical report,” 2024, *arXiv:2408.09869*.",
  "G. Kurdi, J. Leo, B. Parsia, U. Sattler, and S. Al-Emari, “A systematic review of automatic question generation for educational purposes,” *Int. J. Artif. Intell. Educ.*, vol. 30, pp. 121–204, 2020.",
  "E. Kasneci *et al.*, “ChatGPT for good? On opportunities and challenges of large language models for education,” *Learn. Individual Differences*, vol. 103, 2023, Art. no. 102274.",
  "A. T. Corbett and J. R. Anderson, “Knowledge tracing: Modeling the acquisition of procedural knowledge,” *User Model. User-Adapted Interact.*, vol. 4, no. 4, pp. 253–278, 1994.",
  "R. Pelánek, “Applications of the Elo rating system in adaptive educational systems,” *Comput. Educ.*, vol. 98, pp. 169–179, 2016.",
  "S. Russell and P. Norvig, *Artificial Intelligence: A Modern Approach*, 3rd ed. Upper Saddle River, NJ, USA: Prentice Hall, 2015.",
  "Qwen Team, “Qwen2.5 technical report,” 2024, *arXiv:2412.15115*.",
];
const references = [
  H1u("References"),
  ...refs.map((r, i) => new Paragraph({
    alignment: AlignmentType.JUSTIFIED, spacing: { after: 30 }, indent: { left: 360, hanging: 360 },
    children: [new TextRun({ text: `[${i + 1}]\t`, font: FONT, size: 16 }), ...runs(r, { font: FONT, size: 16 })],
    tabStops: [{ type: TabStopType.LEFT, position: 360 }],
  })),
];

// ================================ ASSEMBLY ================================
const page = { size: PAGE, margin: MARGIN };
const footer = new Footer({ children: [new Paragraph({ alignment: AlignmentType.CENTER, children: [new TextRun({ children: [PageNumber.CURRENT], font: FONT, size: 16 })] })] });
const two = { type: SectionType.CONTINUOUS, page, column: { count: 2, space: GAP, equalWidth: true } };
const one = { type: SectionType.CONTINUOUS, page, column: { count: 1 } };

const doc = new Document({
  creator: "Royce B Selvaraj, Srujan Buddharaju, Pranali Kamisetty",
  title: "Course Notes RAG and Adaptive Quiz Engine",
  description: "IEEE-format project report for BCSE306L Artificial Intelligence",
  styles: {
    default: { document: { run: { font: FONT, size: 20 }, paragraph: { spacing: { line: 240 } } } },
    paragraphStyles: [
      { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true, run: { font: FONT, size: 20 }, paragraph: { outlineLevel: 0 } },
      { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true, run: { font: FONT, size: 20, italics: true }, paragraph: { outlineLevel: 1 } },
    ],
  },
  numbering: {
    config: [
      { reference: "bullets", levels: [{ level: 0, format: "bullet", text: "•", alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 300, hanging: 200 } } } }] },
      { reference: "numbers", levels: [{ level: 0, format: "decimal", text: "%1)", alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 300, hanging: 260 } } } }] },
    ],
  },
  sections: [
    { properties: { page, column: { count: 1 } }, footers: { default: footer }, children: [...title, authorTable, new Paragraph({ spacing: { after: 200 }, children: [] })] },
    { properties: two, footers: { default: footer }, children: body1 },
    { properties: one, footers: { default: footer }, children: archFigure },
    { properties: two, footers: { default: footer }, children: [...body2, ...body3a, ...body3b, ...body4, ...FIGURE("fig_quiz.png", COL, "Feedback after answering a generated question: every option shows its trap type and rationale, the verbatim source quote links to its page, and the change in mastery is shown.", aspect("fig_quiz.png")), ...body5, ...references] },
    { properties: one, footers: { default: footer }, children: [new Paragraph({ children: [] })] },
  ],
});

fs.mkdirSync(path.dirname(OUT), { recursive: true });
Packer.toBuffer(doc).then((buf) => { fs.writeFileSync(OUT, buf); console.log("wrote", OUT); });
