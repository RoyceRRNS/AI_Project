import { useCallback, useRef, useState, type DragEvent } from "react";
import {
  AlertTriangle, CheckCircle2, Eye, FileText, Loader2, Presentation, RotateCw, Sparkles, Trash2, UploadCloud, X,
} from "lucide-react";
import { api, type DocumentInfo } from "../api";
import { prettyPdfName } from "../citations";
import { useStore } from "../store";
import { Markdown } from "./Markdown";

export const MAX_UPLOAD_MB = 100;
const STAGES: DocumentInfo["status"][] = ["queued", "parsing", "chunking", "indexing", "ready"];
const STAGE_LABEL: Record<DocumentInfo["status"], string> = {
  queued: "Queued",
  parsing: "Parsing layout",
  chunking: "Chunking",
  indexing: "Indexing",
  ready: "Ready",
  failed: "Failed",
};

export function validateFiles(files: File[]): { accepted: File[]; rejected: string[] } {
  const accepted: File[] = [];
  const rejected: string[] = [];
  for (const f of files) {
    if (!f.name.toLowerCase().endsWith(".pdf") && f.type !== "application/pdf") {
      rejected.push(`${f.name} is not a PDF`);
    } else if (f.size === 0) {
      rejected.push(`${f.name} is empty`);
    } else if (f.size > MAX_UPLOAD_MB * 1024 * 1024) {
      rejected.push(`${f.name} is larger than ${MAX_UPLOAD_MB} MB`);
    } else {
      accepted.push(f);
    }
  }
  return { accepted, rejected };
}

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

export function Library() {
  const { course, docs, docsLoaded, refreshDocs, notify, openViewer, refreshCourses } = useStore();
  const [dragging, setDragging] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [preview, setPreview] = useState<{ name: string; markdown: string; engine: string } | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<DocumentInfo | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const upload = useCallback(
    async (files: File[]) => {
      if (!course) return;
      const { accepted, rejected } = validateFiles(files);
      rejected.forEach((r) => notify(r, "error"));
      if (!accepted.length) return;
      setUploading(true);
      try {
        const res = await api.uploadDocuments(course.id, accepted);
        res.errors.forEach((e) => notify(e.detail, "error"));
        if (res.documents.length) {
          notify(`Uploading ${res.documents.length} file${res.documents.length > 1 ? "s" : ""}…`, "info");
        }
      } catch (e) {
        notify((e as Error).message, "error");
      } finally {
        setUploading(false);
        await refreshDocs();
        await refreshCourses();
      }
    },
    [course, notify, refreshDocs, refreshCourses],
  );

  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setDragging(false);
    void upload(Array.from(e.dataTransfer.files));
  };

  const showPreview = async (doc: DocumentInfo) => {
    try {
      const md = await api.documentMarkdown(doc.id);
      setPreview({ name: doc.pdf_name, markdown: md.markdown, engine: md.engine });
    } catch (e) {
      notify((e as Error).message, "error");
    }
  };

  const remove = async (doc: DocumentInfo) => {
    setConfirmDelete(null);
    try {
      await api.deleteDocument(doc.id);
      notify(`Deleted ${doc.pdf_name}`, "success");
    } catch (e) {
      notify((e as Error).message, "error");
    }
    await refreshDocs();
    await refreshCourses();
  };

  const retry = async (doc: DocumentInfo) => {
    try {
      await api.reprocessDocument(doc.id);
      await refreshDocs();
    } catch (e) {
      notify((e as Error).message, "error");
    }
  };

  if (!course) return null;
  const totalPages = docs.reduce((n, d) => n + (d.num_pages ?? 0), 0);
  const ready = docs.filter((d) => d.status === "ready").length;

  return (
    <div className="library">
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
        aria-label="Upload lecture PDFs"
        data-testid="dropzone"
      >
        <input
          ref={inputRef}
          type="file"
          accept="application/pdf,.pdf"
          multiple
          hidden
          data-testid="file-input"
          onChange={(e) => {
            void upload(Array.from(e.target.files ?? []));
            e.target.value = "";
          }}
        />
        {uploading ? <Loader2 className="spin" size={28} /> : <UploadCloud size={28} />}
        <div>
          <strong>Drop lecture PDFs here</strong> or <span className="link">browse</span>
          <p className="muted small">
            Lecture notes, Beamer slides and scanned handwritten pages · up to {MAX_UPLOAD_MB} MB each
          </p>
        </div>
      </div>

      {docsLoaded && docs.length > 0 && (
        <div className="lib-stats" aria-label="Library summary">
          <span><strong>{docs.length}</strong> documents</span>
          <span><strong>{ready}</strong> ready</span>
          <span><strong>{totalPages}</strong> pages</span>
          <span><strong>{docs.reduce((n, d) => n + d.num_parents, 0)}</strong> sections</span>
        </div>
      )}

      {docsLoaded && docs.length === 0 && (
        <div className="empty-inline">
          <FileText size={20} />
          <span>No documents yet. Upload your lecture notes to start chatting and practising.</span>
        </div>
      )}

      <ul className="doc-list" aria-label="Documents">
        {docs.map((doc) => (
          <li key={doc.id} className={`doc-card status-${doc.status}`} data-testid="doc-card">
            <div className="doc-icon">{doc.is_slides ? <Presentation size={20} /> : <FileText size={20} />}</div>
            <div className="doc-main">
              <div className="doc-name" title={doc.pdf_name}>{prettyPdfName(doc.pdf_name)}</div>
              <div className="doc-meta muted small">
                {doc.num_pages ? `${doc.num_pages} pages · ` : ""}
                {formatBytes(doc.size_bytes)}
                {doc.is_slides && <span className="badge">Slides</span>}
                {doc.engine && <span className="badge ghost">{doc.engine}</span>}
                {doc.status === "ready" && <span>· {doc.num_parents} section{doc.num_parents === 1 ? "" : "s"} · {doc.num_children} search chunk{doc.num_children === 1 ? "" : "s"}</span>}
              </div>
              <StatusPipeline doc={doc} />
              {doc.status === "failed" && (
                <div className="doc-error" role="alert">
                  <AlertTriangle size={14} /> {doc.error}
                </div>
              )}
            </div>
            <div className="doc-actions">
              {doc.status === "ready" && (
                <>
                  <button className="btn ghost sm" onClick={() => void showPreview(doc)} aria-label={`Preview ${doc.pdf_name}`}>
                    <Eye size={15} /> Preview
                  </button>
                  <button
                    className="btn ghost sm"
                    onClick={() => openViewer({ documentId: doc.id, pdfName: doc.pdf_name, page: 1, bboxes: [] })}
                    aria-label={`Open ${doc.pdf_name}`}
                  >
                    <FileText size={15} /> Open
                  </button>
                </>
              )}
              {doc.status === "failed" && (
                <button className="btn ghost sm" onClick={() => void retry(doc)} aria-label={`Retry ${doc.pdf_name}`}>
                  <RotateCw size={15} /> Retry
                </button>
              )}
              <button className="icon-btn danger" onClick={() => setConfirmDelete(doc)} aria-label={`Delete ${doc.pdf_name}`}>
                <Trash2 size={16} />
              </button>
            </div>
          </li>
        ))}
      </ul>

      {confirmDelete && (
        <div className="modal-backdrop" role="presentation" onClick={() => setConfirmDelete(null)}>
          <div className="modal" role="dialog" aria-modal="true" aria-label="Confirm delete" onClick={(e) => e.stopPropagation()}>
            <h3>Delete {prettyPdfName(confirmDelete.pdf_name)}?</h3>
            <p className="muted">
              Its sections are removed from search, chat and quizzes, along with their mastery history. This cannot be undone.
            </p>
            <div className="modal-actions">
              <button className="btn ghost" onClick={() => setConfirmDelete(null)}>Cancel</button>
              <button className="btn danger" onClick={() => void remove(confirmDelete)}>Delete</button>
            </div>
          </div>
        </div>
      )}

      {preview && (
        <div className="drawer-backdrop" role="presentation" onClick={() => setPreview(null)}>
          <div className="drawer" role="dialog" aria-modal="true" aria-label="Extracted content" onClick={(e) => e.stopPropagation()}>
            <header className="drawer-head">
              <div>
                <strong>{prettyPdfName(preview.name)}</strong>
                <div className="muted small">Extracted Markdown · {preview.engine}</div>
              </div>
              <button className="icon-btn" aria-label="Close preview" onClick={() => setPreview(null)}>
                <X size={16} />
              </button>
            </header>
            <div className="drawer-body">
              <Markdown text={preview.markdown} />
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function StatusPipeline({ doc }: { doc: DocumentInfo }) {
  if (doc.status === "ready") {
    return (
      <div className="status ok small">
        <CheckCircle2 size={14} /> Ready for chat and quizzes
      </div>
    );
  }
  if (doc.status === "failed") return null;
  const current = STAGES.indexOf(doc.status);
  return (
    <div className="pipeline" aria-label={`Status: ${STAGE_LABEL[doc.status]}`}>
      <div className="pipeline-steps">
        {STAGES.slice(0, 4).map((s, i) => (
          <span key={s} className={`step ${i < current ? "done" : i === current ? "active" : ""}`}>
            {i === current && <Loader2 size={12} className="spin" />} {STAGE_LABEL[s]}
          </span>
        ))}
      </div>
      <div className="progress"><div style={{ width: `${Math.max(6, doc.progress * 100)}%` }} /></div>
    </div>
  );
}

export function EmptyCourses({ onCreate }: { onCreate: () => void }) {
  const { notify, refreshCourses, selectCourse } = useStore();
  const [busy, setBusy] = useState(false);
  const loadSample = async () => {
    setBusy(true);
    try {
      const res = await api.createSampleCourse();
      await refreshCourses();
      selectCourse(res.course.id);
      notify("Sample course created. Processing 4 documents…", "success");
    } catch (e) {
      notify((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="hero">
      <div className="hero-badge"><Sparkles size={14} /> Grounded in your own notes</div>
      <h1>Study smarter with your lecture notes</h1>
      <p className="muted">
        Upload lecture PDFs, slides or scanned handwriting. Ask questions and get answers that cite the exact page,
        then practise with adaptive quizzes that target your weak spots.
      </p>
      <div className="hero-actions">
        <button className="btn primary lg" onClick={onCreate}>Create a course</button>
        <button className="btn ghost lg" onClick={() => void loadSample()} disabled={busy}>
          {busy ? <Loader2 size={16} className="spin" /> : <Sparkles size={16} />} Try the sample course
        </button>
      </div>
      <ol className="hero-steps">
        <li><strong>1. Upload</strong><span>Layout-aware parsing keeps equations, tables and slides intact.</span></li>
        <li><strong>2. Ask</strong><span>Hybrid search + reranking, with every claim cited to a page.</span></li>
        <li><strong>3. Practise</strong><span>Bloom-calibrated quizzes that adapt to your mastery.</span></li>
      </ol>
    </div>
  );
}
