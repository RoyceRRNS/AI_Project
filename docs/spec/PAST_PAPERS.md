# Past question papers

The Papers tab shows how a course's exam is set and what to do about it. The student uploads previous papers (digital or scanned PDFs) and gets a blueprint of the exam, a revision plan built around it, and mock papers in the same style.

```
paper PDF ─► shared parser (keep_markers) ─► extract: number, part, marks, sub-questions, OR choices
          ─► classify: question type + Bloom (rules, model only when unsure, printed level wins)
          ─► map: HybridRetriever finds the section of the notes, or reports a gap
          ─► blueprint ─► revision plan (+ mastery)
                       └► mock paper / practice set ─► Qwen, [S#] evidence ─► validate, repair, retry
```

Code lives in `course_notes_rag/papers/`, with tables in `storage/papers.py`, routes in `api/papers.py` and the tab in `frontend/src/components/Papers.tsx` and `components/papers/`.

## Reading a paper

`extract.py` reads the blocks the shared parser produces, so Docling, PyMuPDF and RapidOCR all work unchanged. The parser takes `keep_markers=True` for papers: by default it strips "1." and "(a)" from list items, and in an exam those are data. That flag also keeps line breaks and turns off display-formula detection, because "Compute P(A) = 0.3" is a question and not an equation.

| Layout | How it is read |
|---|---|
| Numbered text | A line-by-line state machine for parts, questions, (a)/(b), (i)/(ii) and OR |
| Question table | Columns found from the header (Q.No, Sub, Question, Marks, CO, BL), or inferred if there is none |
| Scan | OCR text, read like numbered text |
| Anything else | If the rules find fewer than 3 questions, Qwen extracts them, and each is kept only if at least 85% of its terms are in the paper |

Each stored question is a leaf, the unit a student answers. A stem shared by (a) and (b) becomes their `context`. Sides of an OR share a `choice_group`. Marks may come from the question, a stem, or the part header; marks shared out across sub-questions are flagged `marks_estimated`, and missing marks are reported, never invented.

## Classifying

| Label | Source |
|---|---|
| Question type (7) | Command-word rules. Clear cases never reach the model; ties and questions with no cue go to Qwen in batches |
| Bloom level (6) | The level printed on the paper if there is one, otherwise verbs, with floors by question type |
| Topic | `HybridRetriever` on the question with its command words removed, threshold 0.2 (exam wording differs from note wording). No match is reported as a gap in the notes |

If the model fails, the rule labels stand and the paper still finishes.

## Blueprint

| Output | Detail |
|---|---|
| Topic weightage | Marks and frequency per lecture and section. Sides of an OR split their marks, so shares add up to the paper's real total |
| Expected marks | As above, weighted 0.8 per year behind the newest paper |
| Type and Bloom mix | Share of marks |
| Structure | Parts, pattern (5 × 2), totals, either/or and "answer any"; the most common pattern across papers |
| Recurring questions | Clusters around a leader question, so similarity cannot chain. Near-identical wording or very high cosine links on its own; anything weaker must also be the same question type |
| Phrasings | Examiner templates such as "Explain … with a neat diagram", each with real questions |

Deleting a note section clears `parent_id` but keeps the stored title. That is how stale mappings are detected, and "Match again" re-runs the mapping.

## Revision plan

Computed only, with no model text, so every line traces back to past questions.

`priority = expected marks × (0.5 + 0.5 × frequency) × (1.5 − mastery)`, scaled to 100. Tiers follow the marks covered: must revise up to 60%, should revise up to 85%. Each section's focus (numericals, derivations, algorithm tracing, diagrams, case studies, theory) is the practice mode with the most marks there. Mastery comes from the quiz. With a number of days, sections are spread across them and the last day is kept for a timed mock.

## Mock papers and practice sets

A mock copies the template paper's parts, marks, OR choices and question types. A practice set takes a count, optional types and an optional topic. If the template has no numerical or application question, the biggest theory questions are converted, which keeps the marks pattern.

Sections are drawn by weightage, without repeats while another section fits, and only from sections that suit the question type: a numerical needs mathematics in the section.

| Check | Rule |
|---|---|
| Grounding | Notes are shown as `[S#]` sentences (`generation/evidence.py`); steps cite ids, and `[Source: file.pdf, p. N]` is built from those ids, so a citation cannot name a page the model made up. `verify_citations` then produces the report the UI renders |
| Numerical answers | `answer_expression` is evaluated by a safe arithmetic evaluator (an AST walk, no `eval`) and must match the stated answer within 2%; it must also use numbers from the question |
| Style | Each type has content checks, for example a case-based question needs a scenario of 25+ words |
| Originality | A draft 90% like a past question, or 80% like another mock question, goes back |
| Repair, then retry | Follows `quiz_engine.py`: fix LaTeX escapes, drop unknown or unrelated evidence, re-select evidence, scale the marking scheme; send anything else back with the list of problems, raising the temperature each retry; then try another section; then skip with a warning |

## API

| Route | Purpose |
|---|---|
| `GET, POST /api/courses/{id}/papers` | List, upload |
| `POST /api/courses/{id}/papers/sample` | Sample papers for the sample course |
| `POST /api/courses/{id}/papers/remap` | Match questions to the notes again |
| `GET /api/courses/{id}/papers/blueprint` | Blueprint |
| `GET /api/courses/{id}/papers/plan?days=N` | Revision plan |
| `GET, PATCH, DELETE /api/papers/{id}`, `POST .../reprocess`, `GET .../file` | One paper |
| `POST /api/papers/mock` | Generate; streams `mock`, `progress`, `question`, `warning`, `error`, `done` |
| `GET /api/courses/{id}/mocks`, `GET, DELETE /api/mocks/{id}` | Saved mocks |

## Tests

| Suite | Where |
|---|---|
| Unit | `tests/unit/test_paper_*.py` |
| Backend journey | `test_uc_past_papers_blueprint_plan_and_mock_paper` in `tests/e2e/test_user_journeys.py` |
| Live | `tests/live/test_live_papers.py` (`pytest -m live`) |
| Frontend unit | `frontend/src/components/Papers.test.tsx` |
| UI e2e | `frontend/e2e/papers.spec.ts`, `papers.mobile.spec.ts` |

The offline test profile answers these prompts through `papers/offline.py`, which `ExtractiveLLM` calls first.
