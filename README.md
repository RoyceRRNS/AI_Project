# Course Notes Studio — RAG & Adaptive Quiz Engine

Upload lecture PDFs, Beamer slides or scanned handwritten notes; ask questions and get answers
that cite the exact page (click a citation to see the paragraph highlighted in the PDF); practise
with Bloom's-taxonomy quizzes that adapt to your weak sections.

```
PDF ─► Docling (layout, tables, OCR, formula→LaTeX) ─► parent/child chunks ─► SQLite
                                                          └─► BGE-M3 → Qdrant (dense)
                                                          └─► bm25s (math-aware tokens)
question ─► [multi-query / HyDE] ─► dense top-25 + BM25 top-25 ─► RRF (k=60) ─► top-15 children
         ─► parent promotion ─► bge-reranker-v2-m3 ─► top-5 with score > 0.35  (else "Not Found")
         ─► Qwen 2.5 7B (Ollama) ─► streamed answer with [Source: file.pdf, p. N] ─► citation check
quiz     ─► mastery-weighted section sampling ─► Qwen JSON-schema output ─► validation/retry ─► grading
```

## Documentation

| | |
|---|---|
| Technical specification (architecture, every pipeline, data model, API, PEAS, syllabus mapping) | [docs/spec/TECHNICAL_SPEC.md](docs/spec/TECHNICAL_SPEC.md) ([PDF](docs/spec/Course_Notes_Studio_Technical_Spec.pdf)) |
| IEEE-format report | [docs/report/Course_Notes_RAG_IEEE_Report.pdf](docs/report/Course_Notes_RAG_IEEE_Report.pdf) ([.docx](docs/report/Course_Notes_RAG_IEEE_Report.docx)) |
| Implementation & demo deck (17 slides, incl. syllabus mapping) | [docs/presentation/Course_Notes_RAG_Demo.pptx](docs/presentation/Course_Notes_RAG_Demo.pptx) ([.pdf](docs/presentation/Course_Notes_RAG_Demo.pdf)) |
| Benchmark results | [benchmarks/results/](benchmarks/results/) |
| Acceptance criteria and original plan | [acceptance_criteria.md](acceptance_criteria.md), [course_notes_rag_quiz_engine_master_plan.md](course_notes_rag_quiz_engine_master_plan.md) |

The report and deck are generated from `docs/build/` (`build_report.js`, `build_deck.js`, with measured numbers in `results.json`); screenshots come from `frontend/scripts/capture-screens.mjs` run against the real stack.

**Team** (BCSE306L Artificial Intelligence, VIT Vellore): Royce B Selvaraj (24BCB0078), Srujan Buddharaju (24BDS0347), Pranali Kamisetty.

## Run it

Requirements: Python 3.12, Node 20+, [Ollama](https://ollama.com) with `ollama pull qwen2.5:7b`.

```bash
python -m venv .venv
.venv/Scripts/python -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu130
.venv/Scripts/python -m pip install -e ".[dev]"
cd frontend && npm install && cd ..
```

```bash
.venv/Scripts/python scripts/serve.py            # API on :8000 (real models; downloaded on first use)
```

```bash
npm --prefix frontend run dev                    # UI on http://localhost:5173 (proxies /api)
```

Or build the UI once (`npm --prefix frontend run build`) and the API serves it at http://localhost:8000.
Click **Try the sample course** on the landing page for a ready-made course.

`scripts/serve.py --profile test --storage-dir ./storage-test` runs the whole product with offline
stand-ins (PyMuPDF + RapidOCR parsing, hashing embedder, lexical reranker, extractive "LLM") — no GPU
or Ollama needed. Every setting in `course_notes_rag/config/settings.py` can be overridden with a
`CNR_` environment variable (e.g. `CNR_LLM_MODEL=qwen2.5:14b`, `CNR_RERANK_THRESHOLD=0.4`).

## Tests

| Suite | Command | What it covers |
|---|---|---|
| Backend unit | `.venv/Scripts/python -m pytest tests/unit` | every module, positive and negative cases |
| Backend e2e | `.venv/Scripts/python -m pytest tests/e2e` | 14 user journeys over HTTP through the whole stack (test profile) |
| Live stack | `.venv/Scripts/python -m pytest -m live` | Docling, BGE-M3, reranker, Qwen: ingestion, 10-question benchmark, chat, quiz |
| Frontend unit | `npm --prefix frontend test` | components, SSE parser, citation rendering |
| UI e2e | `npm --prefix frontend run e2e` | Playwright: real browser against a real backend (desktop + mobile) |

`python scripts/benchmark.py` re-runs the retrieval benchmark and writes a JSON report to `benchmarks/results/`.

## Decisions on the plan's contradictions

| Topic | Plan said | Chosen | Why |
|---|---|---|---|
| Rerank threshold | 0.35 text / 0.25 code | **0.35**, configurable | Text is the spec; sigmoid scores from bge-reranker-v2-m3 |
| Dense candidates | K=25 text / 20 code | **25 dense + 25 BM25 → RRF**, top 15 children | Sample code skipped BM25/RRF entirely |
| What is reranked | parent (text) / child (code) | **Parent chunks from SQLite** | Full derivations reach the reranker and LLM |
| Sparse index | Qdrant sparse / bm25s | **bm25s per course**, rebuilt from SQLite on change | Custom math tokenizer, always consistent with SQLite |
| Database | study_tracker.db / metadata.sqlite | **One `storage/metadata.sqlite`** | Single source of truth, FK cascades |
| Citation format | `[Page X]` / `[Source: f, p. N]` | **`[Source: Lecture_04.pdf, p. 12]`** | Multi-document courses need the file name |
| LLM | Llama-3.1-8B / qwen2.5:7b | **qwen2.5:7b via Ollama** (any OpenAI-compatible server works) | User choice |
| Embedding size | 1024 hard-coded | **Read from the model**; collection name includes the model | Switching models never mixes vector spaces |
| Remaining 30% of quiz slots | unspecified | Topic quiz: next most relevant sections; course quiz: uniform random | Coverage while 70% target weak spots |
| Quiz length / levels | unspecified | 1–10 (default 5), any subset of Recall/Conceptual/Application | — |
| Distractor traps | prompt only | Each option carries `trap_type`; validated | Verifiable and shown to the student |
| Mastery on retries | unspecified | Only the **first attempt** updates mastery | Matches "S = 1 if correct on first try" |
| Benchmark bar | unspecified | Hit@5 ≥ 0.8 and MRR ≥ 0.7 | Recorded in `benchmarks/results/` |
| Parent size | headers *and* 800–1200 tokens | Split on `#`–`###` (or per slide), merge small neighbours up to ~800, hard cap 1200 | Both rules hold |
| Exact quote | "Exact quote from notes" | Notes are shown to Qwen as numbered sentences `[S#]`; it returns `evidence_ids` and the quote is assembled from those sentences | A 7B model paraphrases quotes (1/3 questions passed); with ids 3/3 pass and quotes are verbatim by construction |
| Evidence relevance | — | The correct option must share ≥25% of its terms with its evidence | Rejects questions that cite an unrelated sentence |

## Grounded citation verification

Every streamed answer is checked after generation (`generation/citations.py`):

1. **Source check** — each `[Source: file, p. N]` must name a retrieved document and a page inside the retrieved section; otherwise it is shown struck through.
2. **Page check** — if the claim's words are clearly on another page of that section, the chip is flagged (`p.2 → 1`) and opens the page that actually supports it.
3. **Highlight** — the chip opens the PDF with the single paragraph that best matches the claim highlighted.
4. **Completion** — sentences the model left uncited are matched against the retrieved passages; a strong match gets an `auto` citation, anything else is reported as an *unsupported claim* (a hallucination signal shown on the answer's badge).

## Acceptance criteria → implementation → tests

| AC | Where | Tests |
|---|---|---|
| 1–4 PDF/slides/scans, LaTeX, GFM tables, page+bbox | `ingestion/parser.py` (Docling + PyMuPDF/RapidOCR fallback) | `unit/test_parser.py`, `live::test_docling_ingestion…` |
| 5 hard PDFs inspected | `samples.py` (math images, tables, slides, handwriting) | `e2e::test_uc1…`, Playwright "first visit…" |
| 6–10 parent/child chunks, overlap, prefix, atomic blocks, metadata | `ingestion/chunker.py` | `unit/test_chunker.py` |
| 11–14 BGE-M3/Qdrant, math-aware BM25, idempotent, payload | `retrieval/indexer.py`, `sparse.py`, `lexical.py` | `unit/test_retrieval.py`, `unit/test_lexical_fusion.py` |
| 15–21 expansion, 25+25, RRF k=60, parent promotion, rerank, top-5 > τ, Not Found | `retrieval/search.py`, `fusion.py` | `unit/test_retrieval.py`, `e2e::test_uc2…uc5b` |
| 22 benchmark | `benchmark.py`, `scripts/benchmark.py` | `live::test_retrieval_benchmark` |
| 23–28 grounded, cited, KaTeX, SSE, clickable citations, provider switch | `generation/chat_engine.py`, `citations.py`, `llm.py`, UI `Chat.tsx`, `PdfViewer.tsx` | `unit/test_chat_engine.py`, `unit/test_llm.py`, `Chat.test.tsx`, Playwright "grounded chat…" |
| 29–36 schema, one correct, rationales, Bloom, traps, verbatim quote, LaTeX, grading | `generation/quiz_engine.py`, `latex.py` | `unit/test_quiz_engine.py`, `Quiz.test.tsx`, Playwright "quiz…" |
| 37–40 mastery init/update, history, 70% weighted sampling | `tracking/mastery.py`, `quiz_engine.select_contexts` | `unit/test_mastery.py`, `e2e::test_uc7/uc8` |
| 41–44 API, async ingestion, 3-tab UI + viewer, settings | `api/server.py`, `ingestion/pipeline.py`, `frontend/`, `config/settings.py` | `unit/test_api.py`, `unit/test_pipeline.py`, `App.test.tsx`, Playwright |

Extra endpoints the plan was missing: answer submission, upload status (`GET /api/courses/{id}/documents`),
delete/re-process documents, delete courses, mastery summary, quiz summary, search debugging, sample course.

## Layout

```
course_notes_rag/
  config/settings.py      model names, chunk sizes, thresholds, paths (CNR_* env overrides)
  ingestion/              parser.py · chunker.py · pipeline.py (background jobs, status)
  retrieval/              encoders.py · indexer.py · sparse.py · lexical.py · fusion.py · search.py
  generation/             llm.py · prompts.py · chat_engine.py · citations.py · quiz_engine.py · latex.py
  tracking/mastery.py     mastery update rule and adaptive sampling
  storage/db.py           SQLite schema and queries
  api/server.py           FastAPI (+ serves the built UI)
frontend/                 React + Vite UI, Vitest unit tests, Playwright e2e
tests/                    unit · e2e · live
scripts/                  serve.py · benchmark.py
```
