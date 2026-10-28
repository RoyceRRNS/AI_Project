# Acceptance Criteria: Course Notes RAG & Adaptive Quiz Engine

Derived from `course_notes_rag_quiz_engine_master_plan.md`.

## A. Parsing (`ingestion/parser.py`)
1. Takes lecture PDFs, Beamer slides and scanned handwritten notes (with OCR).
2. Produces Markdown. Equations come out as LaTeX (`$…$` inline, `$$…$$` display) and are never cut in half.
3. Tables come out as GitHub-style Markdown tables with rows and columns kept in place.
4. Every block keeps its page number and its position box on the page (bbox x0, y0, x1, y1).
5. Tested on 3 hard PDFs with math and tables, and the output was checked by hand.

## B. Chunking (`ingestion/chunker.py`)
6. Parent chunks split on `#`, `##`, `###` or on each slide, about 800–1200 tokens each, with a unique `parent_id`, stored in SQLite.
7. Child chunks are 200–300 tokens, overlap by 50 tokens, and stay inside one parent.
8. Each child's text starts with `[Course: … | Lecture: … | Section: …] `.
9. No chunk boundary cuts through a LaTeX block or a table.
10. Each child carries `chunk_id`, `parent_id`, `course_id`, `pdf_name`, `page_num`, `section_title`, `raw_text` and `contextualized_text`.

## C. Indexing (`retrieval/indexer.py`)
11. Dense vectors use BGE-M3 (1024 dimensions, normalized, cosine) in local Qdrant at `./storage/qdrant_db`.
12. Keyword search uses BM25 (`bm25s`). Its tokenizer keeps math symbols, Greek letters and references like "Lemma 3.2".
13. Creating the collection is safe to run twice, and re-indexing updates chunks without making duplicates.
14. Every stored point has all the metadata from #10.

## D. Retrieval (`retrieval/search.py`)
15. Query expansion (multi-query or HyDE) is available.
16. Takes the top 25 from dense search and the top 25 from BM25, limited to the chosen `course_id`.
17. Merges the two lists with Reciprocal Rank Fusion, k = 60.
18. Takes the top 15 merged children and replaces them with their full parent chunks from SQLite, with no duplicate parents.
19. Reranks (query, parent) pairs with `bge-reranker-v2-m3`.
20. Returns the top 5 that score above the threshold τ.
21. If nothing scores above τ, it returns "Not Found in Course Notes" and the LLM is not called.
22. A benchmark of 10 tricky questions is run and the results are recorded.

## E. Grounded chat (`generation/chat_engine.py`)
23. Answers use only the retrieved context.
24. Every claim or formula ends with `[Source: <pdf>, p. <n>]`, and that page must be in the retrieved context. Citations are checked automatically.
25. LaTeX displays correctly with KaTeX.
26. Answers stream over SSE.
27. Clicking a citation opens the PDF at that page and highlights the paragraph (using the bbox).
28. The LLM provider can be switched in config (Ollama/vLLM locally, or Gemini/OpenAI).

## F. Quiz (`generation/quiz_engine.py`)
29. Output passes the `GeneratedQuiz` schema. If it doesn't, it is retried or rejected.
30. Each question has exactly 4 options (A–D) and exactly one correct answer. The schema doesn't check the "exactly one" part, so it needs an extra validator.
31. Every option has a `misconception_explanation`.
32. `bloom_level` is one of Recall, Conceptual or Application.
33. Wrong options use the three traps: flipped sign or formula, theorem used where its conditions don't hold, and swapped terms.
34. Every question has `hint`, `source_pdf`, `source_page` and `source_quote`, and the quote appears word for word on that page.
35. All LaTeX is valid.
36. The grader records each answer and whether it was right on the first try.

## G. Adaptive tracking
37. Each parent chunk has a mastery score M between 0 and 1, starting at 0.5.
38. After each answer: M ← clip(M + 0.25·(S − M), 0, 1), where S = 1 only if correct on the first try.
39. Every attempt is saved as quiz history.
40. "Generate Practice Quiz" picks 70% of its chunks with probability ∝ (1.05 − M)², so weak sections come up more.

## H. API, UI and config
41. FastAPI exposes `/upload`, `/chat` and `/quiz`.
42. Uploading doesn't block: ingestion runs in the background.
43. The UI has 3 tabs (Document Library, Grounded Chat, Interactive Quiz) next to a PDF viewer.
44. `config/settings.py` holds model names, chunk sizes, DB paths and thresholds.

## Contradictions and gaps to settle first
1. **Rerank threshold:** the text says 0.35, the code uses 0.25.
2. **Dense top-K:** the text says 25, the code uses 20. The sample code also has no BM25, no RRF, no `course_id` filter, and reranks child `raw_text` instead of parent chunks.
3. **Where BM25 lives:** sparse vectors inside one Qdrant collection, or a separate `bm25s` index?
4. **Database file:** `storage/study_tracker.db` or `storage/metadata.sqlite`?
5. **Citation format:** `[Page X]` or `[Source: file, p. N]`?
6. **LLM:** Llama-3.1-8B or qwen2.5:7b?
7. **Embedding model:** the nomic alternative uses 768 dimensions, which doesn't match `size=1024`.
8. **Not specified:** how the other 30% of quiz chunks are picked, how many questions a quiz has, whether the user can choose Bloom levels, and what score the 10-question benchmark must reach.
9. **Missing endpoints:** submitting an answer, checking upload status, and deleting or re-uploading a document.
10. **Not addressed:** how fast responses must be, and how separate courses are kept apart.
