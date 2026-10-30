// Builds docs/presentation/Course_Notes_RAG_Demo.pptx
//   NODE_PATH=<dir with pptxgenjs, react-icons, react, react-dom, sharp> node docs/build/build_deck.js
const path = require("path");
const pptxgen = require("pptxgenjs");
const React = require("react");
const ReactDOMServer = require("react-dom/server");
const sharp = require("sharp");
const lu = require("react-icons/lu");
const data = require("./results.json");

const ROOT = path.resolve(__dirname, "..", "..");
const FIG = (f) => path.join(ROOT, "docs", "figures", f);
const OUT = path.join(ROOT, "docs", "presentation", "Course_Notes_RAG_Demo.pptx");
const SKILL_THEME = process.env.APPLY_THEME_JS;

const THEME = {
  name: "Course Notes Studio",
  headFontFace: "Cambria",
  bodyFontFace: "Calibri",
  colors: {
    dk1: "0F172A", lt1: "FFFFFF", dk2: "475569", lt2: "EEF2F7",
    accent1: "4F46E5", accent2: "0D9488", accent3: "D97706", accent4: "DC2626",
    accent5: "7C3AED", accent6: "059669", hlink: "4F46E5", folHlink: "7C3AED",
  },
};
const HEX = THEME.colors;
const FOOTER = "Course Notes RAG & Adaptive Quiz Engine  ·  BCSE306L Artificial Intelligence";

const pres = new pptxgen();
pres.layout = "LAYOUT_16x9"; // 10 x 5.625 in
pres.title = "Course Notes RAG & Adaptive Quiz Engine - Implementation and Demo";
pres.author = "Royce B Selvaraj, Srujan Buddharaju, Pranali Kamisetty";
pres.subject = "BCSE306L Artificial Intelligence project";
pres.theme = { headFontFace: THEME.headFontFace, bodyFontFace: THEME.bodyFontFace };
const C = pres.SchemeColor;

// ---------- layouts ----------
pres.defineSlideMaster({
  title: "DARK",
  background: { color: C.text1 },
  objects: [
    { placeholder: { options: { name: "kicker", type: "body", x: 0.6, y: 1.05, w: 5.6, h: 0.3, fontSize: 11, bold: true, color: C.accent2, charSpacing: 4, margin: 0 }, text: "" } },
    { placeholder: { options: { name: "title", type: "title", x: 0.6, y: 1.4, w: 5.6, h: 1.45, fontFace: "Cambria", fontSize: 32, bold: true, color: C.background1, align: "left", valign: "top", margin: 0 }, text: "" } },
  ],
});
pres.defineSlideMaster({
  title: "CLOSING",
  background: { color: C.text1 },
  objects: [
    { placeholder: { options: { name: "kicker", type: "body", x: 0.6, y: 1.05, w: 8.8, h: 0.3, fontSize: 11, bold: true, color: C.accent2, charSpacing: 4, margin: 0 }, text: "" } },
    { placeholder: { options: { name: "title", type: "title", x: 0.6, y: 1.4, w: 8.8, h: 1.2, fontFace: "Cambria", fontSize: 36, bold: true, color: C.background1, align: "left", valign: "top", margin: 0 }, text: "" } },
  ],
});
pres.defineSlideMaster({
  title: "CONTENT",
  background: { color: C.background1 },
  margin: [0.4, 0.5, 0.5, 0.5],
  objects: [
    { placeholder: { options: { name: "kicker", type: "body", x: 0.5, y: 0.3, w: 9, h: 0.25, fontSize: 10, bold: true, color: C.accent2, charSpacing: 4, margin: 0 }, text: "" } },
    { placeholder: { options: { name: "title", type: "title", x: 0.5, y: 0.55, w: 9, h: 0.6, fontFace: "Cambria", fontSize: 26, bold: true, color: C.text1, align: "left", valign: "top", margin: 0 }, text: "" } },
    { text: { text: FOOTER, options: { x: 0.5, y: 5.25, w: 7.5, h: 0.22, fontSize: 9, color: C.text2, margin: 0 } } },
  ],
  slideNumber: { x: 9.1, y: 5.25, w: 0.4, h: 0.22, fontSize: 9, color: C.text2, align: "right" },
});

// ---------- helpers ----------
async function icon(name, hex, px = 256) {
  const svg = ReactDOMServer.renderToStaticMarkup(
    React.createElement(lu[name], { color: `#${hex}`, size: px, strokeWidth: 2 }),
  );
  const buf = await sharp(Buffer.from(svg)).png().toBuffer();
  return "image/png;base64," + buf.toString("base64");
}
const shadow = () => ({ type: "outer", color: "0F172A", opacity: 0.12, blur: 6, offset: 2, angle: 90 });
function card(slide, x, y, w, h, opts = {}) {
  slide.addShape(pres.shapes.ROUNDED_RECTANGLE, {
    x, y, w, h, rectRadius: 0.08, fill: { color: opts.fill ?? C.background2 },
    line: { color: opts.line ?? C.background2, width: 0.75 }, shadow: opts.shadow ? shadow() : undefined,
    objectName: opts.name,
  });
}
async function iconDot(slide, name, x, y, size, circle, glyph = "FFFFFF") {
  slide.addShape(pres.shapes.OVAL, { x, y, w: size, h: size, fill: { color: circle }, line: { color: circle } });
  const pad = size * 0.24;
  slide.addImage({ data: await icon(name, glyph), x: x + pad, y: y + pad, w: size - 2 * pad, h: size - 2 * pad });
}
function text(slide, t, o) {
  slide.addText(t, { isTextBox: true, margin: 0, fontSize: 14, color: C.text1, valign: "top", ...o });
}
function content(kicker, title, section) {
  const s = pres.addSlide({ masterName: "CONTENT", sectionTitle: section });
  s.addText(kicker, { placeholder: "kicker" });
  s.addText(title, { placeholder: "title" });
  return s;
}
const pct = (x) => `${Math.round(x * 100)}%`;
const shot = (slide, file, x, y, w, crop) => {
  const ratio = 1800 / 2880;
  slide.addImage({ path: FIG(file), x, y, w, h: w * ratio, sizing: crop, altText: file });
  slide.addShape(pres.shapes.RECTANGLE, { x, y, w, h: w * ratio, fill: { type: "none" }, line: { color: "CBD5E1", width: 0.75 } });
};

(async () => {
  // =============================== 1. Title ===============================
  pres.addSection({ title: "Introduction" });
  let s = pres.addSlide({ masterName: "DARK", sectionTitle: "Introduction" });
  s.addText("IMPLEMENTATION & DEMO", { placeholder: "kicker" });
  s.addText("Course Notes RAG & Adaptive Quiz Engine", { placeholder: "title" });
  text(s, "A study assistant that answers only from your own lecture PDFs, cites the exact page, and quizzes you on the sections you know least.", { x: 0.6, y: 2.95, w: 5.4, h: 0.75, fontSize: 14, color: C.background2 });
  text(s, "BCSE306L Artificial Intelligence  ·  Vellore Institute of Technology", { x: 0.6, y: 3.85, w: 5.6, h: 0.25, fontSize: 11, color: C.accent2, bold: true });
  text(s, [
    { text: "Royce B Selvaraj (24BCB0078)", options: { breakLine: true } },
    { text: "Srujan Buddharaju (24BDS0347)", options: { breakLine: true } },
    { text: "Pranali Kamisetty (24BDS0326)" },
  ], { x: 0.6, y: 4.2, w: 5.6, h: 0.8, fontSize: 11, color: C.background1, paraSpaceAfter: 2 });
  card(s, 6.55, 0.95, 2.95, 3.75, { fill: "1E293B", line: "1E293B" });
  text(s, "EVERY ANSWER IS CHECKED", { x: 6.8, y: 1.15, w: 2.5, h: 0.25, fontSize: 9, bold: true, color: C.background2, charSpacing: 2 });
  const verdicts = [
    ["LuShieldCheck", HEX.accent6, "Verified citation", "Claim found on the cited page"],
    ["LuLink", HEX.accent1, "Auto-cited", "Supported sentence linked to its page"],
    ["LuSearchX", HEX.accent3, "Not found", "No evidence above τ = 0.35, so no guess"],
  ];
  for (const [i, [ic, col, head, sub]] of verdicts.entries()) {
    const y = 1.6 + i * 0.95;
    await iconDot(s, ic, 6.8, y, 0.55, col);
    text(s, head, { x: 7.5, y: y + 0.04, w: 1.9, h: 0.25, fontSize: 13, bold: true, color: C.background1 });
    text(s, sub, { x: 7.5, y: y + 0.29, w: 1.9, h: 0.4, fontSize: 10, color: C.background2 });
  }
  s.addNotes("Introduce the team and the one-line idea: answers come only from the student's own notes, every claim is cited to a page, and quizzes adapt to weak sections. The card on the right previews the three outcomes the grounding check can produce.");

  // =============================== 2. Problem ===============================
  s = content("THE PROBLEM", "Generic chatbots don't know your lecture notes", "Introduction");
  const problems = [
    ["LuTriangleAlert", "Answers you can't check", "A general chatbot answers from its training data, not the course's notes, and gives no page to verify against."],
    ["LuSigma", "Lecture PDFs break naive RAG", "Character splitters cut equations in half, scramble table columns, and lose image-only formulas and handwritten scans."],
    ["LuBookOpen", "Revision is passive", "Re-reading feels productive but gives no feedback on which sections a student actually knows."],
  ];
  for (const [i, [ic, head, body]] of problems.entries()) {
    const y = 1.45 + i * 1.18;
    await iconDot(s, ic, 0.5, y, 0.55, HEX.accent1);
    text(s, head, { x: 1.25, y, w: 4.6, h: 0.3, fontSize: 16, bold: true });
    text(s, body, { x: 1.25, y: y + 0.33, w: 4.6, h: 0.7, fontSize: 13, color: C.text2 });
  }
  card(s, 6.2, 1.45, 3.3, 3.25, { fill: C.text1, line: C.text1 });
  s.addImage({ data: await icon("LuQuote", HEX.accent2), x: 6.45, y: 1.7, w: 0.45, h: 0.45 });
  text(s, "Practice testing is one of only two learning techniques rated high utility across ages, materials and tasks.", { x: 6.45, y: 2.3, w: 2.85, h: 1.4, fontSize: 15, color: C.background1, italic: true, fontFace: "Cambria" });
  text(s, "Dunlosky et al., Psychological Science in the Public Interest, 2013", { x: 6.45, y: 3.95, w: 2.85, h: 0.5, fontSize: 10, color: C.background2 });
  s.addNotes("Three gaps: answers aren't grounded in the course material, lecture PDFs are hard to parse, and studying is passive. The research quote motivates the quiz half of the project: retrieval practice is one of the most effective study techniques.");

  // =============================== 3. What we built ===============================
  s = content("WHAT WE BUILT", "One workspace: library, chat and adaptive quiz", "Introduction");
  const features = [
    ["LuFileText", HEX.accent1, "Library", "Upload lecture PDFs, Beamer slides or scans. Watch them parse, then preview the extracted Markdown with rendered math."],
    ["LuMessagesSquare", HEX.accent2, "Grounded chat", "Answers stream in with clickable page citations. A click opens the PDF with the supporting paragraph highlighted."],
    ["LuBrain", HEX.accent5, "Adaptive quiz", "Bloom-calibrated multiple-choice questions with a rationale for every option and a mastery score per section."],
  ];
  for (const [i, [ic, col, head, body]] of features.entries()) {
    const y = 1.4 + i * 1.22;
    await iconDot(s, ic, 0.5, y, 0.5, col);
    text(s, head, { x: 1.15, y: y - 0.02, w: 3.0, h: 0.3, fontSize: 16, bold: true });
    text(s, body, { x: 1.15, y: y + 0.3, w: 3.0, h: 0.85, fontSize: 12, color: C.text2 });
  }
  shot(s, "ui_chat_viewer.png", 4.4, 1.4, 5.1);
  text(s, "Runs fully offline on one laptop: RTX 4070 (8 GB), Qwen 2.5 7B through Ollama.", { x: 4.4, y: 4.67, w: 5.1, h: 0.3, fontSize: 11, color: C.text2, italic: true });
  s.addNotes("Live demo starts here. Show the three tabs. The screenshot is a real answer from Qwen 2.5 7B: green chips are verified citations, dashed 'auto' chips were added by the grounding check, and the viewer on the right highlights the paragraph the citation points to.");

  // =============================== 4. How it works ===============================
  pres.addSection({ title: "System" });
  s = content("HOW IT WORKS", "Five stages from a lecture PDF to a checked answer", "System");
  const steps = [
    ["Parse", "Layout model finds headings, tables and formulas; OCR reads scans; formulas become LaTeX.", "Docling · RapidOCR"],
    ["Chunk", "Sections become parents (≈800–1200 tokens); 200–300-token children with 50-token overlap are indexed.", "Hierarchical chunker"],
    ["Retrieve", "Dense and keyword search run in parallel and are fused by rank, then mapped back to parent sections.", "BGE-M3 · bm25s · RRF"],
    ["Rerank", "A cross-encoder scores each section against the question; nothing above τ = 0.35 means “Not found”.", "bge-reranker-v2-m3"],
    ["Verify", "Qwen writes a cited answer; every citation is then checked against the retrieved pages.", "Qwen 2.5 7B · Ollama"],
  ];
  const sw = 1.66, gap = 0.17;
  for (const [i, [head, body, tech]] of steps.entries()) {
    const x = 0.5 + i * (sw + gap);
    const last = i === steps.length - 1;
    card(s, x, 1.45, sw, 2.75, { fill: last ? C.accent2 : C.background2, line: last ? C.accent2 : C.background2 });
    s.addShape(pres.shapes.OVAL, { x: x + 0.15, y: 1.62, w: 0.42, h: 0.42, fill: { color: last ? C.background1 : C.accent2 }, line: { color: last ? C.background1 : C.accent2 } });
    text(s, String(i + 1), { x: x + 0.15, y: 1.62, w: 0.42, h: 0.42, fontSize: 14, bold: true, align: "center", valign: "middle", color: last ? C.accent2 : C.background1 });
    text(s, head, { x: x + 0.15, y: 2.18, w: sw - 0.3, h: 0.3, fontSize: 15, bold: true, fontFace: "Cambria", color: last ? C.background1 : C.text1 });
    text(s, body, { x: x + 0.15, y: 2.52, w: sw - 0.3, h: 1.6, fontSize: 11, color: last ? C.background1 : C.text2 });
    text(s, tech, { x, y: 4.3, w: sw, h: 0.25, fontSize: 10, italic: true, color: C.text2 });
  }
  text(s, "Python · FastAPI · Qdrant · SQLite · React + Vite · server-sent events for streaming", { x: 0.5, y: 4.72, w: 9, h: 0.3, fontSize: 12, color: C.text1 });
  s.addNotes("Walk through the five stages. The first two run once per upload as a background job; the last three run for every question in about a quarter of a second plus generation time.");

  // =============================== 5. Ingestion ===============================
  s = content("INGESTION", "Parsing keeps equations, tables and scans intact", "System");
  const ing = [
    ["LuSigma", "Image-only equation → LaTeX", "Bayes' rule recovered as LaTeX from a slide where the formula is only a picture"],
    ["LuTable", "Table → Markdown rows", "Kernel table keeps its 4 rows × 3 columns; rows are also rewritten as sentences for search"],
    ["LuPenLine", "Handwriting → text", "Scanned notes are OCR'd: “If alpha is too large, gradient descent can overshoot…”"],
    ["LuLayers", "Structure → parent/child chunks", "Headings and slides set the parents; a formula or table is never split"],
  ];
  for (const [i, [ic, head, body]] of ing.entries()) {
    const y = 1.4 + i * 0.88;
    await iconDot(s, ic, 0.5, y, 0.45, HEX.accent2);
    text(s, head, { x: 1.1, y: y - 0.02, w: 3.6, h: 0.27, fontSize: 14, bold: true });
    text(s, body, { x: 1.1, y: y + 0.26, w: 3.6, h: 0.55, fontSize: 11, color: C.text2 });
  }
  shot(s, "ui_preview.png", 4.95, 1.4, 4.55);
  const ingStats = [[data.ingest.pages, "pages"], [data.ingest.seconds + " s", "on the GPU"], [data.ingest.children, "search chunks"]];
  for (const [i, [v, l]] of ingStats.entries()) {
    const x = 4.95 + i * 1.55;
    text(s, String(v), { x, y: 4.35, w: 1.45, h: 0.45, fontSize: 24, bold: true, color: C.accent1, fontFace: "Cambria" });
    text(s, l, { x, y: 4.8, w: 1.45, h: 0.25, fontSize: 10, color: C.text2 });
  }
  s.addNotes("Docling's layout model plus formula recognition is what makes lecture notes usable. The preview on the right is the parsed Markdown for the SVM lecture with equations rendered by KaTeX. The four sample PDFs (13 pages, including a scanned handwritten page) ingest in about 41 seconds on the laptop GPU.");

  // =============================== 6. Retrieval ===============================
  s = content("RETRIEVAL", "Hybrid search finds both meaning and exact symbols", "System");
  card(s, 0.5, 1.45, 2.6, 1.0, { fill: C.background2 });
  text(s, [{ text: "Dense · BGE-M3", options: { bold: true, breakLine: true } }, { text: "top 25 by meaning", options: { color: C.text2 } }], { x: 0.7, y: 1.6, w: 2.3, h: 0.75, fontSize: 13 });
  card(s, 0.5, 2.65, 2.6, 1.0, { fill: C.background2 });
  text(s, [{ text: "Keyword · BM25", options: { bold: true, breakLine: true } }, { text: "top 25; “α” = “\\alpha”, “Lemma 3.2”", options: { color: C.text2 } }], { x: 0.7, y: 2.8, w: 2.3, h: 0.75, fontSize: 13 });
  s.addShape(pres.shapes.RIGHT_ARROW, { x: 3.2, y: 2.3, w: 0.4, h: 0.5, fill: { color: C.text2 }, line: { color: C.text2 } });
  card(s, 3.7, 1.45, 2.7, 2.2, { fill: C.text1, line: C.text1 });
  text(s, "Reciprocal Rank Fusion", { x: 3.9, y: 1.6, w: 2.4, h: 0.3, fontSize: 14, bold: true, color: C.background1 });
  text(s, "RRF(d) = Σₘ 1 / (60 + rankₘ(d))", { x: 3.9, y: 2.05, w: 2.45, h: 0.4, fontSize: 13, color: C.accent2, fontFace: "Cambria", italic: true });
  text(s, "top 15 children → their parent sections, so the model sees whole derivations", { x: 3.9, y: 2.6, w: 2.35, h: 0.9, fontSize: 11, color: C.background2 });
  s.addShape(pres.shapes.RIGHT_ARROW, { x: 6.5, y: 2.3, w: 0.4, h: 0.5, fill: { color: C.text2 }, line: { color: C.text2 } });
  card(s, 7.0, 1.45, 2.5, 1.0, { fill: C.background2 });
  text(s, [{ text: "Cross-encoder rerank", options: { bold: true, breakLine: true } }, { text: "keep top 5 with score > 0.35", options: { color: C.text2 } }], { x: 7.2, y: 1.6, w: 2.2, h: 0.75, fontSize: 13 });
  card(s, 7.0, 2.65, 2.5, 1.0, { fill: "FEF3C7", line: "FEF3C7" });
  text(s, [{ text: "Nothing passes?", options: { bold: true, breakLine: true } }, { text: "reply “Not found in course notes”", options: { color: C.text2 } }], { x: 7.2, y: 2.8, w: 2.2, h: 0.75, fontSize: 13 });
  text(s, "Why both?", { x: 0.5, y: 3.95, w: 9, h: 0.28, fontSize: 14, bold: true });
  text(s, "Embeddings understand paraphrases (“why do early inputs stop learning?”) but blur exact tokens; BM25 with a math-aware tokenizer catches theorem numbers, variable names and Greek letters. Fusing ranks, not scores, needs no tuning between the two.", { x: 0.5, y: 4.25, w: 9, h: 0.7, fontSize: 12, color: C.text2 });
  s.addNotes("The retrieval funnel. Two rankings of child chunks are fused with RRF, promoted to parent sections, then a cross-encoder decides relevance. The 0.35 threshold is what turns 'probably irrelevant' into an explicit Not Found instead of a hallucinated answer.");

  // =============================== 7. Grounded chat ===============================
  pres.addSection({ title: "Features" });
  s = content("GROUNDED CHAT", "Every citation is checked against the retrieved pages", "Features");
  shot(s, "ui_chat_viewer.png", 0.5, 1.4, 5.0);
  const checks = [
    ["1", "Source", "the cited file and page must be in the retrieved context"],
    ["2", "Page", "if the claim's words are on another page, the chip shows p.2 → 1"],
    ["3", "Highlight", "the viewer opens on the single paragraph that matches the claim"],
    ["4", "Completion", "uncited but supported sentences get an auto citation; the rest are flagged as unsupported"],
  ];
  for (const [i, [n, head, body]] of checks.entries()) {
    const y = 1.4 + i * 0.78;
    s.addShape(pres.shapes.OVAL, { x: 5.8, y, w: 0.36, h: 0.36, fill: { color: C.accent6 }, line: { color: C.accent6 } });
    text(s, n, { x: 5.8, y, w: 0.36, h: 0.36, fontSize: 12, bold: true, color: C.background1, align: "center", valign: "middle" });
    text(s, head, { x: 6.3, y: y - 0.02, w: 3.2, h: 0.26, fontSize: 13, bold: true });
    text(s, body, { x: 6.3, y: y + 0.24, w: 3.2, h: 0.5, fontSize: 11, color: C.text2 });
  }
  card(s, 5.8, 4.55, 3.7, 0.55, { fill: "D1FAE5", line: "D1FAE5" });
  text(s, `Sentences with a verified citation (Qwen 2.5 7B): ${pct(data.grounding.coverage_before)} → ${pct(data.grounding.coverage_after)}`, { x: 5.95, y: 4.62, w: 3.45, h: 0.42, fontSize: 11.5, bold: true, color: "065F46", valign: "middle" });
  s.addNotes("Demo: ask the vanishing-gradient question and click a chip. Explain the four checks. The small model tended to cite once per paragraph; repeating the rule in the user turn plus server-side completion took verified coverage from 29% to 100% of sentences.");

  // =============================== 8. Quiz ===============================
  s = content("ADAPTIVE QUIZ", "Every question is repaired and validated first", "Features");
  const qsteps = [
    ["LuTarget", "Pick sections", "70% weighted toward weak sections, 30% by relevance"],
    ["LuListOrdered", "Number the evidence", "notes shown as [S1], [S2]… so Qwen cites sentence ids, not a retyped quote"],
    ["LuSparkles", "Generate to a schema", "4 options; each distractor labelled sign inversion, boundary violation or terminology swap"],
    ["LuCircleCheck", "Repair and validate", "fix label slips; reject if not exactly one correct, bad LaTeX, or unsupported evidence"],
  ];
  for (const [i, [ic, head, body]] of qsteps.entries()) {
    const y = 1.4 + i * 0.88;
    await iconDot(s, ic, 0.5, y, 0.45, HEX.accent5);
    text(s, head, { x: 1.1, y: y - 0.02, w: 3.3, h: 0.27, fontSize: 14, bold: true });
    text(s, body, { x: 1.1, y: y + 0.26, w: 3.3, h: 0.55, fontSize: 11, color: C.text2 });
  }
  shot(s, "ui_quiz_feedback.png", 4.6, 1.4, 4.9);
  text(s, `Questions passing validation: ${pct(data.quiz.before_evidence_ids)} with free-text quotes → ${pct(data.quiz.evidence_ids_no_repair)} with evidence ids → ${pct(data.quiz.after)} (${data.quiz.after_n} of ${data.quiz.after_n}) with repair`, { x: 4.6, y: 4.55, w: 4.9, h: 0.5, fontSize: 11, color: C.text2, italic: true });
  s.addNotes("Generate a 3-question quiz on KKT conditions. After answering, every option shows its rationale and trap type, the source quote links to the page, and the mastery change is shown. Only the first attempt counts toward mastery.");

  // =============================== 9. Mastery ===============================
  s = content("ADAPTIVE PRACTICE", "Weak sections come back far more often", "Features");
  card(s, 0.5, 1.4, 4.1, 1.45, { fill: C.background2 });
  text(s, "After each first attempt", { x: 0.7, y: 1.52, w: 3.7, h: 0.25, fontSize: 12, bold: true, color: C.text2 });
  text(s, "M ← clip(M + α(S − M), 0, 1)", { x: 0.7, y: 1.85, w: 3.7, h: 0.45, fontSize: 18, fontFace: "Cambria", italic: true, color: C.accent1 });
  text(s, "S = 1 if correct, 0 if not · α = 0.25 · M starts at 0.5", { x: 0.7, y: 2.38, w: 3.7, h: 0.35, fontSize: 11, color: C.text2 });
  card(s, 0.5, 3.05, 4.1, 1.45, { fill: C.background2 });
  text(s, "Choosing the next quiz's sections", { x: 0.7, y: 3.17, w: 3.7, h: 0.25, fontSize: 12, bold: true, color: C.text2 });
  text(s, "P(section c) ∝ (1.05 − M꜀)²", { x: 0.7, y: 3.5, w: 3.7, h: 0.45, fontSize: 18, fontFace: "Cambria", italic: true, color: C.accent1 });
  text(s, "for 70% of slots; the other 30% keep coverage", { x: 0.7, y: 4.03, w: 3.7, h: 0.35, fontSize: 11, color: C.text2 });
  const ms = [0.1, 0.3, 0.5, 0.7, 0.9];
  const w = ms.map((m) => (1.05 - m) ** 2);
  s.addChart(pres.charts.BAR, [{ name: "Relative chance", labels: ms.map((m) => `M = ${m}`), values: w.map((v) => +(v / w[2]).toFixed(2)) }], {
    x: 4.9, y: 1.35, w: 4.6, h: 3.3, barDir: "col", chartColors: [HEX.accent1],
    showTitle: true, title: "Chance of being picked vs. a section at M = 0.5", titleFontSize: 12, titleColor: HEX.dk1, titleFontFace: "+mn-lt",
    showValue: true, dataLabelPosition: "outEnd", dataLabelFontSize: 11, dataLabelColor: HEX.dk1, dataLabelFormatCode: "0.0\"×\"", dataLabelFontFace: "+mn-lt",
    catAxisLabelColor: HEX.dk2, valAxisLabelColor: HEX.dk2, catAxisLabelFontSize: 11, valAxisLabelFontSize: 10, catAxisLabelFontFace: "+mn-lt", valAxisLabelFontFace: "+mn-lt",
    valGridLine: { color: "E2E8F0", size: 0.75 }, catGridLine: { style: "none" }, showLegend: false, valAxisHidden: true,
  });
  text(s, "A section at 10% mastery is picked about 40× more often than one at 90%.", { x: 4.9, y: 4.7, w: 4.6, h: 0.3, fontSize: 11, italic: true, color: C.text2 });
  s.addNotes("The student model is one number per parent section. A correct first try pulls it toward 1, a wrong one toward 0. Sampling with a squared weight strongly favours weak sections without ever starving the others.");

  // =============================== 10. Results: retrieval ===============================
  pres.addSection({ title: "Evaluation" });
  s = content("RESULTS", "Right lecture section found for 10 of 10 questions", "Evaluation");
  const cfg = data.retrieval;
  s.addChart(pres.charts.BAR, [
    { name: "Offline stand-ins", labels: ["Hit@1", "Hit@5", "MRR", "Page accuracy"], values: cfg.offline.map((v) => v * 100) },
    { name: "Real models, first run", labels: ["Hit@1", "Hit@5", "MRR", "Page accuracy"], values: cfg.live_v1.map((v) => v * 100) },
    { name: "Real models, final", labels: ["Hit@1", "Hit@5", "MRR", "Page accuracy"], values: cfg.live_final.map((v) => v * 100) },
  ], {
    x: 0.5, y: 1.35, w: 5.4, h: 3.4, barDir: "col", barGapWidthPct: 60, chartColors: ["94A3B8", HEX.accent3, HEX.accent2],
    showValue: true, dataLabelPosition: "outEnd", dataLabelFontSize: 9, dataLabelColor: HEX.dk1, dataLabelFormatCode: "0", dataLabelFontFace: "+mn-lt",
    valAxisMinVal: 0, valAxisMaxVal: 110, valAxisHidden: true, valGridLine: { style: "none" }, catGridLine: { style: "none" },
    catAxisLabelColor: HEX.dk2, catAxisLabelFontSize: 11, catAxisLabelFontFace: "+mn-lt",
    showLegend: true, legendPos: "b", legendFontSize: 10, legendColor: HEX.dk2, legendFontFace: "+mn-lt",
  });
  const rows = [
    [{ text: "Measure", options: { bold: true, color: "FFFFFF", fill: { color: HEX.dk1 } } }, { text: "Final", options: { bold: true, color: "FFFFFF", fill: { color: HEX.dk1 }, align: "right" } }],
    ["Hit@1 / Hit@5", { text: "100% / 100%", options: { align: "right" } }],
    ["Mean reciprocal rank", { text: "1.00", options: { align: "right" } }],
    ["Cited page inside section", { text: "100%", options: { align: "right" } }],
    ["Warm query latency", { text: `${data.latency_ms_warm} ms`, options: { align: "right" } }],
  ];
  s.addTable(rows, { x: 6.2, y: 1.45, w: 3.3, colW: [2.1, 1.2], fontSize: 11, fontFace: "Calibri", color: HEX.dk1, border: { type: "solid", pt: 0.5, color: "CBD5E1" }, rowH: 0.34 });
  text(s, "The one miss in the first run was “variance of a Bernoulli variable”, answered by a table. Rewriting table rows as sentences for embedding and reranking fixed it (score 0.38 > τ), and lifted the kernel-table question from 0.61 to 0.86.", { x: 6.2, y: 3.35, w: 3.3, h: 1.35, fontSize: 11, color: C.text2 });
  text(s, "10 hand-written questions over 4 lecture PDFs (13 pages): paraphrases, theorem numbers, a table lookup and a handwritten scan.", { x: 0.5, y: 4.8, w: 9, h: 0.3, fontSize: 10, italic: true, color: C.text2 });
  s.addNotes("Benchmark of ten tricky questions. Offline stand-ins use hashing embeddings and lexical reranking, so they test the pipeline, not the models. With BGE-M3 and the cross-encoder, the first run missed only the table question; linearizing tables fixed it.");

  // =============================== 11. Results: grounding & tests ===============================
  s = content("RESULTS", "What made a 7B model reliable enough", "Evaluation");
  const ba = [
    ["Sentences with a verified citation", data.grounding.coverage_before, data.grounding.coverage_after, "rule repeated in the user turn + server-side completion"],
    ["Quiz questions passing validation", data.quiz.before_evidence_ids, data.quiz.after, "numbered evidence sentences + repair of label slips"],
    ["Benchmark questions found (Hit@1)", 0.9, 1.0, "table rows rewritten as sentences for search"],
  ];
  for (const [i, [label, before, after, how]] of ba.entries()) {
    const y = 1.4 + i * 1.05;
    card(s, 0.5, y, 5.6, 0.9, { fill: C.background2 });
    text(s, label, { x: 0.7, y: y + 0.1, w: 3.2, h: 0.3, fontSize: 13, bold: true });
    text(s, how, { x: 0.7, y: y + 0.42, w: 3.2, h: 0.4, fontSize: 10.5, color: C.text2 });
    text(s, pct(before), { x: 3.95, y: y + 0.18, w: 0.8, h: 0.5, fontSize: 20, color: C.text2, align: "right", fontFace: "Cambria" });
    s.addImage({ data: await icon("LuArrowRight", HEX.dk2), x: 4.85, y: y + 0.3, w: 0.3, h: 0.3 });
    text(s, pct(after), { x: 5.2, y: y + 0.16, w: 0.85, h: 0.5, fontSize: 22, bold: true, color: C.accent6, fontFace: "Cambria" });
  }
  card(s, 6.4, 1.4, 3.1, 3.0, { fill: C.text1, line: C.text1 });
  text(s, "AUTOMATED TESTS, ALL PASSING", { x: 6.6, y: 1.55, w: 2.8, h: 0.25, fontSize: 9, bold: true, color: C.accent2, charSpacing: 2 });
  const tests = [[data.tests.backend, "backend unit + API end-to-end"], [data.tests.frontend, "frontend unit (Vitest)"], [data.tests.browser, "browser end-to-end (desktop + mobile)"], [data.tests.live, "live tests on the real models"]];
  for (const [i, [n, l]] of tests.entries()) {
    const y = 1.9 + i * 0.6;
    text(s, String(n), { x: 6.6, y, w: 0.9, h: 0.45, fontSize: 22, bold: true, color: C.background1, fontFace: "Cambria" });
    text(s, l, { x: 7.5, y: y + 0.08, w: 1.9, h: 0.45, fontSize: 10.5, color: C.background2 });
  }
  text(s, "Positive and negative cases for every component: invalid, empty, encrypted and duplicate PDFs, off-topic questions, malformed model output.", { x: 6.4, y: 4.45, w: 3.1, h: 0.6, fontSize: 10, italic: true, color: C.text2 });
  s.addNotes("The three fixes that mattered most, measured on the real stack. On the right, the test suites: unit and end-to-end tests run offline in about a minute; the live suite exercises Docling, BGE-M3, the reranker and Qwen together.");

  // =============================== 12-14. Syllabus ===============================
  pres.addSection({ title: "Syllabus" });
  const strength = { Strong: HEX.accent6, Partial: HEX.accent3 };
  const syllabusSlide = (title, rowsData) => {
    const sl = content("RELATION TO THE SYLLABUS", title, "Syllabus");
    const header = ["Module", "Syllabus topics", "Where it appears in this project", "Link"].map((t) => ({ text: t, options: { bold: true, color: "FFFFFF", fill: { color: HEX.dk1 } } }));
    const body = rowsData.map(([mod, topics, where, link]) => [
      { text: mod, options: { bold: true } }, topics, where,
      { text: link, options: { bold: true, color: strength[link], align: "center" } },
    ]);
    sl.addTable([header, ...body], {
      x: 0.5, y: 1.35, w: 9, colW: [1.35, 2.15, 4.75, 0.75], fontSize: 10, fontFace: "Calibri", color: HEX.dk1, valign: "middle",
      border: { type: "solid", pt: 0.5, color: "CBD5E1" }, fill: { color: "FFFFFF" }, margin: 0.06,
    });
    return sl;
  };
  s = syllabusSlide("Modules 1–4: agents, search, local search, logic", [
    ["M1 Introduction & Intelligent Agents", "Agent structure, environments, applications of AI", "The app is a learning agent: it perceives notes (parser, OCR) and questions, acts by answering and quizzing, and keeps a student model. The environment is partially observable (knowledge is hidden) and sequential.", "Strong"],
    ["M2 Search", "State-space search, best-first, uniform cost, A*", "Retrieval is search over a space of passages: cosine and BM25 scores order the frontier, RRF fuses the rankings, and best-first pruning keeps 25 → 15 → 5 for the costly cross-encoder.", "Strong"],
    ["M3 Local & Adversarial Search", "Hill climbing, simulated annealing, minimax", "Quiz generation is generate-and-test: score a draft against constraints, repair it, or restart with a higher temperature. Distractors are chosen as the most tempting wrong answers.", "Partial"],
    ["M4 Logic & Reasoning", "Propositional and first-order logic, forward chaining", "Citation and quiz validation are rule-based inference: facts (file, page, section range) fire rules that conclude valid / moved / unsupported; “exactly one correct option” is a propositional constraint.", "Strong"],
  ]);
  s.addNotes("How the project maps to BCSE306L. Modules 1, 2 and 4 are used directly; local search is used in spirit by the generate-repair-retry loop.");
  s = syllabusSlide("Modules 5–8: uncertainty, planning, language", [
    ["M5 Uncertain Knowledge", "Quantifying uncertainty, Bayes rule, Bayesian networks", "BM25 comes from the probabilistic relevance model; the reranker outputs P(relevant | question, passage) and τ = 0.35 is a decision threshold; mastery is a belief about the student, updated by evidence (a simplified knowledge-tracing model).", "Strong"],
    ["M6 Planning", "Classical, hierarchical planning, acting under uncertainty", "A quiz is planned top-down: goal (n questions on weak spots) → slots → section → Bloom level → generate → validate. Ingestion is a tracked plan (queued → parsing → chunking → indexing → ready) with a fallback parser.", "Partial"],
    ["M7 Communicating, Perceiving, Acting", "Probabilistic language processing, information retrieval, information extraction, perception, object recognition", "The core of the project: hybrid retrieval and reranking (IR), layout parsing, tables and formula-to-LaTeX (IE), LLM generation and tokenization (language), layout detection and OCR on page images (perception).", "Strong"],
    ["M8 Contemporary Issues", "Current topics in AI", "Hallucination and grounded generation, verifiable citations, local and private inference on a laptop GPU, academic integrity.", "Strong"],
  ]);
  s.addNotes("Module 7 is the heart of the project: information retrieval, information extraction and probabilistic language processing. Module 5 appears in the probabilistic ranking, the relevance threshold and the student model.");

  s = content("RELATION TO THE SYLLABUS", "Course outcomes met by the project", "Syllabus");
  const cos = [
    ["CO1", "Evaluate AI methods and their foundations", "10-question retrieval benchmark (Hit@k, MRR); offline stand-ins vs. real models; before/after ablations of each fix."],
    ["CO2", "Apply AI to inference, perception, representation and learning", "Perception (layout, OCR), knowledge representation (parent/child chunks, vectors), inference (verification rules), learning (mastery model)."],
    ["CO3", "Reason with uncertainty and knowledge representation", "Probabilistic ranking, a relevance threshold that answers “Not found” instead of guessing, mastery as an updated belief."],
    ["CO4", "Show how search drives problem solving", "Ranked vector and BM25 search, rank fusion and top-k pruning decide what the model is allowed to say."],
  ];
  for (const [i, [co, head, body]] of cos.entries()) {
    const col = i % 2, row = Math.floor(i / 2);
    const x = 0.5 + col * 4.6, y = 1.4 + row * 1.8;
    card(s, x, y, 4.4, 1.6, { fill: C.background2 });
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: x + 0.2, y: y + 0.2, w: 0.75, h: 0.4, rectRadius: 0.06, fill: { color: C.accent1 }, line: { color: C.accent1 } });
    text(s, co, { x: x + 0.2, y: y + 0.2, w: 0.75, h: 0.4, fontSize: 13, bold: true, color: C.background1, align: "center", valign: "middle" });
    text(s, head, { x: x + 1.1, y: y + 0.16, w: 3.15, h: 0.5, fontSize: 12, bold: true, valign: "middle" });
    text(s, body, { x: x + 0.2, y: y + 0.8, w: 4.0, h: 0.75, fontSize: 11, color: C.text2 });
  }
  s.addNotes("The four BCSE306L course outcomes, each with the concrete part of the project that demonstrates it.");

  // =============================== 15. Lessons ===============================
  pres.addSection({ title: "Wrap-up" });
  s = content("WHAT WE LEARNED", "Problems we hit, and how we fixed them", "Wrap-up");
  const lessons = [
    ["Quotes were paraphrased", "Qwen rewrote the “exact quote”, so 2 of 3 questions failed. Showing the notes as numbered sentences and asking for ids makes quotes verbatim by construction."],
    ["One citation per paragraph", "Only 29% of sentences carried a citation. Repeating the rule in the user turn and completing citations on the server reached 100%."],
    ["Tables were invisible to search", "Pipes and dashes don't look like a question. Rewriting rows as “Distribution: Bernoulli; Variance: p(1 − p).” fixed the benchmark miss."],
    ["Every heading was level 1", "Docling flattens heading levels, so sections never merged into 800-token parents. Merging now stops only at “Lecture N” / “Chapter N”."],
  ];
  for (const [i, [head, body]] of lessons.entries()) {
    const col = i % 2, row = Math.floor(i / 2);
    const x = 0.5 + col * 4.6, y = 1.4 + row * 1.8;
    card(s, x, y, 4.4, 1.6, { fill: C.background2 });
    await iconDot(s, "LuLightbulb", x + 0.2, y + 0.2, 0.42, HEX.accent3);
    text(s, head, { x: x + 0.75, y: y + 0.24, w: 3.5, h: 0.3, fontSize: 14, bold: true });
    text(s, body, { x: x + 0.2, y: y + 0.75, w: 4.0, h: 0.8, fontSize: 11, color: C.text2 });
  }
  s.addNotes("Four problems that only showed up with the real models, each with a measured fix.");

  // =============================== 16. Limitations ===============================
  s = content("LIMITATIONS & NEXT STEPS", "What it can't do yet, and where it goes next", "Wrap-up");
  const next = [
    ["LuGauge", "Bigger evaluation", "A larger, independently written question set per course, and quiz-quality ratings from students and faculty."],
    ["LuFlaskConical", "Stronger verification", "Use an entailment (NLI) model next to the lexical check, so paraphrased claims are verified too."],
    ["LuRocket", "Better student model", "Replace the moving-average mastery with Bayesian knowledge tracing and spaced-repetition scheduling."],
  ];
  for (const [i, [ic, head, body]] of next.entries()) {
    const x = 0.5 + i * 3.05;
    card(s, x, 1.4, 2.85, 2.0, { fill: C.background2 });
    await iconDot(s, ic, x + 0.2, 1.6, 0.48, HEX.accent1);
    text(s, head, { x: x + 0.2, y: 2.2, w: 2.45, h: 0.3, fontSize: 14, bold: true });
    text(s, body, { x: x + 0.2, y: 2.52, w: 2.45, h: 0.85, fontSize: 11, color: C.text2 });
  }
  text(s, "KNOWN LIMITATIONS", { x: 0.5, y: 3.65, w: 9, h: 0.25, fontSize: 10, bold: true, color: C.accent4, charSpacing: 3 });
  text(s, [
    { text: "Evaluation scale: 10 benchmark questions over 4 synthetic lecture PDFs; results need confirming on real course material.", options: { bullet: true, breakLine: true } },
    { text: "A 7B model still varies run to run: answers may cite few sentences (completion fills the gap), and two distractors can look nearly identical once rendered.", options: { bullet: true, breakLine: true } },
    { text: "Formula OCR can misread symbols (γ̂ read as Ŷ), and the lexical page check cannot verify paraphrased claims.", options: { bullet: true } },
  ], { x: 0.5, y: 3.95, w: 9, h: 1.15, fontSize: 11, color: C.text2, paraSpaceAfter: 4 });
  s.addNotes("Be candid: the benchmark is small and synthetic, and a 7B model is not deterministic. The next steps target exactly those gaps.");

  // =============================== 17. Thank you ===============================
  s = pres.addSlide({ masterName: "CLOSING", sectionTitle: "Wrap-up" });
  s.addText("THANK YOU", { placeholder: "kicker" });
  s.addText("Questions and feedback welcome", { placeholder: "title" });
  text(s, "Royce B Selvaraj (24BCB0078)  ·  Srujan Buddharaju (24BDS0347)  ·  Pranali Kamisetty (24BDS0326)", { x: 0.6, y: 3.0, w: 8.8, h: 0.3, fontSize: 12, color: C.background1 });
  text(s, "github.com/RoyceRRNS/AI_Project", { x: 0.6, y: 3.45, w: 8.8, h: 0.3, fontSize: 12, color: C.accent2, bold: true });
  for (const [i, [ic, col]] of [["LuShieldCheck", HEX.accent6], ["LuLink", HEX.accent1], ["LuSearchX", HEX.accent3]].entries()) {
    await iconDot(s, ic, 0.6 + i * 0.75, 4.1, 0.55, col);
  }
  s.addNotes("Thank the panel and invite questions. The repository has the code, tests, benchmark results, this deck and the report.");

  await pres.writeFile({ fileName: OUT });
  if (SKILL_THEME) {
    const { applyTheme } = require(SKILL_THEME);
    await applyTheme(OUT, THEME);
  }
  console.log("wrote", OUT);
})().catch((e) => { console.error(e); process.exit(1); });
