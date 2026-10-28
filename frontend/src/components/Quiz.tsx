import { useCallback, useEffect, useRef, useState } from "react";
import {
  ArrowRight, BookMarked, Brain, CheckCircle2, Lightbulb, Loader2, RotateCcw, Target, Trophy, XCircle, Zap,
} from "lucide-react";
import {
  api, type AnswerResult, type BloomLevel, type MasterySummary, type PublicQuestion, type TrapType,
} from "../api";
import { prettyPdfName } from "../citations";
import { useStore } from "../store";
import { MathText } from "./Markdown";
import { isTyping } from "../dom";

export const BLOOM: { level: BloomLevel; label: string; hint: string }[] = [
  { level: "Recall", label: "Recall", hint: "Definitions, facts and formulas" },
  { level: "Conceptual", label: "Conceptual", hint: "Why and how things work" },
  { level: "Application", label: "Application", hint: "Apply it to a new problem" },
];

export const TRAP_LABEL: Record<TrapType, string> = {
  correct: "Correct answer",
  sign_inversion: "Trap: sign / formula inversion",
  boundary_violation: "Trap: boundary-condition violation",
  terminology_swap: "Trap: terminology swap",
};

type Phase = "setup" | "running" | "results";

interface QuizState {
  quizId: string | null;
  topic: string;
  total: number;
  questions: PublicQuestion[];
  generating: boolean;
  progress: { index: number; section: string; level: string } | null;
  warnings: string[];
  error: string | null;
}

const EMPTY: QuizState = {
  quizId: null, topic: "", total: 0, questions: [], generating: false, progress: null, warnings: [], error: null,
};

const pct = (x: number) => `${Math.round(x * 100)}%`;

export function Quiz() {
  const { course, readyDocs, notify, bumpMastery } = useStore();
  const [phase, setPhase] = useState<Phase>("setup");
  const [topic, setTopic] = useState("");
  const [count, setCount] = useState(5);
  const [levels, setLevels] = useState<BloomLevel[]>(["Recall", "Conceptual", "Application"]);
  const [adaptive, setAdaptive] = useState(true);
  const [quiz, setQuiz] = useState<QuizState>(EMPTY);
  const [answers, setAnswers] = useState<Record<number, AnswerResult>>({});
  const [firstTry, setFirstTry] = useState<Record<number, boolean>>({});
  const [index, setIndex] = useState(0);
  const abortRef = useRef<AbortController | null>(null);

  // Switching course remounts this component (keyed by course in <App/>), which resets it.
  useEffect(() => () => abortRef.current?.abort(), []);

  const start = useCallback(
    async (override?: { topic?: string; adaptive?: boolean }) => {
      if (!course) return;
      const t = (override?.topic ?? topic).trim();
      const controller = new AbortController();
      abortRef.current = controller;
      setQuiz({ ...EMPTY, generating: true, total: count, topic: t });
      setAnswers({});
      setFirstTry({});
      setIndex(0);
      setPhase("running");
      try {
        await api.quiz(
          { course_id: course.id, topic: t || null, num_questions: count, bloom_levels: levels, adaptive: override?.adaptive ?? adaptive },
          (ev) => {
            const d = ev.data as Record<string, unknown>;
            if (ev.event === "quiz") setQuiz((q) => ({ ...q, quizId: String(d.quiz_id), topic: String(d.topic), total: Number(d.total) }));
            if (ev.event === "progress")
              setQuiz((q) => ({ ...q, progress: { index: Number(d.index), section: String(d.section), level: String(d.level) } }));
            if (ev.event === "question") setQuiz((q) => ({ ...q, questions: [...q.questions, d.question as PublicQuestion] }));
            if (ev.event === "warning") setQuiz((q) => ({ ...q, warnings: [...q.warnings, String(d.message)] }));
            if (ev.event === "error") setQuiz((q) => ({ ...q, error: String(d.message) }));
            if (ev.event === "done") setQuiz((q) => ({ ...q, generating: false, progress: null, total: q.questions.length || q.total }));
          },
          controller.signal,
        );
      } catch (e) {
        if ((e as Error).name !== "AbortError") {
          notify((e as Error).message, "error");
          setPhase("setup");
        }
      } finally {
        setQuiz((q) => ({ ...q, generating: false, progress: null }));
      }
    },
    [course, topic, count, levels, adaptive, notify],
  );

  const submit = async (q: PublicQuestion, label: string) => {
    if (!quiz.quizId) return;
    try {
      const res = await api.answer(quiz.quizId, q.question_id, label);
      setAnswers((a) => ({ ...a, [q.question_id]: res }));
      if (res.first_try) setFirstTry((f) => ({ ...f, [q.question_id]: res.is_correct }));
      if (res.mastery_updated) bumpMastery();
    } catch (e) {
      notify((e as Error).message, "error");
    }
  };

  if (!course) return null;
  const noDocs = readyDocs.length === 0;

  return (
    <div className="quiz-layout">
      <div className="quiz-main">
        {phase === "setup" && (
          <div className="card quiz-setup">
            <h2><Brain size={20} /> Practice quiz</h2>
            <p className="muted">
              Questions are generated from your notes, calibrated to Bloom's taxonomy, and every answer is backed by an
              exact quote from the lecture.
            </p>
            <label className="field">
              <span>Topic <em className="muted">(optional)</em></span>
              <input
                value={topic}
                onChange={(e) => setTopic(e.target.value)}
                placeholder="e.g. KKT conditions, LSTM gates — leave empty to mix your whole course"
                maxLength={300}
                aria-label="Quiz topic"
              />
            </label>
            <label className="field">
              <span>Questions: <strong>{count}</strong></span>
              <input type="range" min={1} max={10} value={count} onChange={(e) => setCount(Number(e.target.value))} aria-label="Number of questions" />
            </label>
            <div className="field">
              <span>Bloom's levels</span>
              <div className="seg">
                {BLOOM.map((b) => {
                  const on = levels.includes(b.level);
                  return (
                    <button
                      key={b.level}
                      className={`seg-btn ${on ? "on" : ""}`}
                      aria-pressed={on}
                      title={b.hint}
                      onClick={() => setLevels(on ? levels.filter((l) => l !== b.level) : [...levels, b.level])}
                    >
                      {b.label}
                    </button>
                  );
                })}
              </div>
              {levels.length === 0 && <span className="warn small">Pick at least one level.</span>}
            </div>
            <label className="switch">
              <input type="checkbox" checked={adaptive} onChange={(e) => setAdaptive(e.target.checked)} />
              <span className="slider" />
              <span>
                <strong>Focus on weak spots</strong>
                <span className="muted small"> 70% of questions come from the sections you know least</span>
              </span>
            </label>
            <button
              className="btn primary lg"
              disabled={noDocs || levels.length === 0}
              onClick={() => void start()}
            >
              <Zap size={16} /> Generate quiz
            </button>
            {noDocs && <p className="notice">Upload and process notes in the Library first.</p>}
          </div>
        )}

        {phase === "running" && (
          <QuizRunner
            quiz={quiz}
            index={index}
            setIndex={setIndex}
            answers={answers}
            onSubmit={submit}
            onFinish={() => setPhase("results")}
            onCancel={() => {
              abortRef.current?.abort();
              setPhase("setup");
            }}
          />
        )}

        {phase === "results" && (
          <Results
            quiz={quiz}
            firstTry={firstTry}
            onRestart={() => setPhase("setup")}
            onWeakSpots={() => {
              setTopic("");
              setAdaptive(true);
              void start({ topic: "", adaptive: true });
            }}
          />
        )}
      </div>
      <WeakSpots onPractice={(section) => { setTopic(section); void start({ topic: section, adaptive: false }); }} />
    </div>
  );
}

function QuizRunner({
  quiz, index, setIndex, answers, onSubmit, onFinish, onCancel,
}: {
  quiz: QuizState;
  index: number;
  setIndex: (i: number) => void;
  answers: Record<number, AnswerResult>;
  onSubmit: (q: PublicQuestion, label: string) => Promise<void>;
  onFinish: () => void;
  onCancel: () => void;
}) {
  const q = quiz.questions[index];
  const total = Math.max(quiz.total, quiz.questions.length);

  if (!q) {
    if (!quiz.generating && quiz.questions.length === 0) {
      return (
        <div className="card">
          <h3><XCircle size={18} /> No questions could be generated</h3>
          <p className="muted">{quiz.error ?? "Try another topic, or check that the model is running."}</p>
          {quiz.warnings.map((w) => <p key={w} className="warn small">{w}</p>)}
          <button className="btn ghost" onClick={onCancel}>Back to setup</button>
        </div>
      );
    }
    return (
      <div className="card generating" data-testid="generating">
        <Loader2 size={22} className="spin" />
        <div>
          <strong>Generating question {quiz.progress?.index ?? 1} of {total}…</strong>
          {quiz.progress && (
            <div className="muted small">{quiz.progress.level} · {quiz.progress.section}</div>
          )}
          <div className="progress"><div style={{ width: `${((quiz.progress?.index ?? 0.5) / Math.max(1, total)) * 100}%` }} /></div>
        </div>
        <button className="btn ghost sm" onClick={onCancel}>Cancel</button>
      </div>
    );
  }

  return (
    <div className="runner">
      <div className="runner-head">
        <div>
          <span className="muted small">{quiz.topic}</span>
          <div className="dots" aria-label="Question progress">
            {Array.from({ length: total }).map((_, i) => {
              const qq = quiz.questions[i];
              const a = qq && answers[qq.question_id];
              const cls = !qq ? "pending" : a ? (a.is_correct ? "right" : "wrong") : i === index ? "current" : "";
              return (
                <button
                  key={i}
                  className={`dot ${cls}`}
                  disabled={!qq}
                  onClick={() => setIndex(i)}
                  aria-label={`Question ${i + 1}`}
                  aria-current={i === index}
                />
              );
            })}
          </div>
        </div>
        {quiz.generating && (
          <span className="muted small"><Loader2 size={13} className="spin" /> {quiz.questions.length}/{total} ready</span>
        )}
      </div>
      <QuestionCard
        key={`${q.question_id}`}
        q={q}
        number={index + 1}
        total={total}
        result={answers[q.question_id]}
        onSubmit={(label) => onSubmit(q, label)}
        onNext={() => (index + 1 < quiz.questions.length ? setIndex(index + 1) : onFinish())}
        isLast={index + 1 >= total && !quiz.generating}
        nextReady={index + 1 < quiz.questions.length || !quiz.generating}
      />
      {quiz.warnings.map((w) => <p key={w} className="warn small">{w}</p>)}
    </div>
  );
}

export function QuestionCard({
  q, number, total, result, onSubmit, onNext, isLast, nextReady,
}: {
  q: PublicQuestion;
  number: number;
  total: number;
  result?: AnswerResult;
  onSubmit: (label: string) => Promise<void>;
  onNext: () => void;
  isLast: boolean;
  nextReady: boolean;
}) {
  const { openViewer } = useStore();
  const [selected, setSelected] = useState<string | null>(null);
  const [showHint, setShowHint] = useState(false);
  const [checking, setChecking] = useState(false);
  const [retrying, setRetrying] = useState(false);
  const answered = !!result && !retrying;

  const check = useCallback(async () => {
    if (!selected || checking) return;
    setChecking(true);
    await onSubmit(selected);
    setChecking(false);
    setRetrying(false);
  }, [selected, checking, onSubmit]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (isTyping(e.target)) return;
      const k = e.key.toUpperCase();
      const map: Record<string, string> = { "1": "A", "2": "B", "3": "C", "4": "D", A: "A", B: "B", C: "C", D: "D" };
      if (!answered && map[k]) setSelected(map[k]);
      if (e.key === "Enter") {
        if (!answered && selected) void check();
        else if (answered && nextReady) onNext();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [answered, selected, check, onNext, nextReady]);

  const graded = answered ? result!.options : null;
  const masteryDelta =
    result?.mastery_updated && result.mastery_before !== null && result.mastery_after !== null
      ? { before: result.mastery_before, after: result.mastery_after }
      : null;

  return (
    <div className="card question" data-testid="question-card">
      <div className="q-meta">
        <span className="badge">Question {number} of {total}</span>
        <span className={`badge bloom-${q.bloom_level.toLowerCase()}`}>{q.bloom_level}</span>
        <span className="muted small">{q.section_title}</span>
      </div>
      <h3 className="q-stem"><MathText text={q.question_stem} /></h3>
      <div className="options" role="radiogroup" aria-label="Answer options">
        {(graded ?? q.options).map((o) => {
          const g = graded?.find((x) => x.label === o.label);
          const state = !graded
            ? selected === o.label ? "selected" : ""
            : g?.is_correct ? "correct" : result!.selected_label === o.label ? "wrong" : "dim";
          return (
            <div key={o.label} className={`option-wrap ${state}`}>
              <button
                className={`option ${state}`}
                role="radio"
                aria-checked={selected === o.label}
                disabled={!!graded}
                onClick={() => setSelected(o.label)}
                data-testid={`option-${o.label}`}
              >
                <span className="opt-label">{o.label}</span>
                <span className="opt-text"><MathText text={o.text} /></span>
                {graded && g?.is_correct && <CheckCircle2 size={18} className="opt-icon ok" />}
                {graded && !g?.is_correct && result!.selected_label === o.label && <XCircle size={18} className="opt-icon bad" />}
              </button>
              {g && (
                <div className="explain small">
                  <span className={`trap ${g.trap_type}`}>{TRAP_LABEL[g.trap_type]}</span>
                  <MathText text={g.misconception_explanation} />
                </div>
              )}
            </div>
          );
        })}
      </div>

      {!answered && (
        <div className="q-actions">
          <button className="link-btn" onClick={() => setShowHint((v) => !v)} aria-expanded={showHint}>
            <Lightbulb size={15} /> {showHint ? "Hide hint" : "Show hint"}
          </button>
          <button className="btn primary" disabled={!selected || checking} onClick={() => void check()}>
            {checking ? <Loader2 size={15} className="spin" /> : null} Check answer
          </button>
        </div>
      )}
      {showHint && !answered && <div className="hint" role="note"><Lightbulb size={14} /> <MathText text={q.hint} /></div>}

      {answered && result && (
        <div className={`feedback ${result.is_correct ? "ok" : "bad"}`} role="status">
          <div className="feedback-head">
            {result.is_correct ? <CheckCircle2 size={18} /> : <XCircle size={18} />}
            <strong>{result.is_correct ? "Correct!" : `Not quite — the answer is ${result.correct_label}.`}</strong>
            {masteryDelta ? (
              <span className={`badge ${masteryDelta.after >= masteryDelta.before ? "ok" : "warn"}`} data-testid="mastery-delta">
                Mastery {pct(masteryDelta.before)} → {pct(masteryDelta.after)}
              </span>
            ) : (
              !result.first_try && <span className="badge ghost">Retry · mastery counts first tries only</span>
            )}
          </div>
          <blockquote className="quote">
            <MathText text={`“${result.source_quote}”`} />
            <button
              className="link-btn small"
              onClick={() =>
                openViewer({
                  documentId: result.document_id,
                  pdfName: result.source_pdf,
                  page: result.source_page,
                  bboxes: result.source_bboxes,
                  label: result.section_title,
                })
              }
            >
              <BookMarked size={13} /> {prettyPdfName(result.source_pdf)} · p. {result.source_page}
            </button>
          </blockquote>
          <div className="q-actions">
            {!result.is_correct && (
              <button className="btn ghost" onClick={() => { setRetrying(true); setSelected(null); }}>
                <RotateCcw size={15} /> Try again
              </button>
            )}
            <button className="btn primary" onClick={onNext} disabled={!nextReady}>
              {isLast ? "See results" : "Next question"} <ArrowRight size={15} />
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function Results({
  quiz, firstTry, onRestart, onWeakSpots,
}: { quiz: QuizState; firstTry: Record<number, boolean>; onRestart: () => void; onWeakSpots: () => void }) {
  const answered = quiz.questions.filter((q) => q.question_id in firstTry);
  const correct = answered.filter((q) => firstTry[q.question_id]).length;
  const byLevel = BLOOM.map((b) => {
    const qs = answered.filter((q) => q.bloom_level === b.level);
    return { ...b, n: qs.length, c: qs.filter((q) => firstTry[q.question_id]).length };
  }).filter((b) => b.n > 0);
  const score = answered.length ? correct / answered.length : 0;
  return (
    <div className="card results" data-testid="results">
      <div className="score-ring" style={{ ["--p" as string]: `${score * 360}deg` }}>
        <div><Trophy size={20} /><strong>{correct}/{answered.length}</strong><span className="muted small">first try</span></div>
      </div>
      <h2>{score >= 0.8 ? "Excellent work!" : score >= 0.5 ? "Good progress" : "Keep practising"}</h2>
      <p className="muted">{quiz.topic}</p>
      <div className="level-bars">
        {byLevel.map((b) => (
          <div key={b.level} className="level-row">
            <span>{b.label}</span>
            <div className="bar"><div style={{ width: `${(b.c / b.n) * 100}%` }} /></div>
            <span className="small">{b.c}/{b.n}</span>
          </div>
        ))}
      </div>
      <ul className="result-list">
        {quiz.questions.map((q, i) => (
          <li key={q.question_id}>
            {firstTry[q.question_id] ? <CheckCircle2 size={15} className="ok" /> : q.question_id in firstTry ? <XCircle size={15} className="bad" /> : <span className="muted">–</span>}
            <span className="small">{i + 1}. <MathText text={q.question_stem} /></span>
          </li>
        ))}
      </ul>
      <div className="q-actions">
        <button className="btn ghost" onClick={onRestart}><RotateCcw size={15} /> New quiz</button>
        <button className="btn primary" onClick={onWeakSpots}><Target size={15} /> Practise weak spots</button>
      </div>
    </div>
  );
}

export function WeakSpots({ onPractice }: { onPractice: (section: string) => void }) {
  const { course, masteryVersion, readyDocs } = useStore();
  const [data, setData] = useState<MasterySummary | null>(null);
  const courseId = course?.id;
  useEffect(() => {
    if (!courseId) return;
    let live = true; // ignore responses that arrive after a newer request
    api.mastery(courseId).then((d) => live && setData(d)).catch(() => live && setData(null));
    return () => {
      live = false;
    };
  }, [courseId, masteryVersion, readyDocs.length]);
  if (!course) return null;
  const sections = data?.sections ?? [];
  return (
    <aside className="card weak" aria-label="Weak spots">
      <h3><Target size={17} /> Mastery</h3>
      {data && (
        <div className="overall">
          <div className="bar big"><div style={{ width: pct(data.overall) }} /></div>
          <span className="small muted">
            Overall {pct(data.overall)} · {data.attempted_sections}/{data.total_sections} sections practised
          </span>
        </div>
      )}
      {sections.length === 0 && <p className="muted small">Sections appear here once your notes are processed.</p>}
      <ul className="mastery-list">
        {sections.slice(0, 12).map((s) => (
          <li key={s.parent_id} data-testid="mastery-row">
            <div className="m-head">
              <span className="m-title" title={`${s.section_title} — ${prettyPdfName(s.pdf_name)}`}>{s.section_title}</span>
              <span className={`m-score ${s.score < 0.4 ? "bad" : s.score >= 0.7 ? "ok" : ""}`}>{pct(s.score)}</span>
            </div>
            <div className={`bar ${s.score < 0.4 ? "bad" : s.score >= 0.7 ? "ok" : ""}`}><div style={{ width: pct(s.score) }} /></div>
            <div className="m-foot muted small">
              <span>{prettyPdfName(s.pdf_name)} · p. {s.page_start}</span>
              <span>{s.attempts ? `${s.correct}/${s.attempts} correct` : "not practised"}</span>
              <button className="link-btn small" onClick={() => onPractice(s.section_title)}>Practise</button>
            </div>
          </li>
        ))}
      </ul>
    </aside>
  );
}
