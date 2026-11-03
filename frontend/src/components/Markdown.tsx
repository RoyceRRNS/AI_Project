import { memo, useMemo, type ReactNode } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import rehypeKatex from "rehype-katex";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import { FileWarning } from "lucide-react";
import "katex/dist/katex.min.css";
import type { CitationReport } from "../api";
import { linkCitations, normalizeMath, prettyPdfName, type InlineCitation } from "../citations";
import { BROKEN_FORMULA_HREF, prepareMath } from "../math";

interface Props {
  text: string;
  report?: CitationReport | null;
  onCite?: (c: InlineCitation) => void;
  className?: string;
  /** Shown in place of a formula that cannot be rendered, instead of the default note. */
  renderUnreadable?: (latex: string) => ReactNode;
}

/** Stands in for a formula the PDF parser misread so badly that it cannot be rendered. */
export function UnreadableFormula({ latex, display = false }: { latex: string; display?: boolean }) {
  return (
    <span className={`formula-unreadable${display ? " display" : ""}`} title={`What the parser read: ${latex}`}>
      <FileWarning size={14} aria-hidden /> This formula couldn't be read from the PDF
    </span>
  );
}

function unreadableLink(href: string | undefined, broken: string[], render?: (latex: string) => ReactNode) {
  const m = href ? BROKEN_FORMULA_HREF.exec(href) : null;
  const latex = m ? broken[Number(m[2])] : undefined;
  if (!m || latex === undefined) return undefined;
  return render ? <>{render(latex)}</> : <UnreadableFormula latex={latex} display={m[1] === "d"} />;
}

function CitationChip({ c, onCite }: { c: InlineCitation; onCite?: (c: InlineCitation) => void }) {
  const title =
    c.status === "invalid"
      ? `Could not verify: ${prettyPdfName(c.pdfName)} p. ${c.page} is not in the retrieved notes`
      : c.status === "moved"
        ? `Cited p. ${c.page}, but the supporting passage is on p. ${c.targetPage}: opens p. ${c.targetPage}`
        : c.inferred
          ? `Citation added by the grounding check: this sentence matches ${prettyPdfName(c.pdfName)} p. ${c.page}`
          : `${prettyPdfName(c.pdfName)} · page ${c.page}${c.status === "valid" ? " (verified)" : ""}`;
  return (
    <button
      type="button"
      className={`cite cite-${c.status}${c.inferred ? " cite-inferred" : ""}`}
      title={title}
      aria-label={`Open citation: ${prettyPdfName(c.pdfName)}, page ${c.page}`}
      onClick={() => onCite?.(c)}
      disabled={c.status === "invalid"}
    >
      <span className="cite-doc">{shortName(c.pdfName)}</span>
      <span className="cite-page">p.{c.page}{c.status === "moved" ? ` → ${c.targetPage}` : ""}</span>
      {c.inferred && <span className="cite-auto">auto</span>}
    </button>
  );
}

function shortName(name: string): string {
  const pretty = prettyPdfName(name);
  return pretty.length > 22 ? `${pretty.slice(0, 20)}…` : pretty;
}

export const Markdown = memo(function Markdown({ text, report, onCite, className, renderUnreadable }: Props) {
  const { markdown, citations, broken } = useMemo(() => {
    const linked = linkCitations(text, report);
    return { ...prepareMath(linked.markdown), citations: linked.citations };
  }, [text, report]);
  const components = useMemo<Components>(
    () => ({
      a({ href, children, ...rest }) {
        if (href?.startsWith("#cite-")) {
          const c = citations[Number(href.slice(6))];
          if (c) return <CitationChip c={c} onCite={onCite} />;
        }
        const unreadable = unreadableLink(href, broken, renderUnreadable);
        if (unreadable) return unreadable;
        return (
          <a href={href} target="_blank" rel="noreferrer" {...rest}>
            {children}
          </a>
        );
      },
      table({ children }) {
        return (
          <div className="table-wrap">
            <table>{children}</table>
          </div>
        );
      },
    }),
    [citations, broken, onCite, renderUnreadable],
  );
  return (
    <div className={`md ${className ?? ""}`}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[[rehypeKatex, { throwOnError: false, strict: "ignore" }]]}
        components={components}
      >
        {markdown}
      </ReactMarkdown>
    </div>
  );
});

/** Math-aware inline renderer for short strings (quiz stems and options). */
export function MathText({ text }: { text: string }) {
  const { markdown, broken } = useMemo(() => prepareMath(normalizeMath(text)), [text]);
  return (
    <ReactMarkdown
      remarkPlugins={[remarkMath]}
      rehypePlugins={[[rehypeKatex, { throwOnError: false, strict: "ignore" }]]}
      components={{
        p: ({ children }) => <span className="math-text">{children}</span>,
        a: ({ href, children, ...rest }) =>
          unreadableLink(href, broken) ?? <a href={href} {...rest}>{children}</a>,
      }}
    >
      {markdown}
    </ReactMarkdown>
  );
}
