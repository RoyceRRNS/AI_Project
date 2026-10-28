"""Render a PDF's pages to PNG for visual review:  rasterize.py <file.pdf> <out_prefix> [dpi]"""

import sys

import pymupdf

pdf, prefix = sys.argv[1], sys.argv[2]
dpi = int(sys.argv[3]) if len(sys.argv) > 3 else 100
doc = pymupdf.open(pdf)
for i, page in enumerate(doc):
    page.get_pixmap(dpi=dpi).save(f"{prefix}-{i + 1:02d}.png")
print(f"{len(doc)} pages -> {prefix}-NN.png")
