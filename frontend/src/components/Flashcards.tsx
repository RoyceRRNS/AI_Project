import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  BookMarked, CheckCircle2, LayoutGrid, Layers, Loader2, Play, RefreshCw, RotateCcw, Sparkles, X,
} from "lucide-react";
import { api, type CardKind, type Flashcard, type FlashcardDeck, type ReviewResult } from "../api";
import { prettyPdfName } from "../citations";
import { isTyping } from "../dom";
import { mathQuality, type MathQuality } from "../math";
import { useStore } from "../store";
import { Markdown, MathText, UnreadableFormula } from "./Markdown";

type Filter = "all" | CardKind;

const KIND_LABEL: Record<CardKind, string> = { formula: "Formula", point: "Key point" };
const FILTERS: { id: Filter; label: string }[] = [
  { id: "all", label: "All" },
  { id: "formula", label: "Formulas" },
  { id: "point", label: "Key points" },
];
const BOXES = [1, 2, 3, 4, 5];

interface Generation {
  running: boolean;
  index: number;
  total: number;
  section: string;
  created: number;
  warnings: string[];
  error: string | null;
}

export function deckStats(cards: Flashcard[]) {
  const byBox: Record<number, number> = { 1: 0, 2: 0, 3: 0, 4: 0, 5: 0 };
  for (const c of cards) byBox[c.box] = (byBox[c.box] ?? 0) + 1;
  return {
    total: cards.length,
    due: cards.filter((c) => c.due).length,
    formulas: cards.filter((c) => c.kind === "formula").length,
    points: cards.filter((c) => c.kind === "point").length,
    byBox,
  };
}

/** Due cards first, weakest box first, then in the order of the notes. */
export function studyOrder(cards: Flashcard[]): Flashcard[] {
  return cards
    .map((card, i) => ({ card, i }))
    .sort((a, b) => Number(b.card.due) - Number(a.card.due) || a.card.box - b.card.box || a.i - b.i)
    .map((x) => x.card);
}

const formatDate = (iso: string) =>
  new Date(iso).toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });

export function Flashcards() {
  const { course, readyDocs } = useStore();
  const [deck, setDeck] = useState<FlashcardDeck | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [filter, setFilter] = useState<Filter>("all");
  const [browsing, setBrowsing] = useState(false);
  const [session, setSession] = useState<Flashcard[] | null>(null);
  const [gen, setGen] = useState<Generation | null>(null);
  const [confirmRebuild, setConfirmRebuild] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  const load = useCallback(async () => {
    if (!course) return;
    try {
      setDeck(await api.flashcards(course.id));
      setLoadError(null);
    } catch (e) {
      setLoadError((e as Error).message);
    }
  }, [course]);

  // Reload when documents finish processing or are removed: their cards come and go with them.
  const readyKey = readyDocs.map((d) => d.id).join(",");
  useEffect(() => {
    void load();
  }, [load, readyKey]);
  // Switching course remounts this component (keyed by course in <App/>), which ends any stream.
  useEffect(() => () => abortRef.current?.abort(), []);

  const generate = async (regenerate: boolean) => {
    if (!course) return;
    setConfirmRebuild(false);
    const controller = new AbortController();
    abortRef.current = controller;
    setGen({ running: true, index: 0, total: 0, section: "", created: 0, warnings: [], error: null });
    if (regenerate) setDeck((d) => d && { ...d, cards: [] });
    try {
      await api.generateFlashcards(
        course.id,
        { regenerate },
        (ev) => {
          const d = ev.data as Record<string, unknown>;
          if (ev.event === "deck") setGen((g) => g && { ...g, total: Number(d.sections) });
          if (ev.event === "progress")
            setGen((g) => g && { ...g, index: Number(d.index), total: Number(d.total), section: String(d.section) });
          if (ev.event === "cards") {
            const cards = d.cards as Flashcard[];
            setGen((g) => g && { ...g, created: g.created + cards.length });
            setDeck((prev) => prev && { ...prev, cards: [...prev.cards, ...cards] });
          }
          if (ev.event === "warning") setGen((g) => g && { ...g, warnings: [...g.warnings, String(d.message)] });
          if (ev.event === "error") setGen((g) => g && { ...g, error: String(d.message) });
        },
        controller.signal,
      );
    } catch (e) {
      if ((e as Error).name !== "AbortError") setGen((g) => g && { ...g, error: (e as Error).message });
    } finally {
      setGen((g) => g && { ...g, running: false });
      void load();
    }
  };

  const replaceCard = useCallback(
    (card: Flashcard) => setDeck((d) => d && { ...d, cards: d.cards.map((c) => (c.id === card.id ? card : c)) }),
    [],
  );

  const cards = useMemo(() => deck?.cards ?? [], [deck]);
  const visible = useMemo(() => (filter === "all" ? cards : cards.filter((c) => c.kind === filter)), [cards, filter]);
  const stats = deckStats(visible);

  if (!course) return null;

  if (session) {
    return (
      <StudySession
        cards={session}
        onReviewed={replaceCard}
        onExit={() => {
          setSession(null);
          void load();
        }}
      />
    );
  }

  const running = !!gen?.running;
  const noDocs = readyDocs.length === 0;
  const empty = cards.length === 0;
  const loaded = deck !== null;
  const nextDue = deck?.stats.next_due_at;

  return (
    <div className="flashcards">
      <section className="card deck-head" aria-labelledby="deck-title">
        <div className="deck-title">
          <h2 id="deck-title"><Layers size={20} /> Flashcards</h2>
          <p className="muted">
            Every formula in your notes, copied exactly, plus the key points worth remembering. Each card links to the
            page it comes from.
          </p>
        </div>

        {!empty && (
          <>
            <div className="deck-stats" aria-label="Deck summary">
              <Stat value={stats.total} label={stats.total === 1 ? "card" : "cards"} />
              <Stat value={stats.formulas} label="formulas" />
              <Stat value={stats.points} label="key points" />
              <Stat value={stats.due} label="due now" accent />
            </div>
            <BoxBar byBox={stats.byBox} total={stats.total} />
          </>
        )}

        {gen && <GenerationStatus gen={gen} onCancel={() => abortRef.current?.abort()} />}
        {loadError && (
          <p className="notice" role="alert">
            Could not load flashcards: {loadError}{" "}
            <button className="link-btn small" onClick={() => void load()}>Try again</button>
          </p>
        )}
        {!loaded && !loadError && <p className="muted small" role="status">Loading your deck…</p>}

        {loaded && <div className="deck-actions">
          {empty ? (
            <button className="btn primary lg" disabled={noDocs || running} onClick={() => void generate(false)}>
              {running ? <Loader2 size={16} className="spin" /> : <Sparkles size={16} />} Make flashcards
            </button>
          ) : (
            <>
              <button
                className="btn primary lg"
                disabled={visible.length === 0 || running}
                onClick={() => setSession(studyOrder(stats.due > 0 ? visible.filter((c) => c.due) : visible))}
              >
                <Play size={16} /> {stats.due > 0 ? `Study ${stats.due} due` : `Study all ${stats.total}`}
              </button>
              {stats.due > 0 && stats.due < stats.total && (
                <button className="btn" disabled={running} onClick={() => setSession(studyOrder(visible))}>
                  Study all {stats.total}
                </button>
              )}
              <button className="btn ghost" disabled={noDocs || running} onClick={() => void generate(false)}
                title="Make cards for sections that have none yet, e.g. newly uploaded notes">
                <Sparkles size={15} /> Add cards for new notes
              </button>
              <button className="btn ghost" disabled={noDocs || running} onClick={() => setConfirmRebuild(true)}>
                <RefreshCw size={15} /> Rebuild deck
              </button>
            </>
          )}
        </div>}
        {loaded && noDocs && <p className="notice">Upload and process notes in the Library first.</p>}
        {!empty && stats.due === 0 && nextDue && (
          <p className="muted small" data-testid="caught-up">
            <CheckCircle2 size={14} className="ok" /> All caught up. Next review: {formatDate(nextDue)}.
          </p>
        )}
        {confirmRebuild && (
          <div className="confirm-row" role="alertdialog" aria-label="Rebuild deck">
            <span>Rebuild every card? Your current cards and their review progress are replaced.</span>
            <button className="btn danger sm" onClick={() => void generate(true)}>Rebuild</button>
            <button className="btn sm" onClick={() => setConfirmRebuild(false)}>Cancel</button>
          </div>
        )}
      </section>

      {!empty && (
        <section className="deck-browse" aria-label="Cards">
          <div className="deck-toolbar">
            <div className="seg" role="group" aria-label="Card type">
              {FILTERS.map((f) => (
                <button key={f.id} className={`seg-btn ${filter === f.id ? "on" : ""}`} aria-pressed={filter === f.id}
                  onClick={() => setFilter(f.id)}>
                  {f.label}
                </button>
              ))}
            </div>
            <button className="btn ghost sm" aria-expanded={browsing} onClick={() => setBrowsing((b) => !b)}>
              <LayoutGrid size={15} /> {browsing ? "Hide cards" : `Browse ${visible.length} cards`}
            </button>
          </div>
          {browsing && (
            <div className="fc-grid">
              {visible.map((c) => <CardTile key={c.id} card={c} />)}
            </div>
          )}
        </section>
      )}
    </div>
  );
}

function Stat({ value, label, accent }: { value: number; label: string; accent?: boolean }) {
  return (
    <div className={`stat ${accent ? "accent" : ""}`}>
      <strong>{value}</strong>
      <span className="muted small">{label}</span>
    </div>
  );
}

function BoxBar({ byBox, total }: { byBox: Record<number, number>; total: number }) {
  if (!total) return null;
  return (
    <div className="box-bar-wrap">
      <div className="box-bar" role="img" aria-label={BOXES.map((b) => `Box ${b}: ${byBox[b] ?? 0}`).join(", ")}>
        {BOXES.filter((b) => byBox[b]).map((b) => (
          <div key={b} className={`box-seg box-${b}`} style={{ flexGrow: byBox[b] }} title={`Box ${b}: ${byBox[b]} cards`} />
        ))}
      </div>
      <div className="box-legend muted small"><span>New</span><span>Box 1 → 5</span><span>Well known</span></div>
    </div>
  );
}

function GenerationStatus({ gen, onCancel }: { gen: Generation; onCancel: () => void }) {
  const pct = gen.total ? Math.round((gen.index / gen.total) * 100) : 0;
  return (
    <div className="gen-status" role="status">
      {gen.running ? (
        <>
          <div className="gen-line">
            <Loader2 size={15} className="spin" />
            <span>
              {gen.total ? `Section ${gen.index} of ${gen.total}` : "Reading your notes…"}
              {gen.section && <span className="muted"> · {gen.section}</span>}
            </span>
            <span className="muted small">{gen.created} cards so far</span>
            <button className="link-btn small" onClick={onCancel}>Stop</button>
          </div>
          <div className="progress"><div style={{ width: `${pct}%` }} /></div>
        </>
      ) : gen.error ? null : (
        <span className="small">
          {gen.total === 0
            ? "Your deck already covers every section. Use Rebuild deck to start over."
            : `Made ${gen.created} new cards.`}
        </span>
      )}
      {gen.error && <p className="notice" role="alert">{gen.error}</p>}
      {gen.warnings.length > 0 && (
        <details className="small">
          <summary>{gen.warnings.length} section{gen.warnings.length > 1 ? "s" : ""} had problems</summary>
          <ul>{gen.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>
        </details>
      )}
    </div>
  );
}

function SourceLink({ card }: { card: Flashcard }) {
  const { openViewer } = useStore();
  return (
    <button
      className="link-btn small"
      onClick={() =>
        openViewer({
          documentId: card.document_id,
          pdfName: card.pdf_name,
          page: card.source_page,
          bboxes: card.bboxes,
          label: card.section_title,
        })
      }
    >
      <BookMarked size={13} /> {prettyPdfName(card.pdf_name)} · p. {card.source_page}
    </button>
  );
}

const CROP_CAPTION: Record<MathQuality, string> = {
  clean: "As written in your notes",
  doubtful: "As written in your notes: the text version may be misread",
  unreadable: "From your notes: this formula couldn't be converted to text",
};

/** The formula cropped from the PDF page, which is right even when the parser misread it. */
function FormulaFromNotes({ card, quality, fallback }: { card: Flashcard; quality: MathQuality; fallback: ReactNode }) {
  const [failed, setFailed] = useState(false);
  const box = card.formula_bbox;
  if (!box || failed) return <>{fallback}</>;
  return (
    <figure className="fc-figure">
      <img
        src={api.documentRegionUrl(card.document_id, box)}
        alt={`The formula on page ${box.page} of ${prettyPdfName(card.pdf_name)}`}
        loading="lazy"
        onError={() => setFailed(true)}
      />
      <figcaption className="muted small">{CROP_CAPTION[quality]}</figcaption>
    </figure>
  );
}

/**
 * A formula card's answer. Studying shows the formula as text and as the notes have it; the
 * card browser shows the notes' version only when the text version is in doubt.
 */
function FormulaAnswer({ card, compact = false }: { card: Flashcard; compact?: boolean }) {
  const quality = useMemo(() => mathQuality(card.back), [card.back]);
  const text = quality === "unreadable"
    ? <UnreadableFormula latex={card.back.replace(/^\s*\$\$|\$\$\s*$/g, "").trim()} display />
    : <Markdown text={card.back} />;
  if (compact && quality === "clean") return text;
  if (compact) return <FormulaFromNotes key={card.id} card={card} quality={quality} fallback={text} />;
  return (
    <>
      {quality !== "unreadable" && text}
      <FormulaFromNotes key={card.id} card={card} quality={quality} fallback={quality === "unreadable" ? text : null} />
    </>
  );
}

function CardTile({ card }: { card: Flashcard }) {
  return (
    <article className="fc-tile" data-testid="card-tile">
      <div className="fc-meta">
        <span className={`badge kind-${card.kind}`}>{KIND_LABEL[card.kind]}</span>
        <span className="badge ghost">Box {card.box}</span>
        {card.due && <span className="badge warn">Due</span>}
      </div>
      <div className="fc-tile-front"><MathText text={card.front} /></div>
      <div className="fc-tile-back">
        {card.kind === "formula" ? <FormulaAnswer card={card} compact /> : <Markdown text={card.back} />}
      </div>
      <div className="fc-tile-foot">
        <span className="muted small">{card.section_title}</span>
        <SourceLink card={card} />
      </div>
    </article>
  );
}

export function StudySession({
  cards, onReviewed, onExit,
}: {
  cards: Flashcard[];
  onReviewed: (card: Flashcard) => void;
  onExit: () => void;
}) {
  const { notify } = useStore();
  const [queue, setQueue] = useState(cards);
  const [pos, setPos] = useState(0);
  const [flipped, setFlipped] = useState(false);
  const [busy, setBusy] = useState(false);
  const [tally, setTally] = useState({ good: 0, again: 0 });
  const card = queue[pos];

  const review = useCallback(
    async (result: ReviewResult) => {
      if (!card || !flipped || busy) return;
      setBusy(true);
      try {
        const updated = await api.reviewFlashcard(card.id, result);
        onReviewed(updated);
        setTally((t) => ({ ...t, [result]: t[result] + 1 }));
        if (result === "again") setQueue((q) => [...q, updated]); // comes back later in this session
        setPos((p) => p + 1);
        setFlipped(false);
      } catch (e) {
        notify((e as Error).message, "error");
      } finally {
        setBusy(false);
      }
    },
    [card, flipped, busy, onReviewed, notify],
  );

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (isTyping(e.target) || !card) return;
      if (e.key === " " || e.key === "Enter") {
        // A focused button or link handles these keys itself; flipping here as well would undo it.
        if (e.target instanceof Element && e.target.closest("button, a, [role=button]")) return;
        e.preventDefault();
        setFlipped((f) => !f);
      }
      if (e.key === "1") void review("again");
      if (e.key === "2") void review("good");
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [card, review]);

  if (!card) {
    return (
      <div className="card fc-done" data-testid="session-done">
        <CheckCircle2 size={34} className="ok" />
        <h2>Session complete</h2>
        <p className="muted">
          {tally.good} known · {tally.again} to review again
        </p>
        <div className="deck-actions">
          <button className="btn primary" onClick={() => { setQueue(cards); setPos(0); setTally({ good: 0, again: 0 }); }}>
            <RotateCcw size={15} /> Study these again
          </button>
          <button className="btn" onClick={onExit}>Back to deck</button>
        </div>
      </div>
    );
  }

  return (
    <div className="study">
      <div className="study-head">
        <span className="badge">Card {pos + 1} of {queue.length}</span>
        <span className="muted small">{tally.good} known · {tally.again} again</span>
        <button className="icon-btn" aria-label="End session" title="End session" onClick={onExit}>
          <X size={16} />
        </button>
      </div>
      <div className="progress"><div style={{ width: `${(pos / queue.length) * 100}%` }} /></div>

      <div className="flashcard-scene">
        <div
          className={`flashcard ${flipped ? "flipped" : ""}`}
          role="button"
          tabIndex={0}
          aria-label={flipped ? "Show the question" : "Show the answer"}
          data-testid="flashcard"
          onClick={() => setFlipped((f) => !f)}
          onKeyDown={(e) => {
            if (e.key === " " || e.key === "Enter") {
              e.preventDefault();
              setFlipped((f) => !f);
            }
          }}
        >
          <div className="flashcard-face front" aria-hidden={flipped}>
            <div className="fc-meta">
              <span className={`badge kind-${card.kind}`}>{KIND_LABEL[card.kind]}</span>
              <span className="muted small">{card.section_title}</span>
            </div>
            <div className="fc-prompt"><MathText text={card.front} /></div>
            <span className="fc-hint muted small">Click or press Space to flip</span>
          </div>
          <div className="flashcard-face back" aria-hidden={!flipped} data-testid="card-back">
            <div className="fc-meta">
              <span className={`badge kind-${card.kind}`}>{KIND_LABEL[card.kind]}</span>
              <span className="muted small">Answer</span>
            </div>
            <div className="fc-answer">
              {card.kind === "formula" ? <FormulaAnswer card={card} /> : <Markdown text={card.back} />}
            </div>
          </div>
        </div>
      </div>

      {flipped ? (
        <div className="fc-after">
          <blockquote className="quote">
            <MathText text={`“${card.source_quote}”`} />
            <SourceLink card={card} />
          </blockquote>
          <div className="fc-grade">
            <button className="btn again" disabled={busy} onClick={() => void review("again")}>
              <RotateCcw size={15} /> Again <kbd>1</kbd>
            </button>
            <button className="btn primary" disabled={busy} onClick={() => void review("good")}>
              {busy ? <Loader2 size={15} className="spin" /> : <CheckCircle2 size={15} />} Got it <kbd>2</kbd>
            </button>
          </div>
        </div>
      ) : (
        <div className="fc-grade">
          <button className="btn primary" onClick={() => setFlipped(true)}>
            Show answer <kbd>Space</kbd>
          </button>
        </div>
      )}
    </div>
  );
}
