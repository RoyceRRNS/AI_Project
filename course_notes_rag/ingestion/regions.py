"""Render one area of a PDF page as a PNG, e.g. a formula exactly as the notes show it.

Parsed formulas can be misread, especially in scans and handwriting, so flashcards also show the
original. Areas use the same coordinates as parsed boxes: points from the top-left corner of the
page as displayed.
"""

from __future__ import annotations

import math
from pathlib import Path

REGION_DPI = 200
REGION_MARGIN = 4.0  # points around the area, so strokes on its edge are not cut off
MAX_REGION_PIXELS = 4_000_000
MIN_REGION_DPI = 36


class RegionError(ValueError):
    """The requested area is not a drawable part of the document."""


def render_region(path: Path, page: int, x0: float, y0: float, x1: float, y1: float) -> bytes:
    import pymupdf

    if not all(math.isfinite(v) for v in (x0, y0, x1, y1)) or x1 <= x0 or y1 <= y0:
        raise RegionError("The area must have a positive width and height")
    try:
        doc = pymupdf.open(path)
    except Exception as exc:  # noqa: BLE001 - PyMuPDF raises several types
        raise RegionError(f"{path.name} is not a readable PDF") from exc
    with doc:
        if not 1 <= page <= doc.page_count:
            raise RegionError(f"Page {page} is not in this document")
        pdf_page = doc[page - 1]
        margin = REGION_MARGIN
        clip = pymupdf.Rect(x0 - margin, y0 - margin, x1 + margin, y1 + margin) & pdf_page.rect
        if clip.is_empty:
            raise RegionError("The area is outside the page")
        # A large area is drawn at a lower resolution rather than as a huge image.
        dpi = min(REGION_DPI, int(72 * math.sqrt(MAX_REGION_PIXELS / clip.get_area())))
        return pdf_page.get_pixmap(clip=clip, dpi=max(dpi, MIN_REGION_DPI)).tobytes("png")
