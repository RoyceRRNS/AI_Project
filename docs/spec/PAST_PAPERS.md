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
| Topic | `HybridRetriever` on the question with its command words removed, threshold 0.2 (exam wording differs from note wording). A question with data is also searched without its numbers and by its last sentence, and the best score wins. No match is reported as a gap in the notes |

If the model fails, the rule labels stand and the paper still finishes.

Numbers drown a question's subject for the reranker: "the variance of a Bernoulli variable when p is 0.25" scored 0.06 as written and 0.80 without the 0.25. Sections unrelated to a question score about 0.02, so trying the variants does not pull in wrong matches.

## Blueprint

| Output | Detail |
|---|---|
| Topic weightage | Marks and frequency per lecture and section. Sides of an OR split their marks, so shares add up to the paper's real total |
| Expected marks | As above, weighted 0.8 per year behind the newest paper |
| Type and Bloom mix | Share of marks |
| Structure | Parts, pattern (5 × 2), totals, either/or and "answer any"; the most common pattern across papers |
| Recurring questions | Clusters around a leader question, so similarity cannot chain. Words and embeddings only shortlist pairs; `bge-reranker-v2-m3` decides, reading each pair in both directions (see below) |
| Phrasings | Examiner templates such as "Explain … with a neat diagram", each with real questions |

Embeddings alone cannot tell a repeat from a neighbouring question. "Define the geometric margin" and "Define the functional margin" score cosine 0.91, above a genuine rephrasing of an LSTM question at 0.83. Read as a pair by the cross-encoder, using the lower of the two directions, the 7 repeats measured scored at least 0.73 and the 7 different questions at most 0.69, so the line is at 0.7. That gap is narrow and comes from a small sample. The offline profile has no cross-encoder and uses word overlap and question type instead.

Deleting a note section clears `parent_id` but keeps the stored title. That is how stale mappings are detected, and "Match again" re-runs the mapping.

## Revision plan

Computed only, with no model text, so every line traces back to past questions.

`priority = expected marks × (0.5 + 0.5 × frequency) × (1.5 − mastery)`, scaled to 100. Tiers follow the marks covered: must revise up to 60%, should revise up to 85%. Each section's focus (numericals, derivations, algorithm tracing, diagrams, case studies, theory) is the practice mode with the most marks there. Mastery comes from the quiz. With a number of days, sections are spread across them and the last day is kept for a timed mock.

## Mock papers and practice sets

A mock copies the template paper's parts, marks, OR choices and question types. A practice set takes a count, optional types and an optional topic. If the template has no numerical or application question, the biggest theory questions are converted, which keeps the marks pattern.

Sections are drawn by weightage, without repeats while another section fits, and only from sections that suit the question type: a numerical needs mathematics in the section.

| Check | Rule |
|---|---|
| Grounding | Notes are shown as `[S#]` sentences (`generation/evidence.py`); steps cite ids, and `[Source: file.pdf, p. N]` is built from those ids, so a citation cannot name a page the model made up. `verify_citations` produces the report the UI renders, with the highlight set to the cited sentences |
| Numerical answers | `answer_expression` is evaluated by a safe arithmetic evaluator (an AST walk, no `eval`) and must match the stated answer within 2%; it must also use numbers from the question |
| Worked solutions | For numerical, algorithm and derivation questions, at least half the steps must produce a new value or equation; repeating the question does not count. For every type, most steps must not be instructions to the student. Qwen otherwise tends to list what to do ("set up the KKT conditions") and state an unsupported answer |
| Step arithmetic | For numerical and algorithm questions, every equality between plain numbers in a step is recomputed, and a number in the final answer must appear in the working. This caught a trace that named its calculations and then claimed 3.2, when the answer is 3.64 |
| Style | Each type has content checks, for example a case-based question needs a scenario of 25+ words |
| Originality | A draft 90% like a past question, or 80% like another mock question, goes back |
| Repair, then retry | Follows `quiz_engine.py`: fix LaTeX escapes, turn `[S#]` written into the text into citations, drop unknown or unrelated evidence, re-select evidence, scale the marking scheme; send anything else back with the list of problems, raising the temperature each retry; then try another section; then skip with a warning |

## What the checks cannot see

Validation proves a solution is grounded and that its stated arithmetic is consistent. It does not prove the reasoning is right, and two live runs showed the gap:

- A numerical question gave a gradient norm of 16, and the solution then used 16 as the time step. The citations were valid and the arithmetic matched.
- A gradient descent trace went from $1 - \frac{1}{2} \cdot 2(1 - 3)$, which is 3, to $1 - 2 = -1$ in the next step. Each line holds on its own, so the checks pass, and the trace reached the right final value by accident.

A model can also state a formula that is not in the notes or make a false claim in an uncited step. The UI therefore says "Arithmetic rechecked", not "correct", and a student should treat a generated solution as a draft to check against the cited pages.

Topic mapping picks one section per question, so a question spanning two sections counts towards the better match. Close calls can land on the neighbouring section: the LSTM diagram question scored 0.32 for the GRU section and 0.31 for the LSTM section.

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
