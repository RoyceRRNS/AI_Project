import {
  createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode,
} from "react";
import { api, type BBox, type Course, type DocumentInfo, type Health } from "./api";

export interface Toast { id: number; kind: "info" | "success" | "error"; message: string }

export interface ViewerTarget {
  documentId: string;
  pdfName: string;
  page: number;
  bboxes: BBox[];
  label?: string;
  /** Bumped on every open so re-clicking the same citation re-flashes the highlight. */
  nonce: number;
}

interface Store {
  health: Health | null;
  courses: Course[];
  course: Course | null;
  selectCourse: (id: string | null) => void;
  refreshCourses: () => Promise<Course[]>;
  docs: DocumentInfo[];
  docsLoaded: boolean;
  refreshDocs: () => Promise<void>;
  readyDocs: DocumentInfo[];
  toasts: Toast[];
  notify: (message: string, kind?: Toast["kind"]) => void;
  dismiss: (id: number) => void;
  viewer: ViewerTarget | null;
  openViewer: (t: Omit<ViewerTarget, "nonce">) => void;
  closeViewer: () => void;
  masteryVersion: number;
  bumpMastery: () => void;
}

const Ctx = createContext<Store | null>(null);

export const storage = {
  get(key: string): string | null {
    try {
      return window.localStorage.getItem(key);
    } catch {
      return null;
    }
  },
  set(key: string, value: string | null): void {
    try {
      if (value === null) window.localStorage.removeItem(key);
      else window.localStorage.setItem(key, value);
    } catch {
      /* storage unavailable (private mode) */
    }
  },
};

const PROCESSING = new Set(["queued", "parsing", "chunking", "indexing"]);

export function StoreProvider({ children, pollMs = 1200 }: { children: ReactNode; pollMs?: number }) {
  const [health, setHealth] = useState<Health | null>(null);
  const [courses, setCourses] = useState<Course[]>([]);
  const [courseId, setCourseId] = useState<string | null>(() => storage.get("cnr.course"));
  const [docs, setDocs] = useState<DocumentInfo[]>([]);
  const [docsLoaded, setDocsLoaded] = useState(false);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [viewer, setViewer] = useState<ViewerTarget | null>(null);
  const [masteryVersion, setMasteryVersion] = useState(0);
  const toastId = useRef(0);
  const nonce = useRef(0);
  const lastStatus = useRef(new Map<string, string>());

  const notify = useCallback((message: string, kind: Toast["kind"] = "info") => {
    const id = ++toastId.current;
    setToasts((t) => [...t.slice(-3), { id, kind, message }]);
    window.setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), kind === "error" ? 7000 : 4000);
  }, []);
  const dismiss = useCallback((id: number) => setToasts((t) => t.filter((x) => x.id !== id)), []);

  const refreshCourses = useCallback(async () => {
    const list = await api.listCourses();
    setCourses(list);
    return list;
  }, []);

  useEffect(() => {
    api.health().then(setHealth).catch(() => setHealth(null));
    const timer = window.setInterval(() => api.health().then(setHealth).catch(() => setHealth(null)), 30000);
    refreshCourses()
      .then((list) => {
        setCourseId((current) => (current && list.some((c) => c.id === current) ? current : list[0]?.id ?? null));
      })
      .catch((e) => notify(`Could not reach the server: ${e.message}`, "error"));
    return () => window.clearInterval(timer);
  }, [refreshCourses, notify]);

  const selectCourse = useCallback((id: string | null) => {
    setCourseId(id);
    storage.set("cnr.course", id);
    setDocs([]);
    setDocsLoaded(false);
    setViewer(null);
    lastStatus.current = new Map();
  }, []);

  const refreshDocs = useCallback(async () => {
    if (!courseId) {
      setDocs([]);
      setDocsLoaded(true);
      return;
    }
    try {
      const list = await api.listDocuments(courseId);
      // Announce transitions outside the state updater (StrictMode runs updaters twice).
      const seen = lastStatus.current;
      for (const d of list) {
        const before = seen.get(d.id);
        if (before && PROCESSING.has(before) && d.status === "ready") notify(`${d.pdf_name} is ready`, "success");
        if (before && PROCESSING.has(before) && d.status === "failed") notify(`${d.pdf_name} failed: ${d.error}`, "error");
      }
      lastStatus.current = new Map(list.map((d) => [d.id, d.status]));
      setDocs(list);
      setDocsLoaded(true);
    } catch (e) {
      notify(`Could not load documents: ${(e as Error).message}`, "error");
    }
  }, [courseId, notify]);

  useEffect(() => {
    void refreshDocs();
  }, [refreshDocs]);

  const processing = docs.some((d) => PROCESSING.has(d.status));
  useEffect(() => {
    if (!processing) {
      refreshCourses().catch(() => undefined);
      return;
    }
    const timer = window.setInterval(() => void refreshDocs(), pollMs);
    return () => window.clearInterval(timer);
  }, [processing, refreshDocs, refreshCourses, pollMs]);

  const openViewer = useCallback((t: Omit<ViewerTarget, "nonce">) => {
    nonce.current += 1;
    setViewer({ ...t, nonce: nonce.current });
  }, []);
  const closeViewer = useCallback(() => setViewer(null), []);
  const bumpMastery = useCallback(() => setMasteryVersion((v) => v + 1), []);

  const value = useMemo<Store>(
    () => ({
      health,
      courses,
      course: courses.find((c) => c.id === courseId) ?? null,
      selectCourse,
      refreshCourses,
      docs,
      docsLoaded,
      refreshDocs,
      readyDocs: docs.filter((d) => d.status === "ready"),
      toasts,
      notify,
      dismiss,
      viewer,
      openViewer,
      closeViewer,
      masteryVersion,
      bumpMastery,
    }),
    [health, courses, courseId, selectCourse, refreshCourses, docs, docsLoaded, refreshDocs, toasts, notify,
      dismiss, viewer, openViewer, closeViewer, masteryVersion, bumpMastery],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useStore(): Store {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useStore must be used inside <StoreProvider>");
  return ctx;
}
