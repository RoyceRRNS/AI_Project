import { useEffect, useState } from "react";
import { AlertTriangle, BookMarked, Loader2, MessageSquareQuote, Repeat2, RotateCw, Ruler, Shapes, Target } from "lucide-react";
import { prettyPdfName } from "../../citations";
import { papersApi, QUESTION_TYPES, type Blueprint as BlueprintData, type PaperStructure, type PastQuestion, type SectionRow } from "../../papersApi";
import { useStore } from "../../store";
import { MathText } from "../Markdown";
import { BarList, ColumnChart, marks, pct, StatTile, YearChips } from "./charts";

const typeLabel = (key: string | null) => QUESTION_TYPES.find((t) => t.key === key)?.label ?? "Question";

export function PastQuestionLine({ q }: { q: PastQuestion }) {
  return (
    <li className="past-question">
      <span className="past-text">
        {q.context && <span className="muted"><MathText text={q.context} /> </span>}
        <MathText text={q.text} />
      </span>
      <span className="past-meta muted small">
        {q.paper_title} · Q{q.label}{q.marks ? ` · ${marks(q.marks)} marks` : ""}
      </span>
    </li>
  );
}

function Structure({ typical, structures }: { typical: PaperStructure | null; structures: PaperStructure[] }) {
  if (!typical) return null;
  return (
    <section className="card" aria-label="Paper structure">
      <h3><Ruler size={17} /> How the paper is set</h3>
      <p className="muted small">
        Most common pattern, seen in {typical.paper_count} of {structures.length} paper{structures.length === 1 ? "" : "s"}:
        {" "}{typical.total_marks} marks{typical.duration ? ` in ${typical.duration}` : ""}.
      </p>
      <ol className="part-strip" aria-label="Typical parts" data-testid="typical-structure">
        {typical.parts.map((p) => (
          <li key={p.name || "all"}>
            <strong>{p.name ? `Part ${p.name}` : "Questions"}</strong>
            <span className="part-pattern">{p.pattern}</span>
            <span className="muted small">
              {marks(p.total)} marks{p.either_or ? " · either/or choices" : p.choice ? ` · answer ${p.required} of ${p.questions}` : ""}
            </span>
          </li>
        ))}
      </ol>
      <div className="table-wrap">
        <table className="data-table">
          <thead><tr><th>Paper</th><th>Parts</th><th className="num">Marks</th><th>Time</th></tr></thead>
          <tbody>
            {structures.map((s) => (
              <tr key={s.paper_id}>
                <td>{s.title}</td>
                <td>{s.parts.map((p) => `${p.name ? `${p.name}: ` : ""}${p.pattern}${p.either_or ? " (or)" : ""}`).join(", ")}</td>
                <td className="num">{marks(s.total_marks)}</td>
                <td className="nowrap">{s.duration ?? "–"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function SectionTable({ sections, paperCount }: { sections: SectionRow[]; paperCount: number }) {
  const { openViewer } = useStore();
  const top = Math.max(...sections.map((s) => s.marks_per_paper), 1);
  return (
    <div className="table-wrap">
      <table className="data-table" aria-label="Marks by section">
        <thead>
          <tr><th>Section</th><th>Marks a paper</th><th>Asked in</th><th>Most often</th></tr>
        </thead>
        <tbody>
          {sections.map((s) => {
            const kind = Object.keys(s.types)[0] ?? null;
            return (
              <tr key={s.parent_id ?? s.section_title} data-testid="section-row">
                <td>
                  {s.document_id && s.parent_id ? (
                    <button
                      className="link-btn"
                      onClick={() => openViewer({
                        documentId: s.document_id!, pdfName: s.notes_pdf ?? "", page: s.page_start, bboxes: [], label: s.section_title,
                      })}
                    >
                      <BookMarked size={13} /> {s.section_title}
                    </button>
                  ) : s.section_title}
                  {!s.section_title.includes(s.module) && <div className="muted small">{s.module}</div>}
                </td>
                <td className="meter-cell">
                  <span className="meter"><span style={{ width: `${(s.marks_per_paper / top) * 100}%` }} /></span>
                  <span className="num">{marks(s.marks_per_paper)}</span>
                </td>
                <td>
                  <span className="nowrap">{s.papers} of {paperCount}</span>
                  <YearChips years={s.years} />
                </td>
                <td className="nowrap">{typeLabel(kind)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export function Blueprint({ version, onRemap }: { version: string; onRemap: () => void }) {
  const { course, notify } = useStore();
  const [data, setData] = useState<BlueprintData | null>(null);
  const courseId = course?.id;

  useEffect(() => {
    if (!courseId) return;
    let live = true;
    papersApi.blueprint(courseId)
      .then((d) => live && setData(d))
      .catch((e) => live && notify((e as Error).message, "error"));
    return () => {
      live = false;
    };
  }, [courseId, version, notify]);

  if (!data) return <div className="card generating"><Loader2 size={20} className="spin" /> Working out the blueprint…</div>;
  const { summary } = data;
  const moduleBars = data.modules.map((m) => ({
    key: m.module, label: m.module, value: m.marks_per_paper, display: `${marks(m.marks_per_paper)} (${pct(m.share)})`,
    title: `${m.module}: ${marks(m.marks_per_paper)} marks a paper, ${m.questions} questions across ${m.papers} papers`,
  }));
  const typeBars = data.types.filter((t) => t.count > 0).sort((a, b) => b.marks - a.marks).map((t) => ({
    key: t.key, label: t.label ?? t.key, value: t.share, display: pct(t.share), title: `${t.label}: ${t.count} questions, ${marks(t.marks)} marks`,
  }));
  const bloomBars = data.bloom.filter((b) => b.count > 0).map((b) => ({
    key: b.key, label: b.key, value: b.share, display: pct(b.share), title: `${b.key}: ${b.count} questions`,
  }));

  return (
    <div className="blueprint">
      <div className="stat-row">
        <StatTile label="Papers analysed" value={String(summary.papers)} hint={summary.years.length ? `${summary.years[0]} to ${summary.years[summary.years.length - 1]}` : undefined} />
        <StatTile label="Questions" value={String(summary.questions)} />
        <StatTile label="Marks a paper" value={marks(summary.marks_per_paper)} hint="Counting one side of each OR" />
        <StatTile label="Covered by your notes" value={pct(summary.coverage)} />
      </div>

      {data.stale_questions > 0 && (
        <div className="notice paper-notice" role="status">
          <span><AlertTriangle size={14} /> Your notes changed, so {data.stale_questions} question{data.stale_questions === 1 ? "" : "s"} point to sections that no longer exist.</span>
          <button className="btn sm" onClick={onRemap}><RotateCw size={14} /> Match again</button>
        </div>
      )}

      <Structure typical={data.typical} structures={data.structures} />

      <section className="card" aria-label="Topic weightage">
        <h3><Target size={17} /> Where the marks are</h3>
        <p className="muted small">Average marks a paper by lecture.</p>
        <BarList bars={moduleBars} label="Marks by lecture" />
        {data.sections.length > 0 && <SectionTable sections={data.sections} paperCount={summary.papers} />}
      </section>

      <div className="blueprint-grid">
        <section className="card" aria-label="Question types">
          <h3><Shapes size={17} /> How questions are asked</h3>
          <p className="muted small">Share of marks by question type.</p>
          <BarList bars={typeBars} label="Marks by question type" />
        </section>
        <section className="card" aria-label="Bloom levels">
          <h3>Thinking required</h3>
          <p className="muted small">Share of marks by Bloom level.</p>
          <BarList bars={bloomBars} label="Marks by Bloom level" />
        </section>
        {data.marks_distribution.length > 0 && (
          <section className="card" aria-label="Marks per question">
            <h3>Marks per question</h3>
            <p className="muted small">How many questions carry each mark value.</p>
            <ColumnChart
              label="Questions by marks"
              columns={data.marks_distribution.map((d) => ({ key: String(d.marks), value: d.count, caption: `${marks(d.marks)}m` }))}
            />
          </section>
        )}
      </div>

      <section className="card" aria-label="Recurring questions">
        <h3><Repeat2 size={17} /> Questions that keep coming back</h3>
        {data.recurring.length === 0 ? (
          <p className="muted small">Nothing repeats yet. Add more years to see what comes back.</p>
        ) : (
          <ul className="recurring-list">
            {data.recurring.map((r) => (
              <li key={r.text} data-testid="recurring">
                <div className="recurring-head">
                  <strong><MathText text={r.text} /></strong>
                  <span className="badge">{r.papers} papers</span>
                </div>
                <div className="muted small">
                  {typeLabel(r.question_type)}{r.marks ? ` · usually ${marks(r.marks)} marks` : ""}{r.section_title ? ` · ${r.section_title}` : ""}
                </div>
                <YearChips years={r.years} />
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="card" aria-label="Typical phrasings">
        <h3><MessageSquareQuote size={17} /> Typical phrasing</h3>
        <p className="muted small">The wording examiners reuse, with real questions from your papers.</p>
        <ul className="phrasing-list">
          {data.phrasings.map((p) => (
            <li key={p.phrasing} data-testid="phrasing">
              <div className="recurring-head">
                <strong>{p.phrasing}</strong>
                <span className="badge ghost">{p.count} time{p.count === 1 ? "" : "s"}</span>
                {p.typical_marks ? <span className="badge ghost">{marks(p.typical_marks)} marks</span> : null}
              </div>
              <ul className="past-list">{p.examples.map((q) => <PastQuestionLine key={q.question_id} q={q} />)}</ul>
            </li>
          ))}
        </ul>
      </section>

      {data.gaps.length > 0 && (
        <section className="card" aria-label="Gaps in your notes">
          <h3><AlertTriangle size={17} /> Asked in papers, missing from your notes</h3>
          <p className="muted small">
            {data.gaps.length} question{data.gaps.length === 1 ? "" : "s"} matched no section. Add notes for these topics, then match again.
          </p>
          <ul className="past-list">{data.gaps.slice(0, 8).map((q) => <PastQuestionLine key={q.question_id} q={q} />)}</ul>
        </section>
      )}
      <p className="muted small">Notes matched: {data.modules.filter((m) => m.notes_pdf).map((m) => prettyPdfName(m.notes_pdf!)).join(", ") || "none yet"}.</p>
    </div>
  );
}
