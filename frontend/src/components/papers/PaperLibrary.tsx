import { useCallback, useRef, useState, type DragEvent } from "react";
import {
  AlertTriangle, CheckCircle2, Eye, FileText, Loader2, Pencil, RotateCw, Sparkles, Trash2, UploadCloud, X,
} from "lucide-react";
import { prettyPdfName } from "../../citations";
import { papersApi, type PaperDetail, type PaperInfo, type PaperStatus } from "../../papersApi";
import { useStore } from "../../store";
import { MAX_UPLOAD_MB, validateFiles } from "../Library";
import { MathText } from "../Markdown";

const STAGE_LABEL: Record<PaperStatus, string> = {
  queued: "Queued",
  parsing: "Reading the paper",
  extracting: "Finding the questions",
  classifying: "Classifying questions",
  mapping: "Matching to your notes",
  ready: "Ready",
  failed: "Failed",
};

export function PaperLibrary({ papers, refresh }: { papers: PaperInfo[]; refresh: () => Promise<void> }) {
  const { course, notify } = useStore();
  const [dragging, setDragging] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [detail, setDetail] = useState<PaperDetail | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<PaperInfo | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const upload = useCallback(
    async (files: File[]) => {
      if (!course) return;
      const { accepted, rejected } = validateFiles(files);
      rejected.forEach((r) => notify(r, "error"));
      if (!accepted.length) return;
      setUploading(true);
      try {
        const res = await papersApi.upload(course.id, accepted);
        res.errors.forEach((e) => notify(e.detail, "error"));
        if (res.papers.length) notify(`Reading ${res.papers.length} paper${res.papers.length > 1 ? "s" : ""}…`, "info");
      } catch (e) {
        notify((e as Error).message, "error");
      } finally {
        setUploading(false);
        await refresh();
      }
    },
    [course, notify, refresh],
  );

  const addSamples = async () => {
    if (!course) return;
    setUploading(true);
    try {
      const res = await papersApi.addSamples(course.id);
      notify(res.papers.length ? `Reading ${res.papers.length} sample papers…` : "The sample papers are already here", "info");
    } catch (e) {
      notify((e as Error).message, "error");
    } finally {
      setUploading(false);
      await refresh();
    }
  };

  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setDragging(false);
    void upload(Array.from(e.dataTransfer.files));
  };

  const act = async (work: () => Promise<unknown>, done?: string) => {
    try {
      await work();
      if (done) notify(done, "success");
    } catch (e) {
      notify((e as Error).message, "error");
    }
    await refresh();
  };

  const show = async (paper: PaperInfo) => {
    try {
      setDetail(await papersApi.get(paper.id));
    } catch (e) {
      notify((e as Error).message, "error");
    }
  };

  return (
    <div className="paper-library">
      <div
        className={`dropzone ${dragging ? "dragging" : ""}`}
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        onClick={() => inputRef.current?.click()}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && inputRef.current?.click()}
        aria-label="Upload past papers"
        data-testid="paper-dropzone"
      >
        <input
          ref={inputRef}
          type="file"
          accept="application/pdf,.pdf"
          multiple
          hidden
          data-testid="paper-input"
          onChange={(e) => {
            void upload(Array.from(e.target.files ?? []));
            e.target.value = "";
          }}
        />
        {uploading ? <Loader2 className="spin" size={28} /> : <UploadCloud size={28} />}
        <div>
          <strong>Drop past question papers here</strong> or <span className="link">browse</span>
          <p className="muted small">Digital or scanned PDFs, up to {MAX_UPLOAD_MB} MB each. More years give a clearer pattern.</p>
        </div>
      </div>

      {papers.length === 0 && (
        <div className="empty-inline">
          <FileText size={20} />
          <span>No papers yet.</span>
          <button className="btn sm" onClick={() => void addSamples()} disabled={uploading}>
            <Sparkles size={14} /> Load sample papers
          </button>
        </div>
      )}

      <ul className="doc-list" aria-label="Past papers">
        {papers.map((paper) => (
          <li key={paper.id} className={`doc-card status-${paper.status}`} data-testid="paper-card">
            <div className="doc-icon"><FileText size={20} /></div>
            <div className="doc-main">
              {editing === paper.id ? (
                <EditPaper
                  paper={paper}
                  onCancel={() => setEditing(null)}
                  onSave={(fields) => {
                    setEditing(null);
                    void act(() => papersApi.update(paper.id, fields), "Saved");
                  }}
                />
              ) : (
                <>
                  <div className="doc-name" title={paper.pdf_name}>{paper.title || prettyPdfName(paper.pdf_name)}</div>
                  <div className="doc-meta muted small">
                    <span>{prettyPdfName(paper.pdf_name)}</span>
                    {paper.year && <span className="badge ghost">{paper.year}</span>}
                    {paper.max_marks && <span className="badge ghost">{paper.max_marks} marks</span>}
                    {paper.duration && <span className="badge ghost">{paper.duration}</span>}
                    {paper.status === "ready" && <span className="badge ok">{paper.question_count} questions</span>}
                  </div>
                </>
              )}
              {paper.status === "ready" ? (
                <div className="status small"><CheckCircle2 size={14} /> Ready for the blueprint and mock papers</div>
              ) : paper.status === "failed" ? (
                <div className="doc-error"><AlertTriangle size={14} /> {paper.error}</div>
              ) : (
                <div className="pipeline">
                  <span className="muted small"><Loader2 size={13} className="spin" /> {STAGE_LABEL[paper.status]}</span>
                  <div className="progress"><div style={{ width: `${Math.round(paper.progress * 100)}%` }} /></div>
                </div>
              )}
              {paper.warnings.map((w) => <p key={w} className="warn small">{w}</p>)}
            </div>
            <div className="doc-actions">
              {paper.status === "ready" && (
                <>
                  <button className="icon-btn" title="See the questions" aria-label={`See questions in ${paper.pdf_name}`} onClick={() => void show(paper)}>
                    <Eye size={16} />
                  </button>
                  <button className="icon-btn" title="Correct the title or year" aria-label={`Edit ${paper.pdf_name}`} onClick={() => setEditing(paper.id)}>
                    <Pencil size={16} />
                  </button>
                </>
              )}
              {(paper.status === "ready" || paper.status === "failed") && (
                <button className="icon-btn" title="Read this paper again" aria-label={`Reprocess ${paper.pdf_name}`} onClick={() => void act(() => papersApi.reprocess(paper.id))}>
                  <RotateCw size={16} />
                </button>
              )}
              <button className="icon-btn danger" title="Delete" aria-label={`Delete ${paper.pdf_name}`} onClick={() => setConfirmDelete(paper)}>
                <Trash2 size={16} />
              </button>
            </div>
          </li>
        ))}
      </ul>

      {detail && <QuestionsDrawer paper={detail} onClose={() => setDetail(null)} />}

      {confirmDelete && (
        <div className="modal-backdrop" role="presentation" onClick={() => setConfirmDelete(null)}>
          <div className="modal" role="alertdialog" aria-modal="true" aria-label="Delete paper" onClick={(e) => e.stopPropagation()}>
            <h3><Trash2 size={18} /> Delete this paper?</h3>
            <p className="muted">{prettyPdfName(confirmDelete.pdf_name)} and its questions will be removed from the blueprint.</p>
            <div className="modal-actions">
              <button className="btn ghost" onClick={() => setConfirmDelete(null)}>Keep</button>
              <button
                className="btn danger"
                onClick={() => {
                  const target = confirmDelete;
                  setConfirmDelete(null);
                  void act(() => papersApi.remove(target.id), `Deleted ${target.pdf_name}`);
                }}
              >
                Delete
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function EditPaper({
  paper, onSave, onCancel,
}: { paper: PaperInfo; onSave: (f: { title: string; year: number | null }) => void; onCancel: () => void }) {
  const [title, setTitle] = useState(paper.title ?? "");
  const [year, setYear] = useState(paper.year ? String(paper.year) : "");
  const valid = title.trim().length > 0 && (year === "" || /^\d{4}$/.test(year));
  return (
    <form
      className="paper-edit"
      onSubmit={(e) => {
        e.preventDefault();
        if (valid) onSave({ title: title.trim(), year: year ? Number(year) : null });
      }}
    >
      <input value={title} onChange={(e) => setTitle(e.target.value)} aria-label="Paper title" maxLength={160} />
      <input value={year} onChange={(e) => setYear(e.target.value)} aria-label="Paper year" placeholder="Year" inputMode="numeric" maxLength={4} />
      <button className="btn sm primary" type="submit" disabled={!valid}>Save</button>
      <button className="btn sm ghost" type="button" onClick={onCancel}>Cancel</button>
    </form>
  );
}

function QuestionsDrawer({ paper, onClose }: { paper: PaperDetail; onClose: () => void }) {
  return (
    <div className="drawer-backdrop" role="presentation" onClick={onClose}>
      <div className="drawer" role="dialog" aria-modal="true" aria-label="Extracted questions" onClick={(e) => e.stopPropagation()}>
        <div className="drawer-head">
          <div>
            <strong>{paper.title}</strong>
            <div className="muted small">
              <a className="link" href={papersApi.fileUrl(paper.id)} target="_blank" rel="noreferrer">Open the PDF</a> to check them against the paper
            </div>
          </div>
          <button className="icon-btn" aria-label="Close questions" onClick={onClose}><X size={18} /></button>
        </div>
        <div className="drawer-body">
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr><th>Q</th><th>Question</th><th className="num">Marks</th><th>Type</th><th>Bloom</th><th>Topic</th></tr>
              </thead>
              <tbody>
                {paper.questions.map((q) => (
                  <tr key={q.id} data-testid="question-row">
                    <td className="nowrap">{q.part && `${q.part}·`}{q.label}</td>
                    <td>
                      {q.context && <span className="muted small"><MathText text={q.context} /> </span>}
                      <MathText text={q.text} />
                    </td>
                    <td className="num" title={q.marks_estimated ? "Estimated from the total for this question" : undefined}>
                      {q.marks ?? "–"}{q.marks_estimated ? "*" : ""}
                    </td>
                    <td className="nowrap">{q.question_type?.replace("_", " ")}</td>
                    <td>{q.bloom_level}</td>
                    <td>{q.section_title ?? <span className="warn small">Not in your notes</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  );
}
