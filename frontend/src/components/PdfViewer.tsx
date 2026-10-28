import { useEffect, useMemo, useRef, useState } from "react";
import { ChevronLeft, ChevronRight, Minus, Plus, X, ExternalLink } from "lucide-react";
import { Document, Page, pdfjs } from "react-pdf";
import "react-pdf/dist/Page/TextLayer.css";
import "react-pdf/dist/Page/AnnotationLayer.css";
import { api } from "../api";
import { prettyPdfName } from "../citations";
import { isTyping } from "../dom";
import { useStore } from "../store";

pdfjs.GlobalWorkerOptions.workerSrc = new URL("pdfjs-dist/build/pdf.worker.min.mjs", import.meta.url).toString();

const PDF_OPTIONS = {
  cMapUrl: "/pdfjs/cmaps/",
  cMapPacked: true,
  standardFontDataUrl: "/pdfjs/standard_fonts/",
  wasmUrl: "/pdfjs/wasm/",
};

export function PdfViewer() {
  const { viewer, closeViewer, docs } = useStore();
  const [page, setPage] = useState(1);
  const [numPages, setNumPages] = useState(0);
  const [zoom, setZoom] = useState(1);
  const [width, setWidth] = useState(560);
  const [error, setError] = useState<string | null>(null);
  const bodyRef = useRef<HTMLDivElement>(null);
  const highlightRef = useRef<HTMLDivElement>(null);

  const doc = docs.find((d) => d.id === viewer?.documentId);
  const fileUrl = viewer ? api.documentFileUrl(viewer.documentId) : null;
  // The backend knows the page count, so navigation works before pdf.js has finished loading.
  const total = numPages || doc?.num_pages || 0;
  const next = () => setPage((p) => (total ? Math.min(total, p + 1) : p + 1));
  const prev = () => setPage((p) => Math.max(1, p - 1));

  useEffect(() => {
    if (viewer) {
      setPage(viewer.page);
      setError(null);
    }
  }, [viewer]);

  useEffect(() => {
    const el = bodyRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(([entry]) => setWidth(Math.max(260, entry.contentRect.width - 32)));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!viewer || isTyping(e.target)) return;
      if (e.key === "Escape") closeViewer();
      if (e.key === "ArrowRight") next();
      if (e.key === "ArrowLeft") prev();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  const pageSize = doc?.page_sizes?.[page - 1];
  const renderWidth = Math.round(width * zoom);
  const scale = pageSize ? renderWidth / pageSize[0] : 1;
  const boxes = useMemo(
    () => (viewer && page === viewer.page ? viewer.bboxes.filter((b) => b.page === page) : []),
    [viewer, page],
  );

  useEffect(() => {
    highlightRef.current?.scrollIntoView?.({ block: "center", behavior: "smooth" });
  }, [boxes, viewer?.nonce]);

  if (!viewer || !fileUrl) return null;

  return (
    <aside className="viewer" aria-label="PDF viewer">
      <header className="viewer-head">
        <div className="viewer-title">
          <strong title={viewer.pdfName}>{prettyPdfName(viewer.pdfName)}</strong>
          {viewer.label && <span className="muted">{viewer.label}</span>}
        </div>
        <div className="viewer-tools">
          <button className="icon-btn" aria-label="Previous page" onClick={prev} disabled={page <= 1}>
            <ChevronLeft size={16} />
          </button>
          <span className="page-ind" data-testid="viewer-page">
            {page} / {total || "…"}
          </span>
          <button
            className="icon-btn"
            aria-label="Next page"
            onClick={next}
            disabled={!!total && page >= total}
          >
            <ChevronRight size={16} />
          </button>
          <span className="divider" />
          <button className="icon-btn" aria-label="Zoom out" onClick={() => setZoom((z) => Math.max(0.6, +(z - 0.15).toFixed(2)))}>
            <Minus size={16} />
          </button>
          <span className="page-ind">{Math.round(zoom * 100)}%</span>
          <button className="icon-btn" aria-label="Zoom in" onClick={() => setZoom((z) => Math.min(2.5, +(z + 0.15).toFixed(2)))}>
            <Plus size={16} />
          </button>
          <a className="icon-btn" href={`${fileUrl}#page=${page}`} target="_blank" rel="noreferrer" aria-label="Open PDF in new tab">
            <ExternalLink size={16} />
          </a>
          <button className="icon-btn" aria-label="Close viewer" onClick={closeViewer}>
            <X size={16} />
          </button>
        </div>
      </header>
      <div className="viewer-body" ref={bodyRef}>
        {error ? (
          <div className="viewer-error" role="alert">{error}</div>
        ) : (
          <Document
            file={fileUrl}
            options={PDF_OPTIONS}
            onLoadSuccess={({ numPages: n }) => setNumPages(n)}
            onLoadError={(e) => setError(`Could not load the PDF: ${e.message}`)}
            loading={<div className="viewer-loading">Loading PDF…</div>}
          >
            <div className="page-wrap" style={{ width: renderWidth }}>
              <Page pageNumber={page} width={renderWidth} renderAnnotationLayer={false} />
              {boxes.map((b, i) => (
                <div
                  key={`${viewer.nonce}-${i}`}
                  ref={i === 0 ? highlightRef : undefined}
                  className="highlight"
                  data-testid="pdf-highlight"
                  style={{
                    left: b.x0 * scale - 4,
                    top: b.y0 * scale - 3,
                    width: (b.x1 - b.x0) * scale + 8,
                    height: (b.y1 - b.y0) * scale + 6,
                  }}
                />
              ))}
            </div>
          </Document>
        )}
      </div>
    </aside>
  );
}
