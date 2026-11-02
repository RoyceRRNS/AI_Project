import { Fragment, useCallback, useEffect, useRef, useState } from "react";
import { CheckCircle2, ChevronDown, FileQuestion, Loader2, Printer, RotateCcw, Trash2, XCircle, Zap } from "lucide-react";
import type { InlineCitation } from "../../citations";
import {
  papersApi, QUESTION_TYPES, type MockQuestion, type MockSpec, type MockSummary, type PaperStructure, type QuestionType,
} from "../../papersApi";
import { useStore } from "../../store";
import { Markdown, MathText } from "../Markdown";
import { marks } from "./charts";

interface Sheet {
  id: string | null;
  title: string;
  spec: MockSpec | null;
  total: number;
  questions: MockQuestion[];
  generating: boolean;
  progress: { index: number; label: string; section: string; type: string } | null;
  warnings: string[];
  error: string | null;
}

const EMPTY: Sheet = {
  id: null, title: "", spec: null, total: 0, questions: [], generating: false, progress: null, warnings: [], error: null,
};

function Solution({ q }: { q: MockQuestion }) {
  const { openViewer, notify } = useStore();
  const onCite = useCallback(
    (c: InlineCitation) => {
      const v = c.verified;
      const documentId = v?.document_id ?? q.document_id;
      if (!documentId) {
        notify("This citation could not be matched to a document", "error");
        return;
      }
      openViewer({
        documentId, pdfName: v?.resolved_pdf ?? c.pdfName, page: c.targetPage, bboxes: v?.bboxes ?? [],
        label: `Cited page ${c.page}`,
      });
    },
    [openViewer, notify, q.document_id],
  );
  return (
    <div className="solution" data-testid="solution">
      <h4>Worked solution</h4>
      <Markdown text={q.solution} report={q.report} onCite={onCite} />
      <p className="final-answer"><strong>Answer:</strong> <MathText text={q.final_answer} /></p>
      {q.answer_check && (
        <p className="status small" data-testid="answer-check">
          <CheckCircle2 size={14} /> Arithmetic rechecked: {q.answer_check.expression} = {Number(q.answer_check.value.toFixed(4))}
        </p>
      )}
      {q.marking.length > 0 && (
        <div>
          <h4>How the marks are awarded</h4>
          <ul className="marking">
            {q.marking.map((m) => (
              <li key={m.point}><MathText text={m.point} /> <span className="num">{marks(m.marks)}</span></li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function QuestionRow({ q, reveal }: { q: MockQuestion; reveal: boolean }) {
  const [open, setOpen] = useState(false);
  const shown = open || reveal;
  return (
    <li className="mock-question" data-testid="mock-question">
      <div className="mock-line">
        <span className="mock-label">{q.label}</span>
        <div className="mock-text">
          <MathText text={q.question} />
          <div className="mock-tags muted small">
            <span>{q.type_label}</span> · <span>{q.bloom_level}</span> · <span>{q.section_title}</span>
          </div>
        </div>
        <span className="mock-marks">[{marks(q.marks)}]</span>
      </div>
      <button className="link-btn small no-print" onClick={() => setOpen((v) => !v)} aria-expanded={shown}>
        <ChevronDown size={13} className={shown ? "rot" : ""} /> {shown ? "Hide worked solution" : "Show worked solution"}
      </button>
      {shown && <Solution q={q} />}
    </li>
  );
}

function MockSheet({ sheet, onBack }: { sheet: Sheet; onBack: () => void }) {
  const [reveal, setReveal] = useState(false);
  const { questions, spec } = sheet;
  const partNames = [...new Set(questions.map((q) => q.part))];
  const partInfo = (name: string) => spec?.parts?.find((p) => p.name === name);
  return (
    <div className="card mock-sheet" data-testid="mock-sheet">
      <div className="sheet-head">
        <div>
          <h2><FileQuestion size={20} /> {sheet.title}</h2>
          <p className="muted small">
            {spec?.max_marks ? `${marks(spec.max_marks)} marks` : ""}
            {spec?.duration ? ` · ${spec.duration}` : ""}
            {sheet.questions.length ? ` · ${sheet.questions.length} questions` : ""}
          </p>
        </div>
        <div className="sheet-actions no-print">
          <button className="btn sm ghost" onClick={() => setReveal((v) => !v)} aria-pressed={reveal}>
            {reveal ? "Hide all solutions" : "Show all solutions"}
          </button>
          <button className="btn sm ghost" onClick={() => window.print()}><Printer size={14} /> Print</button>
          <button className="btn sm ghost" onClick={onBack}><RotateCcw size={14} /> Back</button>
        </div>
      </div>
      {sheet.generating && (
        <div className="generating no-print" data-testid="generating">
          <Loader2 size={20} className="spin" />
          <div>
            <strong>Writing question {sheet.progress?.index ?? 1} of {sheet.total || "…"}</strong>
            {sheet.progress && <div className="muted small">{sheet.progress.type} · {sheet.progress.section}</div>}
            <div className="progress"><div style={{ width: `${((sheet.progress?.index ?? 0.5) / Math.max(1, sheet.total)) * 100}%` }} /></div>
          </div>
        </div>
      )}
      {sheet.error && <p className="error-line" role="alert"><XCircle size={14} /> {sheet.error}</p>}
      {sheet.warnings.map((w) => <p key={w} className="warn small">{w}</p>)}
      {partNames.map((name) => {
        const info = partInfo(name);
        const members = questions.filter((q) => q.part === name);
        return (
          <section key={name || "all"} className="mock-part" aria-label={name ? `Part ${name}` : "Questions"}>
            {name && (
              <h3>
                Part {name}
                {info && <span className="muted small"> {info.pattern} = {marks(info.total)} marks</span>}
              </h3>
            )}
            {info?.instructions && <p className="muted small">{info.instructions}</p>}
            <ol className="mock-list">
              {members.map((q, i) => {
                const prev = members[i - 1];
                const sideChanged = prev && q.choice_group && prev.choice_group === q.choice_group
                  && (prev.sub.split(".")[0] || prev.number) !== (q.sub.split(".")[0] || q.number);
                return (
                  <Fragment key={q.ord}>
                    {sideChanged && <li className="mock-or" aria-label="or">OR</li>}
                    <QuestionRow q={q} reveal={reveal} />
                  </Fragment>
                );
              })}
            </ol>
          </section>
        );
      })}
    </div>
  );
}

export function MockPaperView({
  structures, presetTopic, onPresetUsed,
}: { structures: PaperStructure[]; presetTopic: string | null; onPresetUsed: () => void }) {
  const { course, readyDocs, notify } = useStore();
  const [kind, setKind] = useState<"paper" | "practice">("paper");
  const [template, setTemplate] = useState("");
  const [count, setCount] = useState(6);
  const [topic, setTopic] = useState("");
  const [types, setTypes] = useState<QuestionType[]>([]);
  const [sheet, setSheet] = useState<Sheet | null>(null);
  const [saved, setSaved] = useState<MockSummary[]>([]);
  const abortRef = useRef<AbortController | null>(null);
  const courseId = course?.id;

  useEffect(() => () => abortRef.current?.abort(), []);

  const loadSaved = useCallback(() => {
    if (!courseId) return;
    papersApi.listMocks(courseId).then(setSaved).catch(() => undefined);
  }, [courseId]);
  useEffect(loadSaved, [loadSaved, sheet?.generating]);

  useEffect(() => {
    if (!presetTopic) return;
    setKind("practice");
    setTopic(presetTopic);
    setCount(3);
    onPresetUsed();
  }, [presetTopic, onPresetUsed]);

  const generate = async () => {
    if (!courseId) return;
    const controller = new AbortController();
    abortRef.current = controller;
    setSheet({ ...EMPTY, generating: true });
    try {
      await papersApi.mock(
        kind === "paper"
          ? { course_id: courseId, kind, template_paper_id: template || null }
          : { course_id: courseId, kind, num_questions: count, question_types: types.length ? types : null, topic: topic.trim() || null },
        (ev) => {
          const d = ev.data as Record<string, unknown>;
          setSheet((s) => {
            if (!s) return s;
            if (ev.event === "mock") return { ...s, id: String(d.mock_id), title: String(d.title), total: Number(d.total), spec: d.spec as MockSpec };
            if (ev.event === "progress") {
              return { ...s, progress: { index: Number(d.index), label: String(d.label), section: String(d.section), type: String(d.question_type) } };
            }
            if (ev.event === "question") return { ...s, questions: [...s.questions, d.question as MockQuestion] };
            if (ev.event === "warning") return { ...s, warnings: [...s.warnings, String(d.message)] };
            if (ev.event === "error") return { ...s, error: String(d.message) };
            if (ev.event === "done") return { ...s, generating: false, progress: null };
            return s;
          });
        },
        controller.signal,
      );
    } catch (e) {
      if ((e as Error).name !== "AbortError") {
        notify((e as Error).message, "error");
        setSheet(null);
      }
    } finally {
      setSheet((s) => (s ? { ...s, generating: false, progress: null } : s));
    }
  };

  const open = async (id: string) => {
    try {
      const m = await papersApi.getMock(id);
      setSheet({ ...EMPTY, id: m.id, title: m.title, spec: m.spec, questions: m.questions, total: m.questions.length });
    } catch (e) {
      notify((e as Error).message, "error");
    }
  };

  const remove = async (id: string) => {
    try {
      await papersApi.removeMock(id);
      loadSaved();
    } catch (e) {
      notify((e as Error).message, "error");
    }
  };

  if (sheet) {
    return (
      <MockSheet
        sheet={sheet}
        onBack={() => {
          abortRef.current?.abort();
          setSheet(null);
        }}
      />
    );
  }

  const noNotes = readyDocs.length === 0;
  return (
    <div className="mock-setup">
      <div className="card quiz-setup">
        <h2><Zap size={20} /> Exam practice</h2>
        <p className="muted">
          Written from your notes in the style of your past papers, with worked solutions that cite the page they come from.
        </p>
        <div className="field">
          <span>What to make</span>
          <div className="seg">
            <button className={`seg-btn ${kind === "paper" ? "on" : ""}`} aria-pressed={kind === "paper"} onClick={() => setKind("paper")}>Full mock paper</button>
            <button className={`seg-btn ${kind === "practice" ? "on" : ""}`} aria-pressed={kind === "practice"} onClick={() => setKind("practice")}>Practice set</button>
          </div>
        </div>
        {kind === "paper" ? (
          <label className="field">
            <span>Follow the pattern of</span>
            <select className="paper-select" value={template} onChange={(e) => setTemplate(e.target.value)} aria-label="Template paper">
              <option value="">The most common pattern</option>
              {structures.map((s) => <option key={s.paper_id} value={s.paper_id}>{s.title}</option>)}
            </select>
          </label>
        ) : (
          <>
            <label className="field">
              <span>Topic <em className="muted">(optional)</em></span>
              <input value={topic} onChange={(e) => setTopic(e.target.value)} placeholder="Leave empty for your priority topics" maxLength={300} aria-label="Practice topic" />
            </label>
            <label className="field">
              <span>Questions: <strong>{count}</strong></span>
              <input type="range" min={1} max={10} value={count} onChange={(e) => setCount(Number(e.target.value))} aria-label="Number of practice questions" />
            </label>
            <div className="field">
              <span>Question types <em className="muted">(optional)</em></span>
              <div className="type-toggles">
                {QUESTION_TYPES.map((t) => {
                  const on = types.includes(t.key);
                  return (
                    <button
                      key={t.key}
                      className={`toggle ${on ? "on" : ""}`}
                      aria-pressed={on}
                      onClick={() => setTypes(on ? types.filter((x) => x !== t.key) : [...types, t.key])}
                    >
                      {t.label}
                    </button>
                  );
                })}
              </div>
            </div>
          </>
        )}
        <button className="btn primary lg" disabled={noNotes} onClick={() => void generate()}>
          <Zap size={16} /> Generate
        </button>
        {noNotes && <p className="notice">Upload and process notes in the Library first.</p>}
      </div>

      {saved.length > 0 && (
        <aside className="card" aria-label="Saved papers">
          <h3>Saved</h3>
          <ul className="saved-list">
            {saved.map((m) => (
              <li key={m.id}>
                <button className="link-btn" onClick={() => void open(m.id)}>{m.title}</button>
                <span className="muted small">{m.question_count} questions</span>
                <button className="icon-btn danger" aria-label={`Delete ${m.title}`} onClick={() => void remove(m.id)}><Trash2 size={14} /></button>
              </li>
            ))}
          </ul>
        </aside>
      )}
    </div>
  );
}
