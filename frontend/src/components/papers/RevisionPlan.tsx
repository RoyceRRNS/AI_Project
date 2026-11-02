import { useEffect, useState } from "react";
import { BookMarked, CalendarDays, FlaskConical, Loader2 } from "lucide-react";
import { prettyPdfName } from "../../citations";
import { papersApi, type PlanItem, type RevisionPlan as PlanData } from "../../papersApi";
import { useStore } from "../../store";
import { PastQuestionLine } from "./Blueprint";
import { BarList, marks, pct } from "./charts";

const TIERS: PlanItem["tier"][] = ["must", "should", "optional"];

function PlanCard({ item, onPractise }: { item: PlanItem; onPractise: (section: string) => void }) {
  const { openViewer } = useStore();
  const [open, setOpen] = useState(false);
  return (
    <li className="plan-item" data-testid="plan-item">
      <div className="plan-rank">{item.rank}</div>
      <div className="plan-main">
        <div className="recurring-head">
          <strong>{item.section_title}</strong>
          <span className="badge">{item.focus_label}</span>
        </div>
        {!item.section_title.includes(item.module) && <div className="muted small">{item.module}</div>}
        <p className="plan-advice">{item.advice}</p>
        <dl className="plan-facts">
          <div><dt>Expected</dt><dd>{marks(item.expected_marks)} marks</dd></div>
          <div><dt>Priority</dt><dd>{Math.round(item.priority)}</dd></div>
          <div><dt>Mastery</dt><dd>{item.mastery_attempts ? pct(item.mastery) : "Not practised"}</dd></div>
        </dl>
        {item.practice.length > 1 && (
          <div className="muted small">
            {item.practice.map((p) => `${p.label} ${pct(p.share)}`).join(" · ")}
          </div>
        )}
        <div className="plan-actions">
          {item.document_id && (
            <button
              className="link-btn small"
              onClick={() => openViewer({
                documentId: item.document_id!, pdfName: item.notes_pdf ?? "", page: item.page_start, bboxes: [], label: item.section_title,
              })}
            >
              <BookMarked size={13} /> {prettyPdfName(item.notes_pdf ?? "")} · p. {item.page_start}
            </button>
          )}
          <button className="link-btn small" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
            {open ? "Hide past questions" : `Past questions (${item.examples.length})`}
          </button>
          <button className="link-btn small" onClick={() => onPractise(item.section_title)}>
            <FlaskConical size={13} /> Practise this
          </button>
        </div>
        {open && <ul className="past-list">{item.examples.map((q) => <PastQuestionLine key={q.question_id} q={q} />)}</ul>}
      </div>
    </li>
  );
}

export function RevisionPlan({ version, onPractise }: { version: string; onPractise: (section: string) => void }) {
  const { course, notify, masteryVersion } = useStore();
  const [days, setDays] = useState("");
  const [plan, setPlan] = useState<PlanData | null>(null);
  const courseId = course?.id;
  const parsed = /^\d{1,2}$/.test(days) && Number(days) >= 1 && Number(days) <= 60 ? Number(days) : null;

  useEffect(() => {
    if (!courseId) return;
    let live = true;
    papersApi.plan(courseId, parsed)
      .then((p) => live && setPlan(p))
      .catch((e) => live && notify((e as Error).message, "error"));
    return () => {
      live = false;
    };
  }, [courseId, version, parsed, masteryVersion, notify]);

  if (!plan) return <div className="card generating"><Loader2 size={20} className="spin" /> Building your plan…</div>;
  const bars = plan.practice_mix.map((m) => ({ key: m.mode, label: m.label, value: m.share, display: pct(m.share) }));

  return (
    <div className="plan">
      <div className="plan-top">
        <section className="card" aria-label="Practice mix">
          <h3>Split your practice</h3>
          <p className="muted small">Based on where the marks are in your past papers.</p>
          <BarList bars={bars} label="Marks by kind of practice" />
        </section>
        <section className="card" aria-label="Exam date">
          <h3><CalendarDays size={17} /> Days until the exam</h3>
          <label className="field">
            <span className="muted small">Add this to spread the plan across days</span>
            <input
              value={days}
              onChange={(e) => setDays(e.target.value.replace(/\D/g, "").slice(0, 2))}
              inputMode="numeric"
              placeholder="e.g. 7"
              aria-label="Days until the exam"
            />
          </label>
          {days !== "" && parsed === null && <p className="error-line" role="alert">Enter a number from 1 to 60.</p>}
          {plan.schedule.length > 0 && (
            <ol className="schedule" aria-label="Daily schedule">
              {plan.schedule.map((d) => (
                <li key={d.day}>
                  <strong>{d.title}</strong>
                  <span className="muted small">{d.sections.length ? d.sections.join(", ") : d.focus.join(", ")}</span>
                </li>
              ))}
            </ol>
          )}
        </section>
      </div>

      {plan.items.length === 0 && (
        <p className="notice">None of the questions matched your notes yet. Upload notes in the Library, then match again from the Blueprint.</p>
      )}

      {TIERS.map((tier) => {
        const items = plan.items.filter((i) => i.tier === tier);
        if (!items.length) return null;
        return (
          <section key={tier} className="card" aria-label={items[0].tier_label}>
            <h3>{items[0].tier_label}</h3>
            <ol className="plan-list">
              {items.map((item) => <PlanCard key={item.rank} item={item} onPractise={onPractise} />)}
            </ol>
          </section>
        );
      })}

      {plan.gaps.length > 0 && (
        <p className="notice" role="status">
          {plan.gaps.length} past question{plan.gaps.length === 1 ? " is" : "s are"} on topics missing from your notes, so they are not in this plan.
        </p>
      )}
    </div>
  );
}
