import { useCallback, useEffect, useMemo, useState } from "react";
import { FileStack, Target } from "lucide-react";
import { papersApi, type Blueprint as BlueprintData, type PaperInfo } from "../papersApi";
import "../papers.css";
import { useStore } from "../store";
import { Blueprint } from "./papers/Blueprint";
import { MockPaperView } from "./papers/MockPaper";
import { PaperLibrary } from "./papers/PaperLibrary";
import { RevisionPlan } from "./papers/RevisionPlan";

type View = "blueprint" | "plan" | "mock" | "papers";

const VIEWS: { id: View; label: string }[] = [
  { id: "blueprint", label: "Blueprint" },
  { id: "plan", label: "Revision plan" },
  { id: "mock", label: "Mock paper" },
  { id: "papers", label: "Papers" },
];

const FINISHED = new Set(["ready", "failed"]);

export function Papers({ pollMs = 1200 }: { pollMs?: number }) {
  const { course, readyDocs, notify } = useStore();
  const [papers, setPapers] = useState<PaperInfo[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [view, setView] = useState<View | null>(null);
  const [preset, setPreset] = useState<string | null>(null);
  const [structures, setStructures] = useState<BlueprintData["structures"]>([]);
  const courseId = course?.id;

  const refresh = useCallback(async () => {
    if (!courseId) return;
    try {
      const list = await papersApi.list(courseId);
      setPapers(list);
      setLoaded(true);
      // Chosen once: the view must not change under the student when a paper finishes.
      setView((v) => v ?? (list.some((p) => p.status === "ready") ? "blueprint" : "papers"));
    } catch (e) {
      notify(`Could not load papers: ${(e as Error).message}`, "error");
    }
  }, [courseId, notify]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const processing = papers.some((p) => !FINISHED.has(p.status));
  useEffect(() => {
    if (!processing) return;
    const timer = window.setInterval(() => void refresh(), pollMs);
    return () => window.clearInterval(timer);
  }, [processing, refresh, pollMs]);

  const ready = useMemo(() => papers.filter((p) => p.status === "ready"), [papers]);
  // Anything that can change the analysis: papers finishing or edited, notes added or removed.
  const version = useMemo(
    () => `${ready.map((p) => `${p.id}:${p.updated_at}`).join(",")}|${readyDocs.length}`,
    [ready, readyDocs.length],
  );
  useEffect(() => {
    if (!courseId || !ready.length) return;
    let live = true;
    papersApi.blueprint(courseId).then((b) => live && setStructures(b.structures)).catch(() => undefined);
    return () => {
      live = false;
    };
  }, [courseId, version, ready.length]);

  const remap = useCallback(async () => {
    if (!courseId) return;
    try {
      await papersApi.remap(courseId);
      notify("Matching your papers to the notes again…", "info");
      await refresh();
    } catch (e) {
      notify((e as Error).message, "error");
    }
  }, [courseId, notify, refresh]);

  const practise = useCallback((section: string) => {
    setPreset(section);
    setView("mock");
  }, []);
  const clearPreset = useCallback(() => setPreset(null), []);

  if (!course || !loaded) return null;
  const current: View = view ?? "papers";
  const empty = ready.length === 0 && current !== "papers";

  return (
    <div className="papers">
      <div className="papers-head">
        <div className="seg" role="group" aria-label="Past papers view">
          {VIEWS.map((v) => (
            <button key={v.id} className={`seg-btn ${current === v.id ? "on" : ""}`} aria-pressed={current === v.id} onClick={() => setView(v.id)}>
              {v.label}
              {v.id === "papers" && papers.length > 0 && <span className="seg-count">{papers.length}</span>}
            </button>
          ))}
        </div>
        {processing && <span className="muted small">Reading papers…</span>}
      </div>

      {empty ? (
        <div className="chat-empty card">
          {processing ? <Target size={30} /> : <FileStack size={30} />}
          <h2>{processing ? "Your papers are being read" : "Start with a past paper"}</h2>
          <p>
            {processing
              ? "This view fills in as soon as the first paper is ready."
              : "Upload previous exam papers to see how the exam is set and get a revision plan built around it."}
          </p>
          {!processing && <button className="btn primary" onClick={() => setView("papers")}>Add papers</button>}
        </div>
      ) : (
        <>
          {current === "papers" && <PaperLibrary papers={papers} refresh={refresh} />}
          {current === "blueprint" && <Blueprint version={version} onRemap={() => void remap()} />}
          {current === "plan" && <RevisionPlan version={version} onPractise={practise} />}
          {current === "mock" && <MockPaperView structures={structures} presetTopic={preset} onPresetUsed={clearPreset} />}
        </>
      )}
    </div>
  );
}
