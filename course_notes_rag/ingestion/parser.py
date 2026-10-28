"""Layout-aware PDF parsing into Markdown blocks with page numbers and bounding boxes.

Two engines share one output format (:class:`ParsedDocument`):

* :class:`DoclingParser` (default): layout model, table structure, OCR for scanned or
  handwritten pages, and formula-to-LaTeX enrichment.
* :class:`PyMuPDFParser` (fast fallback): font-size heading detection, ``find_tables``
  tables, Unicode-math-to-LaTeX conversion. No OCR.
"""

from __future__ import annotations

import logging
import re
import threading
from collections import Counter
from pathlib import Path
from typing import Protocol

from course_notes_rag.models import BBox, Block, ParsedDocument

log = logging.getLogger(__name__)


class ParseError(RuntimeError):
    """The file could not be parsed as a PDF."""


class DocumentParser(Protocol):
    name: str

    def parse(self, path: Path, *, pdf_name: str | None = None) -> ParsedDocument: ...


# --- Shared helpers ------------------------------------------------------------------

_NUMBERED = re.compile(r"^(\d+(?:\.\d+)*)[.)]?\s+\S")
_LECTURE = re.compile(r"^(lecture|chapter|week|unit|module)\s+\d+", re.IGNORECASE)
_BULLET = re.compile(r"^\s*(?:[•●▪◦‣∙·\-–*]|\(?\d{1,2}[.)]|\(?[a-z][.)])\s+")

UNICODE_TO_LATEX = {
    "α": r"\alpha", "β": r"\beta", "γ": r"\gamma", "δ": r"\delta", "ε": r"\epsilon",
    "ϵ": r"\epsilon", "ζ": r"\zeta", "η": r"\eta", "θ": r"\theta", "κ": r"\kappa",
    "λ": r"\lambda", "μ": r"\mu", "ν": r"\nu", "ξ": r"\xi", "π": r"\pi", "ρ": r"\rho",
    "σ": r"\sigma", "τ": r"\tau", "φ": r"\phi", "ϕ": r"\phi", "χ": r"\chi", "ψ": r"\psi",
    "ω": r"\omega", "Γ": r"\Gamma", "Δ": r"\Delta", "Θ": r"\Theta", "Λ": r"\Lambda",
    "Σ": r"\Sigma", "Φ": r"\Phi", "Ψ": r"\Psi", "Ω": r"\Omega", "∇": r"\nabla", "∑": r"\sum",
    "∏": r"\prod", "∫": r"\int", "∂": r"\partial", "≤": r"\leq", "≥": r"\geq", "≠": r"\neq",
    "≈": r"\approx", "∞": r"\infty", "→": r"\to", "⇒": r"\Rightarrow", "⇔": r"\Leftrightarrow",
    "∈": r"\in", "∉": r"\notin", "⊂": r"\subset", "⊆": r"\subseteq", "∀": r"\forall",
    "∃": r"\exists", "±": r"\pm", "×": r"\times", "·": r"\cdot", "√": r"\sqrt", "‖": r"\|",
    "⊤": r"^{\top}", "∘": r"\circ", "⊙": r"\odot", "−": "-", "∗": "*",
}
_MATH_CHARS = set(UNICODE_TO_LATEX) | set("=+^_<>|")
MATH_FONT_HINTS = ("cmmi", "cmsy", "cmex", "cmr", "math", "stix", "symbol", "msbm", "esint")


SUPERSCRIPTS = dict(zip("⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁱⁿᵀᵈᵏˣʸᵗ", "0123456789+-=()inTdkxyt"))
SUBSCRIPTS = dict(zip("₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎ₐₑₒₓₕₖₗₘₙₚₛₜᵢⱼ", "0123456789+-=()aeoxhklmnpstij"))
COMBINING = {"̂": r"\hat", "̃": r"\tilde", "̄": r"\bar", "̇": r"\dot", "⃗": r"\vec"}


def unicode_math_to_latex(text: str) -> str:
    """Rewrite Unicode math (Greek, operators, ⁽ⁱ⁾ scripts, combining accents) as KaTeX-safe LaTeX."""
    out: list[str] = []
    i = 0
    while i < len(text):
        ch = text[i]
        for table, op in ((SUPERSCRIPTS, "^"), (SUBSCRIPTS, "_")):
            if ch in table:
                j = i
                while j < len(text) and text[j] in table:
                    j += 1
                out.append(f"{op}{{{''.join(table[c] for c in text[i:j])}}}")
                i = j
                break
        else:
            if ch in COMBINING and out:
                out[-1] = f"{COMBINING[ch]}{{{out[-1].strip()}}}"
            elif ch == "…":
                out.append(r"\ldots ")
            else:
                rep = UNICODE_TO_LATEX.get(ch)
                out.append(ch if rep is None else rep + (" " if rep[-1].isalpha() else ""))
            i += 1
    latex = "".join(out)
    latex = latex.replace("^{\\top}", "^{T}")
    latex = re.sub(r"(?<![\\A-Za-z])(tanh|exp|log|sin|cos|max|min|det|arg)(?![A-Za-z])", r"\\\1", latex)
    latex = re.sub(r"(?<![\\A-Za-z])(Var|Cov|softmax|diag)(?![A-Za-z])", r"\\operatorname{\1}", latex)
    return re.sub(r"[ \t]+", " ", latex).strip()


_RELATIONS = set("=≤≥<>≈≠∝∈⊂⊆→⇒⇔")
_MATH_WORDS = {"tanh", "diag", "softmax", "sigmoid", "relu", "argmax", "argmin", "trace", "subject"}


def looks_like_formula(text: str, fonts: set[str]) -> bool:
    """A short line dominated by symbols with a relation (=, ≤, ...) or set in a math font."""
    stripped = text.strip()
    if not stripped or len(stripped) > 300:
        return False
    words = [w for w in re.findall(r"[A-Za-z]{4,}", stripped) if w.lower() not in _MATH_WORDS]
    if len(words) > 3:
        return False
    font_math = any(h in f.lower() for f in fonts for h in MATH_FONT_HINTS)
    has_relation = any(ch in _RELATIONS for ch in stripped)
    math_chars = sum(ch in _MATH_CHARS for ch in stripped)
    return has_relation and (font_math or math_chars >= 2 or len(words) <= 1)


def infer_heading_level(text: str, fallback: int) -> int:
    if _LECTURE.match(text):
        return 1
    m = _NUMBERED.match(text)
    if m:
        return min(6, 1 + m.group(1).count(".") + 1)
    return max(1, min(6, fallback))


def detect_slides(page_sizes: list[tuple[float, float]]) -> bool:
    if not page_sizes:
        return False
    landscape = sum(1 for w, h in page_sizes if w > h * 1.15)
    return landscape / len(page_sizes) >= 0.6


def _open_pdf(path: Path):
    import pymupdf

    if not path.exists():
        raise ParseError(f"File not found: {path.name}")
    try:
        doc = pymupdf.open(path)
    except Exception as exc:  # noqa: BLE001 - PyMuPDF raises several types
        raise ParseError(f"{path.name} is not a readable PDF: {exc}") from exc
    if not doc.is_pdf:
        doc.close()
        raise ParseError(f"{path.name} is not a PDF")
    if doc.needs_pass:
        doc.close()
        raise ParseError(f"{path.name} is password protected")
    return doc


# --- PyMuPDF engine ------------------------------------------------------------------


class PyMuPDFParser:
    """Fast engine. Pages without a text layer (scans) are read with RapidOCR when available."""

    name = "pymupdf"

    def __init__(self, *, ocr: bool = True, ocr_dpi: int = 150) -> None:
        self.ocr = ocr
        self.ocr_dpi = ocr_dpi
        self._ocr_engine = None
        self._lock = threading.Lock()

    def parse(self, path: Path, *, pdf_name: str | None = None) -> ParsedDocument:
        doc = _open_pdf(Path(path))
        try:
            return self._parse(doc, pdf_name or Path(path).name)
        finally:
            doc.close()

    def _parse(self, doc, pdf_name: str) -> ParsedDocument:
        page_sizes = [(float(p.rect.width), float(p.rect.height)) for p in doc]
        raw: list[tuple[int, dict, list[dict], str]] = []
        tables: dict[int, list[tuple[BBox, str]]] = {}
        ocr_blocks: list[Block] = []
        sizes: Counter[float] = Counter()
        for pno, page in enumerate(doc, start=1):
            tables[pno] = self._tables(page, pno)
            data = page.get_text("dict", sort=True)
            text_blocks = []
            for block in data.get("blocks", []):
                if block.get("type") != 0:
                    continue
                spans = [s for line in block.get("lines", []) for s in line.get("spans", [])]
                if not spans or not "".join(s["text"] for s in spans).strip():
                    continue
                text_blocks.append((block, spans))
            if not text_blocks:
                if self.ocr and page.get_images():
                    ocr_blocks.extend(self._ocr_page(page, pno))
                continue
            for block, spans in text_blocks:
                text = _block_text(block)
                fonts = {sp.get("font", "") for sp in spans}
                kind = "formula" if looks_like_formula(text, fonts) else "prose"
                if kind == "prose":
                    for sp in spans:
                        sizes[round(sp["size"], 1)] += len(sp["text"].strip())
                raw.append((pno, block, spans, kind))
        body = max(sizes, key=sizes.get) if sizes else 10.0
        heading_sizes = sorted({sz for sz in sizes if sz >= body * 1.15}, reverse=True)

        blocks: list[Block] = list(ocr_blocks)
        inserted_tables: set[tuple[int, int]] = set()
        for pno, block, spans, kind in raw:
            x0, y0, x1, y1 = block["bbox"]
            bbox = BBox(pno, x0, y0, x1, y1)
            for t_idx, (tb, md) in enumerate(tables.get(pno, [])):
                if _inside(bbox, tb):
                    if (pno, t_idx) not in inserted_tables:
                        inserted_tables.add((pno, t_idx))
                        blocks.append(Block("table", md, pno, tb))
                    break
            else:
                if kind == "formula":
                    latex = unicode_math_to_latex(_block_text(block))
                    blocks.append(Block("formula", latex, pno, bbox))
                else:
                    blocks.extend(self._text_blocks(pno, block, spans, heading_sizes))
        # Tables with no overlapping text block (rare) still get emitted.
        for pno, page_tables in tables.items():
            for t_idx, (tb, md) in enumerate(page_tables):
                if (pno, t_idx) not in inserted_tables:
                    blocks.append(Block("table", md, pno, tb))
        blocks.sort(key=lambda b: (b.page, b.bbox.y0 if b.bbox else 0.0))
        title = ((doc.metadata or {}).get("title") or "").strip() or next(
            (b.text for b in blocks if b.is_heading and b.level == 1), None
        )
        if title and title.lower() == "untitled":
            title = None
        return ParsedDocument(
            pdf_name=pdf_name,
            num_pages=len(page_sizes),
            page_sizes=page_sizes,
            blocks=blocks,
            title=title,
            is_slides=detect_slides(page_sizes),
            engine=self.name,
        )

    @staticmethod
    def _tables(page, pno: int) -> list[tuple[BBox, str]]:
        found = []
        try:
            for tab in page.find_tables().tables:
                md = tab.to_markdown(clean=False).strip().replace("<br>", " ")
                if md:
                    x0, y0, x1, y1 = tab.bbox
                    found.append((BBox(pno, x0, y0, x1, y1), md))
        except Exception:  # noqa: BLE001 - table detection is best effort
            log.debug("table detection failed on page %s", pno)
        return found

    def _text_blocks(
        self, pno: int, block: dict, spans: list[dict], heading_sizes: list[float]
    ) -> list[Block]:
        x0, y0, x1, y1 = block["bbox"]
        bbox = BBox(pno, x0, y0, x1, y1)
        lines = _block_lines(block)
        text = " ".join(lines)
        size = max(round(sp["size"], 1) for sp in spans)
        bold = all((sp.get("flags", 0) & 16) or "bold" in sp.get("font", "").lower() for sp in spans)
        if size in heading_sizes and len(text) <= 160:
            level = heading_sizes.index(size) + 1
            return [Block("heading", text, pno, bbox, infer_heading_level(text, level))]
        if bold and len(text) <= 90 and len(lines) == 1 and not text.endswith((".", ":")):
            level = infer_heading_level(text, len(heading_sizes) + 1)
            return [Block("heading", text, pno, bbox, level)]
        if len(lines) > 1 and all(_BULLET.match(ln) for ln in lines):
            return [Block("list_item", _BULLET.sub("", ln), pno, bbox) for ln in lines]
        if _BULLET.match(text):
            return [Block("list_item", _BULLET.sub("", text), pno, bbox)]
        return [Block("text", text, pno, bbox)]

    # -- OCR for scanned / handwritten pages --

    def _get_ocr(self):
        with self._lock:
            if self._ocr_engine is None:
                from rapidocr import RapidOCR

                self._ocr_engine = RapidOCR()
            return self._ocr_engine

    def _ocr_page(self, page, pno: int) -> list[Block]:
        try:
            import numpy as np

            engine = self._get_ocr()
        except ImportError:
            log.warning("RapidOCR not installed; scanned page %s skipped", pno)
            return []
        pix = page.get_pixmap(dpi=self.ocr_dpi)
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
        result = engine(np.ascontiguousarray(img[:, :, :3]))
        if result is None or result.txts is None:
            return []
        scale = 72.0 / self.ocr_dpi
        lines = []
        for box, text, score in zip(result.boxes, result.txts, result.scores):
            if float(score) < 0.6 or not text.strip():
                continue
            xs = [float(pt[0]) * scale for pt in box]
            ys = [float(pt[1]) * scale for pt in box]
            lines.append((BBox(pno, min(xs), min(ys), max(xs), max(ys)), text.strip()))
        return group_ocr_lines(lines, pno)


def _block_lines(block: dict) -> list[str]:
    lines = [
        "".join(sp["text"] for sp in line.get("spans", [])).strip()
        for line in block.get("lines", [])
    ]
    return [re.sub(r"(\w)- (\w)", r"\1\2", ln) for ln in lines if ln]


def _block_text(block: dict) -> str:
    return " ".join(_block_lines(block))


def group_ocr_lines(lines: list[tuple[BBox, str]], pno: int) -> list[Block]:
    """Join OCR lines into paragraphs; a markedly taller first line becomes the heading."""
    if not lines:
        return []
    lines = sorted(lines, key=lambda item: item[0].y0)
    heights = sorted(b.y1 - b.y0 for b, _ in lines)
    median = heights[len(heights) // 2]
    blocks: list[Block] = []
    first_box, first_text = lines[0]
    start = 0
    if len(lines) > 1 and (first_box.y1 - first_box.y0) > 1.15 * median:
        blocks.append(Block("heading", first_text, pno, first_box, 2))
        start = 1
    para: list[tuple[BBox, str]] = []

    def flush() -> None:
        if para:
            box = BBox(pno, min(b.x0 for b, _ in para), min(b.y0 for b, _ in para),
                       max(b.x1 for b, _ in para), max(b.y1 for b, _ in para))
            blocks.append(Block("text", " ".join(t for _, t in para), pno, box))
            para.clear()

    for box, text in lines[start:]:
        if para and para[-1][1].rstrip().endswith((".", "!", "?", ":")):
            flush()
        para.append((box, text))
    flush()
    return blocks


def _inside(inner: BBox, outer: BBox, tol: float = 2.0) -> bool:
    cx = (inner.x0 + inner.x1) / 2
    cy = (inner.y0 + inner.y1) / 2
    return outer.x0 - tol <= cx <= outer.x1 + tol and outer.y0 - tol <= cy <= outer.y1 + tol


# --- Docling engine ------------------------------------------------------------------


class DoclingParser:
    name = "docling"

    def __init__(self, *, formula_enrichment: bool = True, ocr: bool = True, device: str = "auto") -> None:
        self.formula_enrichment = formula_enrichment
        self.ocr = ocr
        self.device = device
        self._converter = None
        self._lock = threading.Lock()

    def _get_converter(self):
        with self._lock:
            if self._converter is None:
                self._converter = _build_docling_converter(
                    formula_enrichment=self.formula_enrichment, ocr=self.ocr, device=self.device
                )
            return self._converter

    def parse(self, path: Path, *, pdf_name: str | None = None) -> ParsedDocument:
        path = Path(path)
        pdf = _open_pdf(path)  # fail fast with a clear message on broken files
        meta_title = ((pdf.metadata or {}).get("title") or "").strip()
        pdf.close()
        converter = self._get_converter()
        try:
            result = converter.convert(path, raises_on_error=True)
        except Exception as exc:  # noqa: BLE001
            raise ParseError(f"Docling could not convert {path.name}: {exc}") from exc
        parsed = docling_to_parsed(result.document, pdf_name or path.name, engine=self.name)
        if not parsed.title and meta_title and meta_title.lower() != "untitled":
            parsed.title = meta_title
        return parsed


def _build_docling_converter(*, formula_enrichment: bool, ocr: bool, device: str):
    from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    opts = PdfPipelineOptions()
    opts.do_ocr = ocr
    opts.do_table_structure = True
    opts.do_formula_enrichment = formula_enrichment
    opts.generate_page_images = False
    opts.accelerator_options = AcceleratorOptions(
        device={"cuda": AcceleratorDevice.CUDA, "cpu": AcceleratorDevice.CPU}.get(
            device, AcceleratorDevice.AUTO
        )
    )
    return DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
    )


def docling_to_parsed(ddoc, pdf_name: str, *, engine: str = "docling") -> ParsedDocument:
    from docling_core.types.doc import DocItemLabel, SectionHeaderItem, TableItem, TextItem

    page_sizes: list[tuple[float, float]] = []
    for pno in sorted(ddoc.pages):
        size = ddoc.pages[pno].size
        page_sizes.append((float(size.width), float(size.height)))
    heights = {pno: float(ddoc.pages[pno].size.height) for pno in ddoc.pages}

    has_title = any(
        getattr(item, "label", None) == DocItemLabel.TITLE for item, _ in ddoc.iterate_items()
    )
    blocks: list[Block] = []
    title: str | None = None
    skip = {DocItemLabel.PAGE_HEADER, DocItemLabel.PAGE_FOOTER, DocItemLabel.PICTURE,
            DocItemLabel.CHART, DocItemLabel.DOCUMENT_INDEX}
    for item, _depth in ddoc.iterate_items():
        label = getattr(item, "label", None)
        if label in skip or not getattr(item, "prov", None):
            continue
        prov = item.prov[0]
        pno = int(prov.page_no)
        box = prov.bbox.to_top_left_origin(page_height=heights.get(pno, 0.0))
        bbox = BBox(pno, float(box.l), float(box.t), float(box.r), float(box.b))
        if isinstance(item, TableItem):
            md = item.export_to_markdown(doc=ddoc).strip()
            if md:
                blocks.append(Block("table", md, pno, bbox))
            continue
        if not isinstance(item, TextItem):
            continue
        text = (item.text or "").strip()
        if label == DocItemLabel.FORMULA:
            latex = text or (getattr(item, "orig", "") or "").strip()
            if latex:
                blocks.append(Block("formula", latex, pno, bbox))
            continue
        if not text:
            continue
        if label == DocItemLabel.TITLE:
            title = title or text
            blocks.append(Block("title", text, pno, bbox, level=1))
        elif isinstance(item, SectionHeaderItem) or label == DocItemLabel.SECTION_HEADER:
            base = int(getattr(item, "level", 1) or 1) + (1 if has_title else 0)
            blocks.append(Block("heading", text, pno, bbox, infer_heading_level(text, base)))
        elif label == DocItemLabel.LIST_ITEM:
            blocks.append(Block("list_item", _BULLET.sub("", text), pno, bbox))
        elif label == DocItemLabel.CODE:
            blocks.append(Block("code", text, pno, bbox))
        elif label == DocItemLabel.CAPTION:
            blocks.append(Block("caption", text, pno, bbox))
        else:
            blocks.append(Block("text", text, pno, bbox))
    return ParsedDocument(
        pdf_name=pdf_name,
        num_pages=len(page_sizes),
        page_sizes=page_sizes,
        blocks=blocks,
        title=title,
        is_slides=detect_slides(page_sizes),
        engine=engine,
    )


# --- Engine selection ------------------------------------------------------------------


class AutoParser:
    """Docling first; PyMuPDF if Docling is missing or fails on a file."""

    def __init__(self, primary: DocumentParser, fallback: DocumentParser) -> None:
        self.primary = primary
        self.fallback = fallback
        self.name = f"{primary.name}+{fallback.name}"

    def parse(self, path: Path, *, pdf_name: str | None = None) -> ParsedDocument:
        try:
            return self.primary.parse(path, pdf_name=pdf_name)
        except ImportError:
            log.warning("Docling not installed; using PyMuPDF")
        except ParseError as exc:
            if "not a readable PDF" in str(exc) or "not a PDF" in str(exc) or "password" in str(exc):
                raise
            log.warning("primary parser failed (%s); using PyMuPDF", exc)
        doc = self.fallback.parse(path, pdf_name=pdf_name)
        doc.engine = f"{self.fallback.name} (fallback)"
        return doc


def build_parser(engine: str, *, formula_enrichment: bool = True, ocr: bool = True,
                 device: str = "auto") -> DocumentParser:
    if engine == "pymupdf":
        return PyMuPDFParser(ocr=ocr)
    docling = DoclingParser(formula_enrichment=formula_enrichment, ocr=ocr, device=device)
    if engine == "docling":
        return docling
    return AutoParser(docling, PyMuPDFParser(ocr=ocr))


__all__ = [
    "AutoParser", "DoclingParser", "DocumentParser", "ParseError", "PyMuPDFParser",
    "build_parser", "detect_slides", "docling_to_parsed", "group_ocr_lines", "infer_heading_level",
    "looks_like_formula", "unicode_math_to_latex",
]
