import { memo, useMemo } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import rehypeKatex from "rehype-katex";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import "katex/dist/katex.min.css";
import type { CitationReport } from "../api";
import { linkCitations, normalizeMath, prettyPdfName, type InlineCitation } from "../citations";

interface Props {
  text: string;
  report?: CitationReport | null;
  onCite?: (c: InlineCitation) => void;
  className?: string;
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

export const Markdown = memo(function Markdown({ text, report, onCite, className }: Props) {
  const { markdown, citations } = useMemo(() => linkCitations(text, report), [text, report]);
  const components = useMemo<Components>(
    () => ({
      a({ href, children, ...rest }) {
        if (href?.startsWith("#cite-")) {
          const c = citations[Number(href.slice(6))];
          if (c) return <CitationChip c={c} onCite={onCite} />;
        }
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
    [citations, onCite],
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
  return (
    <ReactMarkdown
      remarkPlugins={[remarkMath]}
      rehypePlugins={[[rehypeKatex, { throwOnError: false, strict: "ignore" }]]}
      components={{ p: ({ children }) => <span className="math-text">{children}</span> }}
    >
      {normalizeMath(text)}
    </ReactMarkdown>
  );
}
