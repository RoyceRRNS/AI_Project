import { Component, lazy, Suspense, useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import {
  BookOpen, Brain, ChevronDown, Library as LibraryIcon, Loader2, MessagesSquare, Monitor, Moon, Plus, Sparkles,
  Sun, Trash2,
} from "lucide-react";
import { api } from "./api";
import { Chat } from "./components/Chat";
import { EmptyCourses, Library } from "./components/Library";
import { Quiz } from "./components/Quiz";
import { Toasts } from "./components/Toasts";
import { storage, useStore } from "./store";
import { nextTheme, useTheme, type ThemeChoice } from "./theme";

// pdf.js is large; load the viewer only when a document is first opened.
const PdfViewer = lazy(() => import("./components/PdfViewer").then((m) => ({ default: m.PdfViewer })));

/** A failure inside pdf.js (e.g. its worker cannot load) stays in the viewer pane instead of blanking the app. */
class ViewerBoundary extends Component<{ onClose: () => void; children: ReactNode }, { error: Error | null }> {
  state: { error: Error | null } = { error: null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error) {
    console.error("PDF viewer failed", error);
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <aside className="viewer" aria-label="PDF viewer">
        <div className="viewer-error" role="alert">
          <p>The PDF viewer stopped working: {this.state.error.message}</p>
          <button className="btn sm" onClick={this.props.onClose}>Close viewer</button>
        </div>
      </aside>
    );
  }
}

export type Tab = "library" | "chat" | "quiz";

const TABS: { id: Tab; label: string; icon: typeof BookOpen }[] = [
  { id: "library", label: "Library", icon: LibraryIcon },
  { id: "chat", label: "Chat", icon: MessagesSquare },
  { id: "quiz", label: "Quiz", icon: Brain },
];

export default function App() {
  const { course, courses, viewer, readyDocs, closeViewer } = useStore();
  const [tab, setTab] = useState<Tab>(() => (storage.get("cnr.tab") as Tab) || "library");
  const [creating, setCreating] = useState(false);

  useEffect(() => storage.set("cnr.tab", tab), [tab]);

  const onTabKey = (e: React.KeyboardEvent, i: number) => {
    if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
    const next = (i + (e.key === "ArrowRight" ? 1 : TABS.length - 1)) % TABS.length;
    setTab(TABS[next].id);
    document.getElementById(`tab-${TABS[next].id}`)?.focus();
  };

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <div className="logo"><BookOpen size={17} /></div>
          <span>Course Notes <em>Studio</em></span>
        </div>
        {courses.length > 0 && <CourseSwitcher onCreate={() => setCreating(true)} />}
        <HealthPill />
        <ThemeToggle />
      </header>

      {!course ? (
        <main className="landing">
          <EmptyCourses onCreate={() => setCreating(true)} />
        </main>
      ) : (
        <main className={`shell ${viewer ? "with-viewer" : ""}`}>
          <section className="workspace">
            <nav className="tabs" role="tablist" aria-label="Workspace">
              {TABS.map((t, i) => {
                const Icon = t.icon;
                const count = t.id === "library" ? course.document_count : undefined;
                return (
                  <button
                    key={t.id}
                    id={`tab-${t.id}`}
                    role="tab"
                    aria-selected={tab === t.id}
                    aria-controls={`panel-${t.id}`}
                    tabIndex={tab === t.id ? 0 : -1}
                    className={`tab ${tab === t.id ? "active" : ""}`}
                    onClick={() => setTab(t.id)}
                    onKeyDown={(e) => onTabKey(e, i)}
                  >
                    <Icon size={16} /> {t.label}
                    {count !== undefined && count > 0 && <span className="count">{count}</span>}
                    {t.id !== "library" && readyDocs.length === 0 && <span className="dot-off" title="Needs processed notes" />}
                  </button>
                );
              })}
            </nav>
            <div className="panel" role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`}>
              {tab === "library" && <Library />}
              {tab === "chat" && <Chat key={course.id} />}
              {tab === "quiz" && <Quiz key={course.id} />}
            </div>
          </section>
          {viewer && (
            <ViewerBoundary key={viewer.nonce} onClose={closeViewer}>
              <Suspense fallback={<aside className="viewer"><div className="viewer-loading">Loading viewer…</div></aside>}>
                <PdfViewer />
              </Suspense>
            </ViewerBoundary>
          )}
        </main>
      )}
      {creating && <CreateCourseDialog onClose={() => setCreating(false)} onCreated={() => setTab("library")} />}
      <Toasts />
    </div>
  );
}

function CourseSwitcher({ onCreate }: { onCreate: () => void }) {
  const { courses, course, selectCourse, refreshCourses, notify } = useStore();
  const [open, setOpen] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const close = (e: MouseEvent) => !ref.current?.contains(e.target as Node) && setOpen(false);
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);

  const remove = async () => {
    if (!course) return;
    setConfirm(false);
    setOpen(false);
    try {
      await api.deleteCourse(course.id);
      const list = await refreshCourses();
      selectCourse(list[0]?.id ?? null);
      notify(`Deleted course ${course.name}`, "success");
    } catch (e) {
      notify((e as Error).message, "error");
    }
  };

  return (
    <div className="switcher" ref={ref}>
      <button className="switcher-btn" onClick={() => setOpen((v) => !v)} aria-haspopup="menu" aria-expanded={open} aria-label="Switch course">
        <span className="course-code">{course?.code || "Course"}</span>
        <span className="course-name">{course?.name}</span>
        <ChevronDown size={15} />
      </button>
      {open && (
        <div className="menu" role="menu">
          {courses.map((c) => (
            <button
              key={c.id}
              role="menuitem"
              className={`menu-item ${c.id === course?.id ? "current" : ""}`}
              onClick={() => {
                selectCourse(c.id);
                setOpen(false);
              }}
            >
              <span className="course-code">{c.code || "—"}</span> {c.name}
              <span className="muted small">{c.ready_count}/{c.document_count} docs</span>
            </button>
          ))}
          <div className="menu-sep" />
          <button role="menuitem" className="menu-item" onClick={() => { setOpen(false); onCreate(); }}>
            <Plus size={14} /> New course
          </button>
          {course && (
            confirm ? (
              <div className="menu-item danger-zone">
                Delete “{course.name}” and all its notes?
                <button className="btn danger sm" onClick={() => void remove()}>Delete</button>
                <button className="btn ghost sm" onClick={() => setConfirm(false)}>Keep</button>
              </div>
            ) : (
              <button role="menuitem" className="menu-item danger" onClick={() => setConfirm(true)}>
                <Trash2 size={14} /> Delete this course
              </button>
            )
          )}
        </div>
      )}
    </div>
  );
}

function CreateCourseDialog({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const { refreshCourses, selectCourse, notify } = useStore();
  const [name, setName] = useState("");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!name.trim()) {
      setError("Give the course a name");
      return;
    }
    setBusy(true);
    try {
      const c = await api.createCourse(name.trim(), code.trim());
      await refreshCourses();
      selectCourse(c.id);
      notify(`Created ${c.name}`, "success");
      onCreated();
      onClose();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="modal-backdrop" role="presentation" onClick={onClose}>
      <form className="modal" role="dialog" aria-modal="true" aria-label="Create course" onSubmit={submit} onClick={(e) => e.stopPropagation()}>
        <h3><Sparkles size={18} /> New course</h3>
        <label className="field">
          <span>Course name</span>
          <input autoFocus value={name} onChange={(e) => { setName(e.target.value); setError(null); }} placeholder="Machine Learning" maxLength={120} aria-label="Course name" />
        </label>
        <label className="field">
          <span>Course code <em className="muted">(optional)</em></span>
          <input value={code} onChange={(e) => setCode(e.target.value)} placeholder="CS229" maxLength={40} aria-label="Course code" />
        </label>
        {error && <p className="error-line" role="alert">{error}</p>}
        <div className="modal-actions">
          <button type="button" className="btn ghost" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn primary" disabled={busy}>
            {busy && <Loader2 size={15} className="spin" />} Create course
          </button>
        </div>
      </form>
    </div>
  );
}

const THEME_NAME: Record<ThemeChoice, string> = { system: "System", light: "Light", dark: "Dark" };
const THEME_ICON: Record<ThemeChoice, typeof Sun> = { system: Monitor, light: Sun, dark: Moon };

function ThemeToggle() {
  const [theme, setTheme] = useTheme();
  const next = nextTheme(theme);
  const Icon = THEME_ICON[theme];
  const label = `Theme: ${THEME_NAME[theme]}. Switch to ${THEME_NAME[next]}`;
  return (
    <button type="button" className="icon-btn theme-toggle" onClick={() => setTheme(next)} aria-label={label} title={label}>
      <Icon size={16} />
    </button>
  );
}

function HealthPill() {
  const { health } = useStore();
  if (!health) return <span className="health bad" title="Backend unreachable"><span className="led" /> Offline</span>;
  const ok = health.llm.ok;
  const model = health.models.llm.replace(/:latest$/, "");
  return (
    <span className={`health ${ok ? "ok" : "bad"}`} title={`${health.llm.detail} · ${health.models.embedding} · ${health.models.reranker}`}>
      <span className="led" /> {model} {ok ? "ready" : "unavailable"}
    </span>
  );
}
