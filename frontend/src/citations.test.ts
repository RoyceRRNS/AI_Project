import { describe, expect, it } from "vitest";
import type { CitationReport } from "./api";
import { linkCitations, normalizeMath, normalizePdf, prettyPdfName } from "./citations";

const report = (valid: boolean): CitationReport => ({
  citations: [
    {
      raw: "[Source: Lecture_04_SVMs.pdf, p. 2]", pdf_name: "Lecture_04_SVMs.pdf", page: 2, page_end: 2, start: 0, end: 10,
      valid, reason: valid ? null : "page_not_in_context", document_id: valid ? "d1" : null, resolved_pdf: null, bboxes: [],
    },
  ],
  valid_count: valid ? 1 : 0, invalid_count: valid ? 0 : 1, uncited_sentences: [], claim_count: 1, coverage: 1, grounded: valid,
});

describe("linkCitations", () => {
  it("turns citations into chip links while streaming", () => {
    const { markdown, citations } = linkCitations("Slackness holds [Source: Lecture_04_SVMs.pdf, p. 2].");
    expect(markdown).toBe("Slackness holds  [p. 2](#cite-0).");
    expect(citations).toEqual([
      { index: 0, pdfName: "Lecture_04_SVMs.pdf", page: 2, pageEnd: 2, verified: null, status: "pending", inferred: false, targetPage: 2 },
    ]);
  });

  it("marks citations valid or invalid from the server report", () => {
    expect(linkCitations("x [Source: Lecture_04_SVMs.pdf, p. 2]", report(true)).citations[0].status).toBe("valid");
    expect(linkCitations("x [Source: Lecture_04_SVMs.pdf, p. 2]", report(false)).citations[0].status).toBe("invalid");
    expect(linkCitations("x [Source: Other.pdf, p. 9]", report(true)).citations[0].status).toBe("invalid");
  });

  it("points 'moved' citations at the page that actually supports the claim", () => {
    const r = report(true);
    r.citations[0] = { ...r.citations[0], page_supported: false, suggested_page: 1 };
    const [c] = linkCitations("x [Source: Lecture_04_SVMs.pdf, p. 2]", r).citations;
    expect(c.status).toBe("moved");
    expect(c.targetPage).toBe(1);
  });

  it("handles ranges and several sources in one bracket", () => {
    const { citations, markdown } = linkCitations("a [Source: A.pdf, pp. 3-4; Source: B.pdf, p. 7]");
    expect(citations.map((c) => [c.pdfName, c.page, c.pageEnd])).toEqual([["A.pdf", 3, 4], ["B.pdf", 7, 7]]);
    expect(markdown).toContain("[p. 3–4](#cite-0) [p. 7](#cite-1)");
  });

  it("leaves malformed brackets and ordinary links untouched", () => {
    expect(linkCitations("see [Source: nowhere]").markdown).toBe("see [Source: nowhere]");
    expect(linkCitations("[docs](https://x.y)").citations).toEqual([]);
  });
});

describe("helpers", () => {
  it("normalizes pdf names like the server", () => {
    expect(normalizePdf("Lecture_04_SVMs.pdf")).toBe(normalizePdf("lecture 04 svms"));
    expect(normalizePdf("dir/Notes.PDF")).toBe("notes");
  });

  it("converts \\( \\) and \\[ \\] math delimiters", () => {
    expect(normalizeMath("a \\(x^2\\) b")).toBe("a $x^2$ b");
    expect(normalizeMath("\\[ y = 1 \\]")).toContain("$$\ny = 1\n$$");
  });

  it("prettifies file names", () => {
    expect(prettyPdfName("Lecture_07_RNNs.pdf")).toBe("Lecture 07 RNNs");
  });
});
