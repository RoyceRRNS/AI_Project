import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import {
  AlertTriangle, BookOpenCheck, Check, ChevronDown, Copy, FileSearch, Loader2, MessageSquarePlus, SendHorizonal,
  ShieldCheck, Square, Telescope,
} from "lucide-react";
import { api, type CitationReport, type Source } from "../api";
import { prettyPdfName, type InlineCitation } from "../citations";
import { storage, useStore } from "../store";
import { Markdown } from "./Markdown";

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  stage?: "retrieving" | "generating" | "done" | "error" | "stopped";
  sources?: Source[];
  expandedQueries?: string[];
  report?: CitationReport | null;
  notFound?: boolean;
  error?: string;
}

const uid = () => Math.random().toString(36).slice(2, 10);
const historyKey = (courseId: string) => `cnr.chat.${courseId}`;

export function loadHistory(courseId: string): ChatMessage[] {
  try {
    const raw = storage.get(historyKey(courseId));
    const parsed = raw ? (JSON.parse(raw) as ChatMessage[]) : [];
    return parsed.map((m) => (m.stage === "retrieving" || m.stage === "generating" ? { ...m, stage: "stopped" } : m));
  } catch {
    return [];
  }
}

export function Chat() {
  const { course, readyDocs, docs, openViewer, notify, health } = useStore();
  const [messages, setMessages] = useState<ChatMessage[]>(() => (course ? loadHistory(course.id) : []));
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [deepSearch, setDeepSearch] = useState(false);
  const [scope, setScope] = useState<string[]>([]);
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const abortRef = useRef<AbortController | null>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  // Load history once per course (the component is also keyed by course in <App/>).
  const loadedFor = useRef(course?.id ?? null);
  useEffect(() => {
    if (!course || loadedFor.current === course.id) return;
    loadedFor.current = course.id;
    setMessages(loadHistory(course.id));
  }, [course]);

  useEffect(() => {
    if (course && !busy) storage.set(historyKey(course.id), JSON.stringify(messages.slice(-40)));
  }, [messages, course, busy]);

  useEffect(() => {
    listRef.current?.scrollTo?.({ top: listRef.current.scrollHeight, behavior: "smooth" });
  }, [messages]);

  const courseId = course?.id;
  useEffect(() => {
    if (!courseId || !readyDocs.length) return;
    let live = true;
    api
      .mastery(courseId)
      .then((m) => {
        if (!live) return;
        const titles = Array.from(new Set(m.sections.map((s) => s.section_title.replace(/^\d+(\.\d+)*\s+/, ""))))
          .filter((t) => t.length > 3 && !/^lecture\b/i.test(t))
          .slice(0, 3);
        setSuggestions(titles.map((t) => `Explain ${t} in simple terms.`));
      })
      .catch(() => live && setSuggestions([]));
    return () => {
      live = false;
    };
  }, [courseId, readyDocs.length]);

  const update = (id: string, patch: Partial<ChatMessage> | ((m: ChatMessage) => Partial<ChatMessage>)) =>
    setMessages((list) => list.map((m) => (m.id === id ? { ...m, ...(typeof patch === "function" ? patch(m) : patch) } : m)));

  const send = useCallback(
    async (text?: string) => {
      const question = (text ?? input).trim();
      if (!course || !question || busy) return;
      const history = messages
        .filter((m) => m.content && (m.role === "user" || m.stage === "done") && !m.notFound)
        .slice(-6)
        .map((m) => ({ role: m.role, content: m.content }));
      const userMsg: ChatMessage = { id: uid(), role: "user", content: question };
      const botId = uid();
      setMessages((list) => [...list, userMsg, { id: botId, role: "assistant", content: "", stage: "retrieving" }]);
      setInput("");
      setBusy(true);
      const controller = new AbortController();
      abortRef.current = controller;
      try {
        await api.chat(
          {
            course_id: course.id,
            question,
            history,
            document_ids: scope.length ? scope : null,
            expansion: deepSearch ? "both" : "none",
          },
          (ev) => {
            const data = ev.data as Record<string, unknown>;
            switch (ev.event) {
              case "status":
                update(botId, { stage: data.stage as ChatMessage["stage"] });
                break;
              case "sources":
                update(botId, {
                  sources: data.sources as Source[],
                  expandedQueries: (data.expanded_queries as string[]) ?? [],
                });
                break;
              case "token":
                update(botId, (m) => ({ content: m.content + String(data.text), stage: "generating" }));
                break;
              case "not_found":
                update(botId, { content: String(data.message), notFound: true });
                break;
              case "citations": {
                const report = data as unknown as CitationReport;
                update(botId, (m) => ({ report, content: report.annotated_answer || m.content }));
                break;
              }
              case "error":
                update(botId, { stage: "error", error: String(data.message) });
                break;
              case "done":
                update(botId, (m) => ({
                  stage: m.stage === "error" ? "error" : "done",
                  notFound: m.notFound || Boolean(data.not_found),
                }));
                break;
            }
          },
          controller.signal,
        );
      } catch (e) {
        if ((e as Error).name === "AbortError") update(botId, { stage: "stopped" });
        else update(botId, { stage: "error", error: (e as Error).message });
      } finally {
        setBusy(false);
        abortRef.current = null;
        inputRef.current?.focus();
      }
    },
    [course, input, busy, messages, scope, deepSearch],
  );

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void send();
    }
  };

  const openSource = useCallback(
    (s: Source, page?: number) => {
      const p = page ?? s.highlight_bboxes[0]?.page ?? s.page_start;
      openViewer({
        documentId: s.document_id,
        pdfName: s.pdf_name,
        page: p,
        bboxes: s.highlight_bboxes.filter((b) => b.page === p),
        label: s.section_title,
      });
    },
    [openViewer],
  );

  const openCitation = useCallback(
    (c: InlineCitation, msg: ChatMessage) => {
      const v = c.verified;
      const docId = v?.document_id ?? msg.sources?.find((s) => s.pdf_name === c.pdfName)?.document_id;
      if (!docId) {
        notify("This citation could not be matched to a document", "error");
        return;
      }
      openViewer({
        documentId: docId,
        pdfName: v?.resolved_pdf ?? c.pdfName,
        page: c.targetPage,
        bboxes: v?.bboxes ?? [],
        label: c.status === "moved" ? `Cited p. ${c.page} · passage found on p. ${c.targetPage}` : `Cited page ${c.page}`,
      });
    },
    [openViewer, notify],
  );

  const scopeLabel = useMemo(() => {
    if (!scope.length) return "All documents";
    if (scope.length === 1) return prettyPdfName(docs.find((d) => d.id === scope[0])?.pdf_name ?? "1 document");
    return `${scope.length} documents`;
  }, [scope, docs]);

  if (!course) return null;
  const noDocs = readyDocs.length === 0;

  return (
    <div className="chat">
      <div className="chat-list" ref={listRef} aria-live="polite">
        {messages.length === 0 && (
          <div className="chat-empty">
            <BookOpenCheck size={30} />
            <h2>Ask anything about {course.code || course.name}</h2>
            <p className="muted">
              Answers come only from your uploaded notes. Every claim links to the page it came from.
            </p>
            {noDocs ? (
              <p className="notice">Upload and process at least one document in the Library to start chatting.</p>
            ) : (
              <div className="suggestions">
                {suggestions.map((s) => (
                  <button key={s} className="chip" onClick={() => void send(s)}>{s}</button>
                ))}
              </div>
            )}
          </div>
        )}
        {messages.map((m) =>
          m.role === "user" ? (
            <div key={m.id} className="msg user"><div className="bubble">{m.content}</div></div>
          ) : (
            <AssistantMessage key={m.id} m={m} onCite={(c) => openCitation(c, m)} onSource={openSource} />
          ),
        )}
      </div>

      <div className="composer">
        <div className="composer-tools">
          <ScopePicker scope={scope} setScope={setScope} label={scopeLabel} />
          <button
            className={`toggle ${deepSearch ? "on" : ""}`}
            onClick={() => setDeepSearch((v) => !v)}
            aria-pressed={deepSearch}
            title="Rewrites your question several ways and drafts a hypothetical answer to widen the search (slower)"
          >
            <Telescope size={14} /> Deep search
          </button>
          {messages.length > 0 && (
            <button className="toggle" onClick={() => setMessages([])} disabled={busy} title="Start a new conversation">
              <MessageSquarePlus size={14} /> New chat
            </button>
          )}
          {health && !health.llm.ok && (
            <span className="warn small"><AlertTriangle size={13} /> {health.llm.detail}</span>
          )}
        </div>
        <div className="composer-box">
          <textarea
            ref={inputRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder={noDocs ? "Upload notes first…" : "Ask about your notes…  (Enter to send, Shift+Enter for a new line)"}
            disabled={noDocs}
            rows={Math.min(6, Math.max(1, input.split("\n").length))}
            aria-label="Your question"
            maxLength={2000}
          />
          {busy ? (
            <button className="btn send" onClick={() => abortRef.current?.abort()} aria-label="Stop generating">
              <Square size={16} />
            </button>
          ) : (
            <button className="btn primary send" onClick={() => void send()} disabled={!input.trim() || noDocs} aria-label="Send question">
              <SendHorizonal size={16} />
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

function AssistantMessage({
  m, onCite, onSource,
}: { m: ChatMessage; onCite: (c: InlineCitation) => void; onSource: (s: Source, page?: number) => void }) {
  const [open, setOpen] = useState(false);
  const [copied, setCopied] = useState(false);
  const thinking = (m.stage === "retrieving" || m.stage === "generating") && !m.content;

  if (m.notFound) {
    return (
      <div className="msg bot">
        <div className="bubble not-found" data-testid="not-found">
          <FileSearch size={18} />
          <div>
            <strong>Not found in your course notes</strong>
            <p className="muted small">
              None of your documents covers this closely enough to answer without guessing. Try rephrasing with terms
              from the lecture, turn on Deep search, or upload the relevant notes.
            </p>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="msg bot">
      <div className="bubble">
        {thinking ? (
          <div className="thinking" data-testid="thinking">
            <Loader2 size={15} className="spin" />
            {m.stage === "retrieving" ? "Searching your notes…" : "Writing a grounded answer…"}
          </div>
        ) : (
          <Markdown text={m.content} report={m.report} onCite={onCite} />
        )}
        {m.stage === "generating" && m.content && <span className="cursor" aria-hidden />}
        {m.error && (
          <div className="error-line" role="alert"><AlertTriangle size={14} /> {m.error}</div>
        )}
        {m.stage === "stopped" && <div className="muted small">Stopped.</div>}
        {(m.report || (m.sources && m.sources.length > 0)) && (
          <div className="msg-foot">
            {m.report && <GroundingBadge report={m.report} />}
            {m.sources && m.sources.length > 0 && (
              <button className="link-btn small" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
                {m.sources.length} source{m.sources.length > 1 ? "s" : ""} <ChevronDown size={13} className={open ? "rot" : ""} />
              </button>
            )}
            {m.stage === "done" && (
              <button
                className="link-btn small"
                onClick={() => {
                  void navigator.clipboard?.writeText(m.content);
                  setCopied(true);
                  window.setTimeout(() => setCopied(false), 1500);
                }}
                aria-label="Copy answer"
              >
                {copied ? <Check size={13} /> : <Copy size={13} />} {copied ? "Copied" : "Copy"}
              </button>
            )}
          </div>
        )}
        {open && m.sources && (
          <ol className="sources">
            {m.sources.map((s) => (
              <li key={s.parent_id}>
                <button className="source" onClick={() => onSource(s)}>
                  <div className="source-head">
                    <strong>{s.section_title}</strong>
                    <span className="muted small">
                      {prettyPdfName(s.pdf_name)} · p. {s.page_start}{s.page_end !== s.page_start ? `–${s.page_end}` : ""}
                    </span>
                  </div>
                  <div className="relevance" title={`Reranker relevance ${(s.rerank_score * 100).toFixed(0)}%`}>
                    <div style={{ width: `${Math.round(s.rerank_score * 100)}%` }} />
                  </div>
                  <p className="excerpt small">{s.excerpt}</p>
                </button>
              </li>
            ))}
          </ol>
        )}
        {m.expandedQueries && m.expandedQueries.length > 0 && open && (
          <div className="muted small">Also searched: {m.expandedQueries.join(" · ")}</div>
        )}
      </div>
    </div>
  );
}

function GroundingBadge({ report }: { report: CitationReport }) {
  const auto = report.inferred_count ? ` · ${report.inferred_count} added automatically` : "";
  if (report.grounded) {
    return (
      <span className="badge ok" data-testid="grounding" title="Every claim is backed by a page of your notes">
        <ShieldCheck size={13} /> {report.valid_count} citation{report.valid_count === 1 ? "" : "s"} verified{auto}
      </span>
    );
  }
  const issues: string[] = [];
  if (report.invalid_count) issues.push(`${report.invalid_count} unverifiable citation${report.invalid_count > 1 ? "s" : ""}`);
  if (report.page_mismatch_count) issues.push(`${report.page_mismatch_count} cited to the wrong page`);
  if (report.uncited_sentences.length) issues.push(`${report.uncited_sentences.length} unsupported claim${report.uncited_sentences.length > 1 ? "s" : ""}`);
  if (!report.citations.length) issues.push("no citations");
  return (
    <span className="badge warn" data-testid="grounding" title={report.uncited_sentences.map((s) => `Unsupported: ${s}`).join("\n")}>
      <AlertTriangle size={13} /> {report.valid_count} verified · {issues.join(" · ")}
    </span>
  );
}

function ScopePicker({ scope, setScope, label }: { scope: string[]; setScope: (s: string[]) => void; label: string }) {
  const { readyDocs } = useStore();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const close = (e: MouseEvent) => !ref.current?.contains(e.target as Node) && setOpen(false);
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);
  return (
    <div className="scope" ref={ref}>
      <button className="toggle" onClick={() => setOpen((v) => !v)} aria-haspopup="listbox" aria-expanded={open}>
        <FileSearch size={14} /> {label} <ChevronDown size={13} />
      </button>
      {open && (
        <div className="menu" role="listbox" aria-label="Search scope">
          <label className="menu-item">
            <input type="checkbox" checked={scope.length === 0} onChange={() => setScope([])} /> All documents
          </label>
          {readyDocs.map((d) => (
            <label key={d.id} className="menu-item">
              <input
                type="checkbox"
                checked={scope.includes(d.id)}
                onChange={(e) => setScope(e.target.checked ? [...scope, d.id] : scope.filter((x) => x !== d.id))}
              />
              {prettyPdfName(d.pdf_name)}
            </label>
          ))}
        </div>
      )}
    </div>
  );
}
