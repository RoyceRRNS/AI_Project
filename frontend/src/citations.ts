/** Client-side citation parsing so chips appear while the answer is still streaming. */

import type { Citation, CitationReport } from "./api";

export interface InlineCitation {
  index: number;
  pdfName: string;
  page: number;
  pageEnd: number;
  /** null until the server's verification report arrives. */
  verified: Citation | null;
  /** "moved": in the retrieved notes, but the claim is on another page of that section. */
  status: "pending" | "valid" | "invalid" | "moved";
  /** Added automatically by the server's citation check rather than written by the model. */
  inferred: boolean;
  /** The page the viewer should open (the suggested page for "moved" citations). */
  targetPage: number;
}

const BRACKET = /\[(\s*Source\s*:[^[\]]+)\]/gi;
const PART = /Source\s*:\s*(.+?)\s*,\s*(?:pages?|pp?)\.?\s*(\d+)(?:\s*[-–]\s*(\d+))?\s*$/i;

export function normalizePdf(name: string): string {
  let stem = name.trim().replace(/^['"`]|['"`]$/g, "").split(/[\\/]/).pop() ?? "";
  if (stem.toLowerCase().endsWith(".pdf")) stem = stem.slice(0, -4);
  return stem.replace(/[\s_-]+/g, "").toLowerCase();
}

/** Convert LaTeX \( \) and \[ \] delimiters (common in LLM output) to $ / $$. */
export function normalizeMath(text: string): string {
  return text
    .replace(/\\\[([\s\S]+?)\\\]/g, (_m, body) => `\n$$\n${String(body).trim()}\n$$\n`)
    .replace(/\\\(([\s\S]+?)\\\)/g, (_m, body) => `$${String(body).trim()}$`);
}

/**
 * Replace each citation with a markdown link `[p. N](#cite-i)` and return the parsed list.
 * The link is rendered as an interactive chip by the Markdown component.
 */
export function linkCitations(
  text: string,
  report?: CitationReport | null,
): { markdown: string; citations: InlineCitation[] } {
  const citations: InlineCitation[] = [];
  const verified = report?.citations ?? [];
  const markdown = normalizeMath(text).replace(BRACKET, (_whole, inner: string) => {
    const links: string[] = [];
    for (const rawPart of inner.split(";")) {
      const part = rawPart.trim().toLowerCase().startsWith("source") ? rawPart.trim() : `Source: ${rawPart.trim()}`;
      const m = PART.exec(part);
      if (!m) continue;
      const page = Number(m[2]);
      const pageEnd = m[3] ? Math.max(page, Number(m[3])) : page;
      const pdfName = m[1].trim();
      const index = citations.length;
      // The report lists citations in text order; fall back to matching by file and page.
      const same = (v?: Citation) => !!v && normalizePdf(v.pdf_name) === normalizePdf(pdfName) && v.page === page;
      const match = same(verified[index]) ? verified[index] : verified.find(same);
      const moved = !!match?.valid && match.page_supported === false && !!match.suggested_page;
      citations.push({
        index,
        pdfName,
        page,
        pageEnd,
        verified: match ?? null,
        status: !report ? "pending" : !match?.valid ? "invalid" : moved ? "moved" : "valid",
        inferred: !!match?.inferred,
        targetPage: moved ? (match!.suggested_page as number) : page,
      });
      links.push(`[p. ${page}${pageEnd !== page ? `–${pageEnd}` : ""}](#cite-${index})`);
    }
    return links.length ? ` ${links.join(" ")}` : _whole;
  });
  return { markdown, citations };
}

export function prettyPdfName(name: string): string {
  return name.replace(/\.pdf$/i, "").replace(/_/g, " ");
}
