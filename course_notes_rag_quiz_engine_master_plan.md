# End-to-End Implementation Master Plan: Course Notes RAG & Adaptive Quiz Engine

This blueprint details the complete implementation of an intelligent academic study assistant. Unlike basic "PDF wrapper" tutorials that blindly split text by character count, this architecture implements **Layout-Aware Document Parsing** (preserving mathematical formulas, tables, and slide structures), **Hybrid Dense-Sparse Retrieval with Cross-Encoder Reranking**, **Grounded Citation Verification**, and **Structured Adaptive Quiz Generation**.

---

## 1. System Architecture Overview

The pipeline is divided into two distinct workflows: **The Ingestion & Indexing Pipeline** (Asynchronous) and **The Retrieval, QA & Quiz Synthesis Pipeline** (Real-Time).

```
┌────────────────────────────────────────────────────────────────────────┐
│                 1. INGESTION & LAYOUT-AWARE PARSING                    │
│  - Input: Lecture PDFs, Beamer Slides, Scanned Handwritten Notes       │
│  - Vision-Language / Layout Parser (Docling / Marker / PyMuPDF)        │
│  - Extracts: Markdown Text + LaTeX Math ($...$) + Structured Tables    │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ Hierarchical Parent-Child Chunks
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│                    2. DUAL-INDEX STORAGE LAYER                         │
│  - Dense Vector Index (Qdrant / Chroma): BGE-M3 Embeddings             │
│  - Sparse Lexical Index (BM25s): Exact keyword & equation matching     │
│  - Document Store (SQLite): Parent chunks, page bounds, & user metrics │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ User Query / Topic Selection
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│               3. HYBRID RETRIEVAL & RERANKING ENGINE                   │
│  - Query Expansion (Multi-Query / HyDE)                                │
│  - Reciprocal Rank Fusion (RRF) combining Dense + BM25 results         │
│  - Cross-Encoder Reranker (bge-reranker-v2-m3) -> Top-K Context        │
└─────────────────┬───────────────────────────────────┬──────────────────┘
                  │ (Chat Mode)                       │ (Quiz Mode)
                  ▼                                   ▼
┌───────────────────────────────────┐ ┌──────────────────────────────────┐
│     4A. GROUNDED CHAT ENGINE      │ │  4B. STRUCTURED QUIZ GENERATOR   │
│  - Synthesizes answer from Top-K  │ │  - Pydantic JSON Schema Enforcer │
│  - Enforces inline [Page X] cites │ │  - Bloom's Taxonomy calibration  │
│  - Streams response via SSE       │ │  - Distractor rationale synthesis│
└───────────────────────────────────┘ └──────────────────────────────────┘
```

---

## 2. Phase 1: Layout-Aware Document Ingestion & Chunking

Standard text splitters (`RecursiveCharacterTextSplitter`) destroy academic lecture notes by slicing equations in half and scrambling table columns. We use a layout-aware vision and PDF structure pipeline.

### 1.1 Document Parsing (`ingestion/parser.py`)
* **Engine:** Use `docling` or `pymupdf4llm` to parse PDFs into clean, structured Markdown.
* **Capabilities:**
  * **OCR & Formula Detection:** Converts embedded mathematical equations into standard LaTeX blocks (`$$...$$`).
  * **Table Preservation:** Converts multi-column lecture tables into GitHub-flavored Markdown tables so the LLM understands row-column relationships.
  * **Bounding Box Metadata:** Retains `(page_number, bbox_x0, bbox_y0, bbox_x1, bbox_y1)` for every extracted block so the UI can highlight the exact paragraph on the PDF viewer.

### 1.2 Parent-Child Hierarchical Chunking (`ingestion/chunker.py`)
Academic retrieval requires fine-grained search accuracy *and* broad surrounding context for generation:
1. **Parent Chunks (Context Level):** Split the Markdown document by structural headers (`#`, `##`, `###`) or full lecture slides (approx. $800\text{--}1200$ tokens). Stored in SQLite with a unique `parent_id`.
2. **Child Chunks (Search Level):** Split each Parent Chunk into smaller semantic units ($200\text{--}300$ tokens) with a $50$-token overlap. Each Child Chunk embeds its parent's section title as a prefix:
   $$\text{Chunk}_{\text{text}} = \text{"[Course: CS229 | Lecture 4: SVMs | Section: KKT Conditions] "} + \text{raw\_content}$$

---

## 3. Phase 2: Hybrid Indexing & Vector Storage

Pure vector search struggles with exact academic terminology, theorem numbers (e.g., *"Lemma 3.2"*), and variable symbols. Hybrid search solves this.

### 2.1 Embedding & Lexical Pipeline (`retrieval/indexer.py`)
* **Dense Embeddings:** Use `BAAI/bge-m3` or `nomic-embed-text-v1.5` (supports 8192 context length and strong academic/technical retrieval).
* **Sparse Lexical Search:** Use `bm25s` (high-speed pure Python BM25 implementation) tokenized with custom rules that preserve mathematical symbols and Greek letters.
* **Vector Database:** Local **Qdrant** (via Docker or in-memory local mode) storing both dense vectors and sparse vector weights in a single collection.

```python
from qdrant_client import QdrantClient, models
from sentence_transformers import SentenceTransformer

class CourseNotesIndexer:
    def __init__(self, collection_name="course_notes"):
        self.client = QdrantClient(path="./storage/qdrant_db")
        self.encoder = SentenceTransformer("BAAI/bge-m3")
        self.collection = collection_name
        self._init_collection()

    def _init_collection(self):
        if not self.client.collection_exists(self.collection):
            self.client.create_collection(
                collection_name=self.collection,
                vectors_config=models.VectorParams(
                    size=1024, distance=models.Distance.COSINE
                ),
            )

    def index_chunks(self, chunks: list[dict]):
        texts = [c["contextualized_text"] for c in chunks]
        embeddings = self.encoder.encode(texts, normalize_embeddings=True)
        
        points = [
            models.PointStruct(
                id=c["chunk_id"],
                vector=embeddings[i].tolist(),
                payload={
                    "parent_id": c["parent_id"],
                    "raw_text": c["raw_text"],
                    "course_id": c["course_id"],
                    "pdf_name": c["pdf_name"],
                    "page_num": c["page_num"],
                    "section_title": c["section_title"]
                }
            )
            for i, c in enumerate(chunks)
        ]
        self.client.upsert(collection_name=self.collection, points=points)
```

---

## 4. Phase 3: Advanced Retrieval & Cross-Encoder Reranking

When a student asks a question (e.g., *"Why does vanishing gradient happen in RNNs and how do LSTMs fix it?"*), the system executes a 3-step retrieval funnel.

### 3.1 Reciprocal Rank Fusion (RRF)
1. **Dense Query:** Retrieve top $K=25$ child chunks via cosine similarity in Qdrant.
2. **Sparse Query:** Retrieve top $K=25$ child chunks via BM25 keyword matching.
3. **RRF Merge:** Combine rankings using the Reciprocal Rank Fusion formula:
   $$\text{RRF\_Score}(d \in D) = \sum_{m \in \{\text{Dense}, \text{BM25}\}} \frac{1}{k + r_m(d)}, \quad k = 60$$

### 3.2 Parent Promotion & Cross-Encoder Reranking (`retrieval/search.py`)
1. Map the top 15 merged child chunks back to their unique `parent_id` chunks in SQLite so the LLM receives complete derivations and surrounding paragraphs.
2. Pass the `(User Query, Parent Chunk)` pairs through a **Cross-Encoder** (`BAAI/bge-reranker-v2-m3`).
3. Retain the top $N=5$ chunks with reranker score $\tau > 0.35$. If all chunks score below $\tau$, trigger an explicit **"Not Found in Course Notes"** fallback to prevent hallucination.

```python
from sentence_transformers import CrossEncoder

class RerankingRetriever:
    def __init__(self, indexer, db_conn):
        self.indexer = indexer
        self.db = db_conn
        self.reranker = CrossEncoder("BAAI/bge-reranker-v2-m3")

    def retrieve(self, query: str, course_id: str, top_k: int = 5):
        query_vec = self.indexer.encoder.encode(query, normalize_embeddings=True).tolist()
        
        # 1. Fetch initial candidates from Qdrant
        hits = self.indexer.client.search(
            collection_name=self.indexer.collection,
            query_vector=query_vec,
            limit=20
        )
        
        # 2. Deduplicate by parent_id and fetch full parent context
        seen_parents = {}
        for hit in hits:
            pid = hit.payload["parent_id"]
            if pid not in seen_parents:
                seen_parents[pid] = hit.payload

        candidates = list(seen_parents.values())
        if not candidates:
            return []

        # 3. Cross-Encoder Reranking
        pairs = [[query, c["raw_text"]] for c in candidates]
        scores = self.reranker.predict(pairs)
        
        for i, c in enumerate(candidates):
            c["rerank_score"] = float(scores[i])
            
        ranked = sorted(candidates, key=lambda x: x["rerank_score"], reverse=True)
        return [r for r in ranked[:top_k] if r["rerank_score"] > 0.25]
```

---

## 5. Phase 4: Grounded QA & Structured Quiz Synthesis

### 4.1 Grounded Chat Mode (`generation/chat_engine.py`)
* **Strict System Prompting:** Instructs the LLM (local Llama-3.1-8B via Ollama/vLLM or Gemini/OpenAI API) to answer *exclusively* using the provided context blocks.
* **Inline Citation Format:** Every claim or formula must end with `[Source: Lecture_04.pdf, p. 12]`.
* **Math Rendering:** Enforces `$inline$` and `$$display$$` LaTeX syntax for seamless frontend rendering via KaTeX.

### 4.2 Structured Quiz Generator (`generation/quiz_engine.py`)
Instead of asking the LLM to "write a quiz in plain text" (which breaks UI parsers), use **Structured Outputs (Pydantic Schema Enforcement)** to generate interactive assessments calibrated to **Bloom's Taxonomy** (*Recall*, *Conceptual Understanding*, *Application/Calculation*).

```python
from pydantic import BaseModel, Field
from typing import List, Literal

class QuizOption(BaseModel):
    label: Literal["A", "B", "C", "D"]
    text: str = Field(description="Option text, using LaTeX $...$ for math.")
    is_correct: bool
    misconception_explanation: str = Field(
        description="Why this option is right, or what specific student misconception this distractor targets."
    )

class QuizQuestion(BaseModel):
    question_id: int
    bloom_level: Literal["Recall", "Conceptual", "Application"]
    question_stem: str
    options: List[QuizOption] = Field(min_length=4, max_length=4)
    hint: str
    source_pdf: str
    source_page: int
    source_quote: str = Field(description="Exact quote from notes proving the answer.")

class GeneratedQuiz(BaseModel):
    topic: str
    questions: List[QuizQuestion]
```

#### How Distractors Are Engineered
The prompt instructs the model to design the 3 incorrect options (`is_correct: False`) using specific pedagogical traps:
1. **Sign/Formula Inversion:** Flipping a sign or exponent from a lecture equation.
2. **Boundary Condition Violation:** Applying a theorem where its prerequisites (e.g., convexity or independence) do not hold.
3. **Terminology Swap:** Confusing closely related concepts from the same lecture.

---

## 6. Phase 5: Spaced Repetition & Weak-Spot Tracking

To make the quiz engine adaptive rather than static, track user performance in SQLite (`storage/study_tracker.db`):

* **Concept Mastery Score ($M_c$):** Every parent chunk/section is assigned a mastery weight $M_c \in [0, 1]$, initialized at $0.5$.
* **Update Rule (Modified SM-2 / Elo):**
  * When a student answers a quiz question sourced from chunk $c$:
    $$M_c \leftarrow \text{clip}\left(M_c + \alpha \cdot (S - M_c), \, 0.0, \, 1.0\right)$$
    where $S = 1.0$ if correct (on first try), $S = 0.0$ if incorrect, and $\alpha = 0.25$.
* **Adaptive Quiz Sampling:** When the user clicks *"Generate Practice Quiz"*, 70% of retrieved context chunks are sampled with probability inversely proportional to $M_c$:
  $$P(\text{select chunk } c) \propto (1.05 - M_c)^2$$
  This automatically targets the student's weakest lecture sections.

---

## 7. Directory Structure & File Map

```
course_notes_rag/
├── config/
│   └── settings.py                 # Model names, chunk sizes, DB paths
├── storage/
│   ├── qdrant_db/                  # Local vector + sparse index storage
│   └── metadata.sqlite             # Parent chunks, quiz history, mastery scores
├── ingestion/
│   ├── parser.py                   # Docling / PyMuPDF layout + LaTeX extractor
│   └── chunker.py                  # Parent-Child hierarchical markdown splitter
├── retrieval/
│   ├── indexer.py                  # BGE-M3 dense + BM25s sparse indexing
│   └── search.py                   # Hybrid RRF + Cross-Encoder reranking
├── generation/
│   ├── prompts.py                  # Grounded QA & Bloom's Taxonomy prompts
│   ├── chat_engine.py              # Streaming RAG response generator
│   └── quiz_engine.py              # Pydantic-enforced quiz builder & grader
├── api/
│   └── server.py                   # FastAPI backend endpoints (/upload, /chat, /quiz)
└── ui/
    └── app.py                      # Streamlit or Next.js split-screen PDF + Chat/Quiz UI
```

---

## 8. 2-Week Implementation Roadmap

* **Days 1–3: Layout Parsing & Hierarchical Chunking**
  * Set up Python environment (`pip install docling pymupdf4llm qdrant-client sentence-transformers pydantic fastapi`).
  * Build `parser.py` and `chunker.py`. Test on 3 complex lecture PDFs containing math equations and tables; inspect the output Markdown to verify equations remain intact.
* **Days 4–6: Hybrid Vector Store & Reranker**
  * Implement `indexer.py` with Qdrant and `BAAI/bge-m3`.
  * Implement `search.py` with `bge-reranker-v2-m3`. Benchmark retrieval quality on 10 tricky lecture questions.
* **Days 7–10: Grounded Chat & Structured Quiz Engine**
  * Connect an LLM provider (Ollama running `qwen2.5:7b` locally, or Gemini/OpenAI API via `instructor` or native structured outputs).
  * Build `quiz_engine.py` using the `GeneratedQuiz` Pydantic schema. Verify that every generated question includes valid LaTeX, 4 options, misconception rationales, and exact page citations.
* **Days 11–14: Adaptive Tracker & Split-Screen UI**
  * Implement the SQLite mastery table to record quiz attempts and weight future quiz generation toward weak topics.
  * Build the frontend interface featuring a 3-tab workspace: **Document Library**, **Grounded Chat (with clickable page citations)**, and **Interactive Quiz Mode**.